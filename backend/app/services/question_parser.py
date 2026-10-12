import re


def normalize_question(question):
    return " ".join(question.lower().strip().split())


def extract_ac_id(question):
    match = re.search(r"\bac\d+\b", question, re.IGNORECASE)
    return match.group(0).upper() if match else None


def extract_company(question):
    companies = [
        "daikin",
        "lg",
        "samsung",
        "voltas",
        "carrier",
        "blue star",
        "panasonic",
        "hitachi",
        "mitsubishi",
    ]

    question_lower = question.lower()

    for company in companies:
        if company in question_lower:
            return company

    return None


def detect_intent(question):
    q = normalize_question(question)

    ac_id = extract_ac_id(q)
    company = extract_company(q)

    # Phase 7: identity-scoped intent — must be checked before the generic
    # "show/list" branch so "show my work orders" never widens to a global list.
    if any(phrase in q for phrase in ("my job", "my work order", "assigned to me", "my task", "my assign")):
        return {"intent": "MY_JOBS", "ac_id": ac_id, "company": company}

    if ac_id and any(word in q for word in ["maintenance", "service", "serviced"]):
        return {"intent": "AC_MAINTENANCE", "ac_id": ac_id, "company": company}

    if ac_id and any(word in q for word in ["details", "detail", "information", "info", "about"]):
        return {"intent": "AC_DETAILS", "ac_id": ac_id, "company": company}

    if any(word in q for word in ["each company", "by company", "per company"]):
        return {"intent": "AC_COMPANY_COUNTS", "ac_id": ac_id, "company": company}

    if any(word in q for word in ["which block", "most ac", "highest number"]):
        return {"intent": "AC_BLOCK_COUNTS", "ac_id": ac_id, "company": company}

    if "tonnage" in q or "total ton" in q:
        return {"intent": "AC_TONNAGE", "ac_id": ac_id, "company": company}

    if "missing" in q and "model" in q:
        return {"intent": "AC_MISSING_MODEL", "ac_id": ac_id, "company": company}

    if any(word in q for word in ["how many", "count", "number of", "total"]):
        if "maintenance" in q or "service" in q:
            return {"intent": "MAINTENANCE_COUNT", "ac_id": ac_id, "company": company}

        if "not working" in q or "inactive" in q or "damaged" in q:
            return {"intent": "AC_STATUS", "status": "Not Working", "ac_id": ac_id, "company": company}

        if "under maintenance" in q or "under service" in q:
            return {"intent": "AC_STATUS", "status": "Under Maintenance", "ac_id": ac_id, "company": company}

        if "working" in q or "operational" in q:
            return {"intent": "AC_STATUS", "status": "Working", "ac_id": ac_id, "company": company}

        if "maintenance" in q:
            return {"intent": "AC_STATUS", "status": "Under Maintenance", "ac_id": ac_id, "company": company}

        if "ac" in q:
            return {"intent": "TOTAL_ACS", "ac_id": ac_id, "company": company}

    if company and ("ac" in q or "air conditioner" in q):
        return {"intent": "AC_BY_COMPANY", "ac_id": ac_id, "company": company}

    if any(word in q for word in ["show", "list", "display", "find"]):
        location_words = [" in ", " at ", " from ", " block ", " room "]
        if any(word in f" {q} " for word in location_words):
            return {
                "intent": "AC_LOCATION",
                "location": _extract_location(q),
                "ac_id": ac_id,
                "company": company,
            }
        if "maintenance" in q or "service" in q:
            return {"intent": "MAINTENANCE_LIST", "ac_id": ac_id, "company": company}

        if "ac" in q or "air conditioner" in q:
            return {"intent": "AC_LIST", "ac_id": ac_id, "company": company}

    return {"intent": "UNKNOWN", "ac_id": ac_id, "company": company}


def _extract_location(question):
    match = re.search(r"(?:in|at|from|block|room)\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
    return match.group(1).strip() if match else question
