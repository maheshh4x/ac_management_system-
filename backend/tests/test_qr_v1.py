"""Phase 6 — QR codes, scanning & public asset page.

Plan Verify coverage:
  * foreign-host payloads rejected (FOREIGN_HOST) before any lookup
  * unknown ref -> 404 (resolve and the public landing)
  * public view carries no serial / technician / remarks / attributes
    (exact key set + raw-value scan)
  * generated PNG decodes (zxing-cpp) to the stable
    {FRONTEND_URL}/a/{asset_uuid} URL — never the internal ac_id
  * regenerate bumps qr_version, audits, and the payload stays byte-identical
    so stickers already printed keep resolving
  * batch renders a real A4 PDF (3x8 labels per page; 25 -> 2 pages) and is
    SUPER_ADMIN / FACILITY_MANAGER only (EXPORT_DATA)

pyzbar from the plan would need the system zbar library; zxing-cpp ships a
self-contained wheel, so the decoder is zxing-cpp (see requirements-dev.txt).
"""

import io
import re
import sys
import uuid
from pathlib import Path

import pytest
import zxingcpp
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services import qr_service  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, TEST_USER_ID, AuthClient, make_token  # noqa: E402

QR_URL = "/api/v1/assets"
RESOLVE = "/api/v1/qr/resolve"
PUBLIC = "/api/v1/public/assets"
BATCH = "/api/v1/assets/qr/batch"
AC_PREFIX = "QRT-"
BATCH_PREFIX = "QRB-"

# The exact field set the Phase 6 plan allows on the public landing page,
# plus the DERIVED visual_id (this task: label-consistent human identifier —
# derived, never stored, exposes nothing the location labels don't already).
PUBLIC_FIELDS = {
    "asset_code",
    "make",
    "model",
    "type",
    "capacity",
    "block",
    "floor",
    "room",
    "operational_status",
    "last_maintenance_date",
    "visual_id",
}


@pytest.fixture()
def app(db, monkeypatch):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """App under test (JWT pinned to the suite secret)."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def anon(app):
    """Unauthenticated client (public landing + auth-required tripwires)."""
    return app.test_client()


@pytest.fixture()
def admin(app):
    return AuthClient(app.test_client(), make_token("SUPER_ADMIN"))


@pytest.fixture()
def tech(app):
    # Fresh sub each test: no profile row -> the JWT claim stays authoritative.
    return AuthClient(app.test_client(), make_token("TECHNICIAN", sub=str(uuid.uuid4())))


@pytest.fixture()
def viewer(app):
    return AuthClient(app.test_client(), make_token("VIEWER", sub=str(uuid.uuid4())))


@pytest.fixture()
def audit_profile(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Short-lived SUPER_ADMIN profile for TEST_USER_ID so the audit writer's
    user FK resolves inside the regenerate test; removed afterwards (a
    lingering profile would flip role-matrix tests that rely on claim-only
    tokens — see tests/test_audit.py)."""
    created = False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM profiles WHERE id = %s", (TEST_USER_ID,))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active) "
                    "VALUES (%s, 'tester@acms.test', 'Test User', 'SUPER_ADMIN', 'APPROVED', TRUE)",
                    (TEST_USER_ID,),
                )
                created = True
        conn.commit()
    finally:
        conn.close()
    yield
    if created:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM audit_logs WHERE user_id = %s AND entity_id LIKE %s",
                    (TEST_USER_ID, f"{AC_PREFIX}%"),
                )
                cur.execute("DELETE FROM profiles WHERE id = %s", (TEST_USER_ID,))
            conn.commit()
        finally:
            conn.close()


