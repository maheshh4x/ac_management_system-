"""Development entry point. Production uses gunicorn + wsgi.py.

debug=True is forbidden outside a laptop: FLASK_DEBUG only engages when
APP_ENV != production, and production boot refuses debug entirely
(see app._assert_safe_boot).
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    debug = (
        os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
        and os.getenv("APP_ENV", "development").lower() != "production"
    )
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=debug)
