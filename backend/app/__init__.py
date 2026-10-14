"""Application factory — Phase 1 hardened.

Boot policy (fail closed, Phase 1.2): APP_ENV=production refuses to boot when
  * no JWT verification key is configured (SUPABASE_JWT_SECRET or
    SUPABASE_JWKS_URL / SUPABASE_URL),
  * debug mode is on (FLASK_DEBUG truthy), or
  * CORS_ORIGINS contains "*".

Anonymous allow-list (everything else requires a verified token, everywhere):
  /health, /ready, /api/v1/openapi.json, /api/v1/public/assets/<ref>, plus the
  trivial "/" info route. Legacy /api/* routes are centrally locked by the
  legacy guard (app.auth.legacy_guard); v1 routes carry require_auth +
  require_permission decorators.
"""

from __future__ import annotations

import logging
import os

from flask import Flask
from flask_cors import CORS

from app.auth.legacy_guard import register_legacy_guard
from app.extensions import init_limiter


def _app_env() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").lower()


def _assert_safe_boot(app: Flask) -> None:
    """Refuse to boot a production process in an unsafe configuration."""
    if _app_env() != "production":
        return
    from app.auth.jwt import auth_configured

    problems = []
    if not auth_configured():
        problems.append(
            "no JWT verification key (set SUPABASE_JWT_SECRET, or "
            "SUPABASE_JWKS_URL / SUPABASE_URL for asymmetric projects)"
        )
    if (os.getenv("FLASK_DEBUG") or "").lower() in {"1", "true", "yes"}:
        problems.append("debug mode is on (FLASK_DEBUG)")
    origins = os.getenv("CORS_ORIGINS", "")
    if "*" in [o.strip() for o in origins.split(",")]:
        problems.append("CORS_ORIGINS contains '*'")
    if problems:
        for p in problems:
            app.logger.critical("unsafe production configuration: %s", p)
        raise RuntimeError("Refusing to boot with unsafe production configuration: " + "; ".join(problems))


def _configure_logging() -> None:
    """Structured JSON logs (Phase 1.4) in production; plain text in dev."""
    level = os.getenv("LOG_LEVEL", "INFO")
    use_json = (os.getenv("LOG_FORMAT") or (_app_env() == "production" and "json") or "plain") == "json"
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    if use_json:
        from pythonjsonlogger import jsonlogger

        handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
                " %(request_id)s %(user_id)s %(route)s %(status)s %(duration_ms)s"
            )
        )
        handler.addFilter(_RequestContextFilter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


class _RequestContextFilter(logging.Filter):
    """Inject request-scoped fields into every JSON log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        from flask import g, has_request_context, request

        record.request_id = getattr(g, "request_id", "-") if has_request_context() else "-"
        user = getattr(g, "current_user", None) if has_request_context() else None
        record.user_id = (user or {}).get("id", "-") if isinstance(user, dict) else "-"
        record.route = request.path if has_request_context() else "-"
        record.status = getattr(record, "status", "-")
        record.duration_ms = getattr(record, "duration_ms", "-")
        return True


def _register_access_log(app: Flask) -> None:
    import time

    @app.before_request
    def _mark_start():
        from flask import g

        g._req_started = time.monotonic()

    @app.after_request
    def _log_request(response):
        from flask import g, request

        started = getattr(g, "_req_started", None)
        duration_ms = round((time.monotonic() - started) * 1000, 1) if started else "-"
        if request.path not in ("/health",):
            app.logger.info(
                "request route=%s method=%s status=%s duration_ms=%s",
                request.path,
                request.method,
                response.status_code,
                duration_ms,
                extra={"status": response.status_code, "duration_ms": duration_ms},
            )
        return response


def create_app():
    app = Flask(__name__)
    _configure_logging()
    _assert_safe_boot(app)

    from app.core.errors import register_error_handlers
    from app.core.request_id import register_request_id

    register_request_id(app)
    register_error_handlers(app)

    # Guard must run before any view; after request-id so logs carry it.
    register_legacy_guard(app)
    _register_access_log(app)

    # Request size cap (Phase 1.4): imports are the largest legitimate body.
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH_MB", "10")) * 1024 * 1024

    # ProxyFix with an explicit trusted-proxy count (never trust raw
    # X-Forwarded-For from arbitrary clients).
    trusted = int(os.getenv("TRUSTED_PROXIES", "0"))
    if trusted > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted, x_proto=trusted, x_host=trusted)

    origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    CORS(app, origins=origins, expose_headers=["X-Request-ID"])

    init_limiter(app)

    @app.errorhandler(413)
    def _too_large(_exc):
        from app.core import error

        return error("PAYLOAD_TOO_LARGE", "Request body is too large.", 413)

    @app.errorhandler(429)
    def _rate_limited(_exc):
        from app.core import error

        return error("RATE_LIMITED", "Too many requests. Please try again shortly.", 429)

    # Security headers on API responses (Phase 1.4).
    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if response.mimetype == "application/json":
            # Authenticated JSON must never be cached by a shared cache.
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # Optional Sentry: no-op unless SENTRY_DSN is set.
    dsn = os.getenv("SENTRY_DSN")
    if dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(dsn=dsn, traces_sample_rate=0.1)
            app.logger.info("Sentry enabled")
        except Exception as exc:  # never break boot on observability failure
            app.logger.warning("Sentry init failed: %s", exc)

    @app.route("/")
    def home():
        return {
            "status": "success",
            "message": "AC Management System API is running",
        }

    from app.api.v1.assets import assets_v1_bp
    from app.api.v1.assets_import import assets_import_v1_bp
    from app.api.v1.assets_write import assets_write_v1_bp
    from app.api.v1.campus import campus_v1_bp
    from app.api.v1.campus_import import campus_import_v1_bp
    from app.api.v1.chat import chat_v1_bp
    from app.api.v1.energy import energy_v1_bp
    from app.api.v1.issue_reports import issue_reports_v1_bp
    from app.api.v1.maintenance import maintenance_v1_bp
    from app.api.v1.movements import movements_v1_bp
    from app.api.v1.public import public_v1_bp
    from app.api.v1.qr import qr_v1_bp
    from app.api.v1.reports import reports_v1_bp
    from app.api.v1.sessions import sessions_v1_bp
    from app.api.v1.system import system_v1_bp
    from app.api.v1.users import users_v1_bp

    app.register_blueprint(assets_v1_bp)
    app.register_blueprint(assets_write_v1_bp)
    app.register_blueprint(assets_import_v1_bp)
    app.register_blueprint(campus_v1_bp)
    app.register_blueprint(campus_import_v1_bp)
    app.register_blueprint(chat_v1_bp)
    app.register_blueprint(energy_v1_bp)
    app.register_blueprint(issue_reports_v1_bp)
    app.register_blueprint(maintenance_v1_bp)
    app.register_blueprint(movements_v1_bp)
    app.register_blueprint(qr_v1_bp)
    app.register_blueprint(reports_v1_bp)
    app.register_blueprint(sessions_v1_bp)
    app.register_blueprint(system_v1_bp)
    app.register_blueprint(users_v1_bp)
    app.register_blueprint(public_v1_bp)

    return app
