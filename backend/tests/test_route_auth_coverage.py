"""Phase 1.2 tripwire: enumerate app.url_map and fail if ANY rule lacks an
auth enforcement path (marker from require_auth / require_permission /
legacy-guard mapping) without being on the explicit public allow-list.

Public allow-list (02-api-contract.md): health probes + the QR landing page.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.auth.legacy_guard import PUBLIC_PATH_PREFIXES, legacy_required_permission  # noqa: E402

_PUBLIC_RULES = {"/", "/health", "/ready", "/api/health", "/api/v1/openapi.json"}
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Flask's own static file route carries no business data.
_FRAMEWORK_RULES = {"/static/<path:filename>"}


@pytest.fixture(scope="module")
def app():
    return create_app()


def _methods(rule):
    return {m for m in rule.methods if m in _METHODS}


def test_every_route_has_auth_or_is_allowlisted(app):
    uncovered = []
    for rule in app.url_map.iter_rules():
        methods = _methods(rule)
        if not methods:
            continue
        path = rule.rule
        if path in _FRAMEWORK_RULES:
            continue
        if path in _PUBLIC_RULES or any(path == p or path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            continue
        view = app.view_functions[rule.endpoint]
        if path.startswith("/api/v1/"):
            if not getattr(view, "_requires_auth", False):
                uncovered.append(f"{sorted(methods)} {path} (v1 without require_auth)")
        else:
            missing = [m for m in methods if legacy_required_permission(path, m) is None]
            if missing:
                uncovered.append(f"{sorted(missing)} {path} (legacy without guard mapping)")
    assert uncovered == [], "Routes without auth coverage:\n" + "\n".join(uncovered)


def test_public_allowlist_is_exactly_the_qr_landing(app):
    """Only these paths may be anonymous, ever."""
    public = set()
    for rule in app.url_map.iter_rules():
        if not _methods(rule):
            continue
        path = rule.rule
        if path in _PUBLIC_RULES or any(path == p or path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            public.add(path)
            continue
        view = app.view_functions[rule.endpoint]
        # A v1 route missing the auth marker that ALSO isn't allow-listed is a
        # bug; the previous test catches it. Here we only collect true publics.
        if path.startswith("/api/v1/") and not getattr(view, "_requires_auth", False):
            public.add(path)
    assert public <= (_PUBLIC_RULES | {"/api/v1/public/assets/<ref>"})
