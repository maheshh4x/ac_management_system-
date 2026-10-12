"""Run app.migrate against the LOCAL test DB, bypassing the .env override.

backend/.env points at the live Supabase cluster and database.py loads it with
override=True, so plain `python -m app.migrate up` would hit live. Importing
database first (dotenv already applied) and then re-pointing os.environ makes
the lazily-created pool use the local throwaway DB instead.
"""

import os

import app.config.database  # noqa: F401  — loads .env first so we can override after

os.environ["DB_HOST"] = "localhost"
os.environ["DB_PORT"] = "5433"
os.environ["DB_NAME"] = "postgres"
os.environ["DB_USER"] = "postgres"
os.environ["DB_PASSWORD"] = "postgres"
os.environ["DB_SSLMODE"] = "disable"  # local docker Postgres; .env says require

from app import migrate  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(migrate.main(["up"]))
