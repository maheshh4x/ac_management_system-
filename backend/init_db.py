"""Deprecated: migrations are now tracked.

Use `python -m app.migrate up` instead — it records applied files and their
checksums in schema_migrations and refuses to re-apply or apply drifted files.
This shim delegates to it so legacy runbooks keep working.
"""
from app.migrate import main

if __name__ == "__main__":
    raise SystemExit(main(["up"]))