@pytest.fixture()
def seeds(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Two uniquely tagged assets. a1 carries the full location chain plus
    values the public view must NEVER expose (serial, technician, remarks)."""
    tag = uuid.uuid4().hex[:8]
    ids = {"a1": f"{AC_PREFIX}{tag}-1", "a2": f"{AC_PREFIX}{tag}-2", "code": f"QRCODE-{tag}"}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, asset_code, ac_make_company, ac_model_number,"
                " ac_serial_number, ac_capacity_ton, ac_type, room_id,"
                " operational_status, last_maintenance_date, lifecycle_status, is_active,"
                " technician, remarks)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    ids["a1"],
                    ids["code"],
                    "QRT Maker",
                    "QRT-18K",
                    "SN-QRT-SECRET",
                    2.5,
                    "Split",
                    "ROOM-102",
                    "WORKING",
                    "2026-01-05",
                    "ACTIVE",
                    True,
                    "T. Echnician",
                    "internal remarks stay private",
                ),
            )
            cur.execute(
                "INSERT INTO acs (ac_id, asset_code, ac_type, room_id, is_active, lifecycle_status)"
                " VALUES (%s, %s, 'Window', NULL, TRUE, 'ACTIVE')",
                (ids["a2"], f"{ids['code']}-B"),
            )
            cur.execute("SELECT asset_uuid FROM acs WHERE ac_id = %s", (ids["a1"],))
            ids["uuid"] = str(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    yield ids
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE entity_id LIKE %s", (f"{AC_PREFIX}{tag}%",))
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"{AC_PREFIX}{tag}%",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def batch_seeds(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """25 tagged assets: 25 / 24 = two A4 sheets for the batch PDF."""
    tag = uuid.uuid4().hex[:8]
    ac_ids = [f"{BATCH_PREFIX}{tag}-{i:02d}" for i in range(25)]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for ac_id in ac_ids:
                cur.execute(
                    "INSERT INTO acs (ac_id, asset_code, room_id, is_active, lifecycle_status)"
                    " VALUES (%s, %s, 'ROOM-102', TRUE, 'ACTIVE')",
                    (ac_id, ac_id),
                )
        conn.commit()
    finally:
        conn.close()
    yield ac_ids
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE entity_id LIKE %s", (f"{BATCH_PREFIX}{tag}%",))
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"{BATCH_PREFIX}{tag}%",))
        conn.commit()
    finally:
        conn.close()


# ── helpers ──────────────────────────────────────────────────────────────────


def _decode_png(png: bytes) -> str:
    result = zxingcpp.read_barcode(Image.open(io.BytesIO(png)))
    assert result is not None, "generated QR PNG did not decode"
    return result.text


def _expected_payload(seeds) -> str:
    return f"{qr_service.frontend_base_url()}/a/{seeds['uuid']}"


def _audit_count(ac_id: str, action: str) -> int:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE entity_id = %s AND action = %s",
                (ac_id, action),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


# ── QR image / payload ───────────────────────────────────────────────────────


def test_png_decodes_to_stable_uuid_url(admin, seeds):
    r = admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=png")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("image/png")
    decoded = _decode_png(r.data)
    assert decoded == _expected_payload(seeds)
    # Stable identity is the UUID — the internal ac_id never appears.
    assert seeds["a1"] not in decoded


def test_svg_and_payload_formats(admin, seeds):
    r = admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=svg")
    assert r.status_code == 200
    assert "image/svg+xml" in r.headers["Content-Type"]
    assert b"<svg" in r.data

    r = admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=payload")
    assert r.status_code == 200
    assert r.get_json()["data"]["payload"] == _expected_payload(seeds)


def test_png_size_param(admin, seeds):
    r = admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=png&size=256")
    assert r.status_code == 200
    width, _height = Image.open(io.BytesIO(r.data)).size
    # Rounded down to whole modules: never above the request, never tiny.
    assert 200 <= width <= 256, width

    r = admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=png&size=big")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "INVALID_SIZE"


def test_qr_image_requires_auth(anon, seeds):
    r = anon.get(f"{QR_URL}/{seeds['a1']}/qr")
    assert r.status_code in (401, 403)


def test_qr_unknown_ref_404(admin):
    r = admin.get(f"{QR_URL}/NO-SUCH-{uuid.uuid4().hex[:8]}/qr")
    assert r.status_code == 404


# ── POST /qr/resolve ─────────────────────────────────────────────────────────


def test_resolve_roundtrip_and_bare_ref(tech, seeds):
    r = tech.post(RESOLVE, json={"payload": _expected_payload(seeds)})
    assert r.status_code == 200
    assert r.get_json()["data"]["asset_uuid"] == seeds["uuid"]

    r = tech.post(RESOLVE, json={"payload": seeds["code"]})
    assert r.status_code == 200
    assert r.get_json()["data"]["asset_uuid"] == seeds["uuid"]


def test_resolve_rejects_foreign_host(tech, seeds):
    foreign = f"https://evil.example.com/a/{seeds['uuid']}"
    r = tech.post(RESOLVE, json={"payload": foreign})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "FOREIGN_HOST"

    # Protocol-relative URL is a foreign host too.
    r = tech.post(RESOLVE, json={"payload": f"//evil.example.com/a/{seeds['uuid']}"})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "FOREIGN_HOST"


def test_resolve_unknown_ref_404(tech):
    r = tech.post(RESOLVE, json={"payload": qr_service.qr_payload(asset_uuid=str(uuid.uuid4()))})
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "ASSET_NOT_FOUND"

    r = tech.post(RESOLVE, json={"payload": f"NO-SUCH-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 404


def test_resolve_invalid_payload_400(tech):
    r = tech.post(RESOLVE, json={"payload": ""})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "INVALID_QR"


# ── public landing ───────────────────────────────────────────────────────────


def test_public_asset_exact_reduced_fields(anon, seeds):
    r = anon.get(f"{PUBLIC}/{seeds['uuid']}")
    assert r.status_code == 200
    data = r.get_json()["data"]

    # Exactly the plan's field list — nothing else may leak.
    assert set(data.keys()) == PUBLIC_FIELDS
    assert data["asset_code"] == seeds["code"]
    assert data["make"] == "QRT Maker"
    assert data["model"] == "QRT-18K"
    assert data["type"] == "Split"
    assert data["capacity"] == 2.5
    assert data["block"] == "Main Academic Block"
    assert data["floor"] == "Floor 1"
    assert data["room"] == "CSE Computer Lab 1"
    assert data["operational_status"] == "WORKING"
    assert data["last_maintenance_date"] == "2026-01-05"

    # Derived Visual Asset ID (label-consistent, human-facing): normalized
    # from the SAME effective location the page shows + the uuid suffix.
    visual = data["visual_id"]
    assert isinstance(visual, str) and visual
    assert "MA-1F" in visual  # "Main Academic Block" -> MA, "Floor 1" -> 1F
    assert "CCL1" in visual  # "CSE Computer Lab 1" -> CCL1
    assert visual.endswith(seeds["uuid"].replace("-", "")[-8:].upper())

    # Belt and braces: the forbidden values themselves must never appear.
    blob = str(data)
    for forbidden in ("SN-QRT-SECRET", "T. Echnician", "internal remarks stay private", "ac_serial"):
        assert forbidden not in blob

    assert r.headers["Cache-Control"] == "public, max-age=60"


def test_public_asset_resolves_code_and_404s(anon, seeds):
    r = anon.get(f"{PUBLIC}/{seeds['code']}")
    assert r.status_code == 200
    assert r.get_json()["data"]["asset_code"] == seeds["code"]

    r = anon.get(f"{PUBLIC}/NO-SUCH-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 404
    # App-wide default: errors stay uncachable even though 200s are public.
    assert r.headers["Cache-Control"] == "no-store"


# ── regenerate ───────────────────────────────────────────────────────────────


def test_regenerate_bumps_version_and_keeps_payload(audit_profile, admin, seeds):
    detail = admin.get(f"{QR_URL}/{seeds['a1']}").get_json()["data"]
    assert detail["qr_version"] == 1

    before = _decode_png(admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=png").data)
    before_count = _audit_count(seeds["a1"], "REGENERATE_QR")

    r = admin.post(f"{QR_URL}/{seeds['a1']}/qr/regenerate")
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["qr_version"] == 2
    assert body["payload"] == before  # payload is immutable by design

    # Old stickers keep working: the image still decodes to the same URL.
    after = _decode_png(admin.get(f"{QR_URL}/{seeds['a1']}/qr?format=png").data)
    assert after == before

    detail = admin.get(f"{QR_URL}/{seeds['a1']}").get_json()["data"]
    assert detail["qr_version"] == 2

    assert _audit_count(seeds["a1"], "REGENERATE_QR") == before_count + 1


def test_regenerate_forbidden_for_technician_and_viewer(tech, viewer, seeds):
    before = tech.get(f"{QR_URL}/{seeds['a1']}").get_json()["data"]["qr_version"]

    r = tech.post(f"{QR_URL}/{seeds['a1']}/qr/regenerate")
    assert r.status_code == 403
    r = viewer.post(f"{QR_URL}/{seeds['a1']}/qr/regenerate")
    assert r.status_code == 403

    after = tech.get(f"{QR_URL}/{seeds['a1']}").get_json()["data"]["qr_version"]
    assert after == before


def test_regenerate_unknown_ref_404(admin):
    r = admin.post(f"{QR_URL}/NO-SUCH-{uuid.uuid4().hex[:8]}/qr/regenerate")
    assert r.status_code == 404


# ── batch label sheet ────────────────────────────────────────────────────────


def _page_count(pdf: bytes) -> int:
    return len(re.findall(rb"/Type /Page(?!s)", pdf))


def test_batch_pdf_two_sheets_for_25_labels(admin, batch_seeds):
    ids_arg = ",".join(batch_seeds)
    r = admin.get(f"{BATCH}?ids={ids_arg}")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.data.startswith(b"%PDF")
    assert _page_count(r.data) == 2  # 25 labels at 3x8 = two A4 pages


def test_batch_without_ids_returns_all_active(admin, seeds):  # noqa: ARG001 - seeds guarantees active rows
    r = admin.get(BATCH)
    assert r.status_code == 200
    assert r.data.startswith(b"%PDF")


def test_batch_permission_matrix(admin, tech, viewer, batch_seeds):
    ids_arg = ",".join(batch_seeds[:2])
    assert tech.get(f"{BATCH}?ids={ids_arg}").status_code == 403
    assert viewer.get(f"{BATCH}?ids={ids_arg}").status_code == 403
    assert admin.get(f"{BATCH}?ids={ids_arg}").status_code == 200


def test_batch_unknown_ids_404(admin):
    r = admin.get(f"{BATCH}?ids=NO-SUCH-{uuid.uuid4().hex[:8]}")
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "NO_ASSETS"


def test_asset_edit_stays_backend_gated_for_viewer_and_anonymous(admin, viewer, anon, seeds):
    """The public/scanned surface never edits: a VIEWER PATCH is refused by
    the server (EDIT_ASSET), anonymous PATCH never authenticates — even
    though the frontend hides the button, the backend remains authoritative.
    """
    ref = seeds["a1"]
    before = seeds_snapshot(admin, ref)

    r = viewer.patch(f"{QR_URL}/{ref}", json={"remarks": "viewer write attempt"})
    assert r.status_code == 403, r.get_json()

    r = anon.patch(f"{QR_URL}/{ref}", json={"remarks": "anonymous write attempt"})
    assert r.status_code == 401, r.get_json()

    # Nothing changed server-side.
    assert seeds_snapshot(admin, ref) == before


def seeds_snapshot(admin, ref):
    """Minimal read-back proving the row is untouched (used above)."""
    r = admin.get(f"{QR_URL}/{ref}")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    return (data["remarks"], data["location_status"])
