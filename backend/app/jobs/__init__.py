"""Runnable background jobs (standalone — no Flask app context).

Phase 4: ``python -m app.jobs.notify_due`` sends due/overdue job reminders.
Each module owns its connection and commits explicitly; nothing here touches
business data, so there is no ``audit()`` involvement.
"""
