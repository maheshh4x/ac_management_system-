"""Monotonic UTC clock for written event/audit timestamps (Phase 8.1).

``datetime.now()`` is only as fine-grained as the platform timer. On Windows
the observed granularity is ~15.6 ms: two calls a millisecond apart routinely
return the *same* microsecond (measured 19992/20000 identical back-to-back
pairs, and 16/200 identical across a real database round-trip).

``maintenance_events.id`` is ``gen_random_uuid()``, so ordering a work-order
timeline by ``created_at, id`` cannot break such a tie deterministically —
the timeline could render ``ASSIGNED`` before ``CREATED``. Guaranteeing that
successive stamps from this process are strictly increasing makes the write
order the sort order for every pair of events one process emits, on any
platform, without a schema change.

Clock steps backwards (NTP) are pulled forward instead of regressing, and the
lead over wall-clock time is capped so a coarse timer cannot drift timestamps
arbitrarily far ahead — beyond the cap the wall clock wins and a tie becomes
possible again, which is the lesser evil.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

# Never let the monotonic bump run more than this far ahead of the real clock.
MAX_LEAD = timedelta(seconds=1)

_lock = threading.Lock()
_last: datetime | None = None


def utcnow() -> datetime:
    """Return a UTC timestamp that is strictly greater than the previous one."""
    global _last
    with _lock:
        stamp = datetime.now(timezone.utc)
        previous = _last
        if previous is not None:
            if stamp < previous - MAX_LEAD:
                # Long stall or a large backwards step: drop the anchor.
                previous = None
                _last = stamp
            elif stamp <= previous:
                stamp = previous + timedelta(microseconds=1)
                _last = stamp
            else:
                _last = stamp
        else:
            _last = stamp
        return stamp


def reset() -> None:
    """Forget the anchor (tests only — starts the sequence from wall time)."""
    global _last
    with _lock:
        _last = None
