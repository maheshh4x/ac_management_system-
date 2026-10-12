"""Assistant endpoint (contract line 68): ``POST /api/v1/chat`` — Phase 7.

Authenticated (``VIEW_ASSET``), rate-limited, deterministic intents only: the
rule-based parser in ``app.services.question_parser`` classifies the question
and a fixed dispatch answers from SQL — no LLM calls, no free-form execution.

* ``INTENT_PERMISSIONS`` maps intent → required permission (default
  ``VIEW_ASSET``), so a single gate can lock an intent down per role; the
  test suite monkeypatches it to prove 403 handling.
* ``MY_JOBS`` (new this phase) is identity-scoped: a caller only ever sees
  their own work orders, regardless of role.
* List-shaped ``data`` is capped at ``DATA_CAP`` rows (the answer text always
  reports the true total) so one question can never stream the whole table.

Replaces the legacy anonymous ``POST /api/chat``, removed this phase.
"""

from __future__ import annotations

import logging
import time

from flask import Blueprint, g, request

from app.auth.jwt import get_current_user, require_auth
from app.auth.permissions import has_permission, require_permission
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.services import query_service
from app.services.question_parser import detect_intent

chat_v1_bp = Blueprint("chat_v1", __name__)

logger = logging.getLogger(__name__)

DATA_CAP = 50

# Intent → minimum permission. Unlisted intents fall back to VIEW_ASSET —
# every signed-in role holds it, which keeps the widget usable everywhere
# while still leaving one place to tighten a specific intent later.
INTENT_PERMISSIONS: dict[str, str] = {
    "MY_JOBS": "VIEW_ASSET",  # identity-scoped rows, no cross-user data
}
DEFAULT_INTENT_PERMISSION = "VIEW_ASSET"


def _jsonable(value):
    """Dates → ISO, Decimal → float, UUID → str (recursively)."""
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, float) or type(value).__name__ == "Decimal":
        return float(value)
    if type(value).__name__ == "UUID":
        return str(value)
    return value


def _capped(data):
    """Keep list payloads bounded; answers already state the true count."""
    if isinstance(data, list) and len(data) > DATA_CAP:
        return data[:DATA_CAP]
    return data


def _my_jobs_answer(user: dict) -> tuple[str, object]:
    """Counts + the caller's most recent work orders (identity-scoped)."""
    user_id = user.get("id")
    summary = query_service.my_jobs_summary(user_id)[0]
    rows = query_service.my_jobs(user_id, DATA_CAP)
    total = summary.get("total") or 0
    if not total:
        return "You have no work orders assigned to you.", {"summary": summary, "items": []}
    answer = (
        f"You have {summary.get('open') or 0} open work order(s)"
        f" ({total} total, {summary.get('overdue') or 0} overdue)."
    )
    if summary.get("in_progress"):
        answer += f" {summary.get('in_progress')} in progress."
    return answer, {"summary": summary, "items": rows}


def _dispatch(intent: str, rec: dict, user: dict) -> tuple[str, object]:
    """Deterministic intent → (answer text, data payload)."""
    if intent == "TOTAL_ACS":
        total = query_service.total_acs()
        return f"There are {total} ACs in the system.", {"total": total}

    if intent == "AC_COMPANY_COUNTS":
        data = query_service.ac_company_counts()
        answer = (
            "; ".join(f"{row['company']}: {row['count']}" for row in data) or "No AC records are available."
        )
        return answer, _capped(data)

    if intent == "AC_BLOCK_COUNTS":
        data = query_service.ac_block_counts()
        answer = (
            f"{data[0]['block']} has the most ACs with {data[0]['count']}."
            if data
            else "No AC records are available."
        )
        return answer, _capped(data)

    if intent == "AC_TONNAGE":
        total = query_service.total_tonnage()
        return f"The total installed capacity is {total} tons.", {"total_tonnage": _jsonable(total)}

    if intent == "AC_MISSING_MODEL":
        total = query_service.missing_model_count()
        return f"{total} AC(s) are missing model information.", {"missing_model_count": total}

    if intent == "AC_LOCATION":
        data = query_service.acs_in_location(rec["location"])
        return f"Found {len(data)} AC(s) matching {rec['location']}.", _capped(data)

    if intent == "AC_STATUS":
        data = query_service.ac_status(rec["status"])
        return f"Found {len(data)} AC(s) with status {rec['status']}.", _capped(data)

    if intent == "AC_BY_COMPANY":
        data = query_service.ac_by_company(rec["company"])
        return f"Found {len(data)} AC(s) from {rec['company']}.", _capped(data)

    if intent == "AC_DETAILS":
        data = query_service.ac_details(rec["ac_id"])
        if not data:
            return f"No AC found with ID {rec['ac_id']}.", []
        return f"Here are the details for {rec['ac_id']}.", data

    if intent == "AC_MAINTENANCE":
        data = query_service.maintenance_for_ac(rec["ac_id"])
        return f"Found {len(data)} maintenance record(s) for {rec['ac_id']}.", _capped(data)

    if intent == "MAINTENANCE_COUNT":
        total = query_service.maintenance_count()
        return f"There are {total} maintenance records in the system.", {"total": total}

    if intent == "MAINTENANCE_LIST":
        data = query_service.maintenance_list()
        return f"Found {len(data)} maintenance record(s).", _capped(data)

    if intent == "AC_LIST":
        data = query_service.ac_list()
        return f"Found {len(data)} AC(s).", _capped(data)

    if intent == "MY_JOBS":
        return _my_jobs_answer(user)

    return (
        "I could not understand that question yet. "
        "Try asking about ACs, status, companies, jobs or maintenance.",
        [],
    )


@chat_v1_bp.route("/api/v1/chat", methods=["POST"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(30, 60, "chat")
def chat():
    """Answer one deterministic question about the campus dataset.

    Body: ``{"question": "..."}`` → ``data``:
    ``{question, intent, answer, data, latency_ms}``. ``MY_JOBS`` is scoped to
    the caller's own work orders; list payloads are capped at ``DATA_CAP``.
    """
    body = request.get_json(silent=True) or {}
    question = str(body.get("question") or "").strip()
    if not question:
        return error("QUESTION_REQUIRED", "Question is required.", 400)
    if len(question) > 500:
        return error("QUESTION_TOO_LONG", "Question must be at most 500 characters.", 400)

    rec = detect_intent(question)
    intent = rec["intent"]

    user = get_current_user() or {}
    required = INTENT_PERMISSIONS.get(intent, DEFAULT_INTENT_PERMISSION)
    if not has_permission(user.get("role"), required):
        return error(
            "FORBIDDEN",
            "You do not have permission to perform this action.",
            403,
        )

    started = time.perf_counter()
    try:
        answer, data = _dispatch(intent, rec, user)
    except Exception:
        logger.exception(
            "POST /api/v1/chat failed intent=%s request_id=%s",
            intent,
            getattr(g, "request_id", "-"),
        )
        return error("CHAT_FAILED", "Unable to process the question.", 500)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)

    logger.info(
        "chat intent=%s latency_ms=%s request_id=%s",
        intent,
        latency_ms,
        getattr(g, "request_id", "-"),
    )
    return success(
        {
            "question": question,
            "intent": intent,
            "answer": answer,
            "data": _jsonable(data),
            "latency_ms": latency_ms,
        }
    )
