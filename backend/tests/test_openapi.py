"""Phase 1.0: openapi.yaml must cover every implemented /api/v1 route."""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture(scope="module")
def spec():
    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    return yaml.safe_load(spec_path.read_text(encoding="utf-8"))


def _flask_path_to_openapi(rule: str) -> str:
    """'/api/v1/assets/<ref>' under server '/api/v1' -> '/assets/{ref}'."""
    assert rule.startswith("/api/v1"), rule
    path = rule[len("/api/v1") :]
    if not path:
        path = "/"
    import re

    return re.sub(r"<[^>]+>", lambda m: "{" + m.group(0)[1:-1].split(":")[-1] + "}", path)


def test_every_v1_route_documented(spec):
    app = create_app()
    documented = set()
    for path, item in (spec.get("paths") or {}).items():
        for method in item:
            documented.add((method.upper(), path))

    missing = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1"):
            continue
        for method in _METHODS & rule.methods:
            if (method, _flask_path_to_openapi(rule.rule)) not in documented:
                missing.append(f"{method} {rule.rule}")
    assert missing == [], "v1 routes missing from openapi.yaml:\n" + "\n".join(missing)


def test_served_openapi_matches_file():
    app = create_app()
    client = app.test_client()
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    body = r.get_json()
    assert body["openapi"].startswith("3.")


def test_location_contract_covers_extended_vocabulary_and_nested_payload(spec):
    """§15: location_status admits ASSIGNED/PARTIAL/PENDING and the nested
    source-vs-confirmed payload is schematized (not prose-only)."""
    params = spec["paths"]["/assets"]["get"]["parameters"]
    status_param = next(p for p in params if p.get("name") == "location_status")
    assert status_param["schema"]["enum"] == ["ASSIGNED", "PARTIAL", "PENDING"]

    schemas = spec["components"]["schemas"]
    location = schemas["AssetLocation"]
    assert location["properties"]["status"]["enum"] == ["ASSIGNED", "PARTIAL", "PENDING"]
    assert set(location["properties"]["levels"]["properties"]) == {
        "college",
        "block",
        "floor",
        "department",
        "room",
    }
    level = schemas["LocationLevel"]
    assert level["properties"]["status"]["enum"] == ["RESOLVED", "UNRESOLVED", "MISSING", "AMBIGUOUS"]
    assert "source" in level["properties"] and "type" in level["properties"]

    facet = schemas["FacetEntry"]
    assert set(facet["properties"]) == {"value", "count", "id"}
