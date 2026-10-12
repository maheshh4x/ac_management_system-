"""WSGI entry point for production servers (Phase 1.2).

Run with gunicorn (see gunicorn.conf.py):
    gunicorn -c gunicorn.conf.py wsgi:app

Never use Flask's development server (run.py) in production.
"""

from app import create_app

app = create_app()
