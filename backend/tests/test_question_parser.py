"""Phase 8.1 â€” unit tests for ``app.services.question_parser``.

``detect_intent`` routes every natural-language chat question, but only a few
intents were covered indirectly through ``test_chat.py`` (the module sat at
62%). It is a pure function, so the whole branch tree is pinned down here with
a table of one question per intent â€” including the ordering guarantees that
stop "show my work orders" from widening into a global list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.question_parser import (  # noqa: E402
    _extract_location,
    detect_intent,
    extract_ac_id,
    extract_company,
    normalize_question,
)

# â”€â”€ helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def test_normalize_question_collapses_whitespace_and_lowercases():
    assert normalize_question("  Show   AC\t Details \n") == "show ac details"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("check ac101 please", "AC101"),
        ("CHECK Ac77", "AC77"),
        ("AC101 is broken", "AC101"),
        ("the ac unit ac42 in room 3", "AC42"),
    ],
)
def test_extract_ac_id(question, expected):
    assert extract_ac_id(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "no identifier here",
        "ac-101 is broken",  # hyphen breaks \bac\d+\b
        "ac101x is broken",  # trailing word character breaks the boundary
        "mac101 is not an ac",
    ],
)
def test_extract_ac_id_returns_none_when_absent(question):
    assert extract_ac_id(question) is None


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("A Daikin unit", "daikin"),
        ("SAMSUNG tower", "samsung"),
        ("blue star chiller", "blue star"),
        ("MITSUBISHI vrf", "mitsubishi"),
        ("nothing to see", None),
    ],
)
def test_extract_company(question, expected):
    assert extract_company(question) == expected


def test_extract_location_reads_past_the_keyword():
    assert _extract_location("show ac in room 102?") == "room 102"
    assert _extract_location("ac from block c?") == "block c"


def test_extract_location_falls_back_to_the_whole_question():
    assert _extract_location("no location mentioned") == "no location mentioned"


# â”€â”€ intent routing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

INTENT_CASES = [
    # identity-scoped â€” must win over every generic branch
    ("show my work orders", "MY_JOBS", None, None),
    ("what is assigned to me", "MY_JOBS", None, None),
    ("my tasks for today", "MY_JOBS", None, None),
    ("my assignments", "MY_JOBS", None, None),
    ("details about my assigned task", "MY_JOBS", None, None),
    ("my task for ac101", "MY_JOBS", "AC101", None),
    # per-AC branches
    ("maintenance history of ac101", "AC_MAINTENANCE", "AC101", None),
    ("was ac202 serviced by daikin", "AC_MAINTENANCE", "AC202", "daikin"),
    ("maintenance details for ac101", "AC_MAINTENANCE", "AC101", None),
    ("details about ac101", "AC_DETAILS", "AC101", None),
    ("information for ac101", "AC_DETAILS", "AC101", None),
    ("tell me about ac101", "AC_DETAILS", "AC101", None),
    # aggregates
    ("ac count by company", "AC_COMPANY_COUNTS", None, None),
    ("each company ac count", "AC_COMPANY_COUNTS", None, None),
    ("per company ac totals", "AC_COMPANY_COUNTS", None, None),
    ("which block has most ac", "AC_BLOCK_COUNTS", None, None),
    ("the highest number of ac per block", "AC_BLOCK_COUNTS", None, None),
    ("total tonnage of all ac", "AC_TONNAGE", None, None),
    ("how many ac are missing model", "AC_MISSING_MODEL", None, None),
    ("ac missing model numbers", "AC_MISSING_MODEL", None, None),
    # count/quantity family
    ("how many maintenance records", "MAINTENANCE_COUNT", None, None),
    ("total service jobs", "MAINTENANCE_COUNT", None, None),
    ("how many ac not working", "AC_STATUS", None, None),
    ("how many inactive ac", "AC_STATUS", None, None),
    ("how many ac are damaged", "AC_STATUS", None, None),
    ("how many ac are operational", "AC_STATUS", None, None),
    ("count working ac", "AC_STATUS", None, None),
    ("total number of ac", "TOTAL_ACS", None, None),
    ("how many ac do we have", "TOTAL_ACS", None, None),
    # company / location / listing
    ("daikin ac list", "AC_BY_COMPANY", None, "daikin"),
    ("voltas air conditioner details", "AC_BY_COMPANY", None, "voltas"),
    ("show ac in block a", "AC_LOCATION", None, None),
    ("find ac at room 102", "AC_LOCATION", None, None),
    ("list ac", "AC_LIST", None, None),
    ("display all ac", "AC_LIST", None, None),
    ("show maintenance list", "MAINTENANCE_LIST", None, None),
    ("list service jobs", "MAINTENANCE_LIST", None, None),
    # anything else
    ("hello there", "UNKNOWN", None, None),
    ("what time is it", "UNKNOWN", None, None),
]


@pytest.mark.parametrize(("question", "intent", "ac_id", "company"), INTENT_CASES)
def test_detect_intent(question, intent, ac_id, company):
    result = detect_intent(question)
    assert result["intent"] == intent, f"{question!r} -> {result}"
    assert result["ac_id"] == ac_id, question
    assert result["company"] == company, question


@pytest.mark.parametrize("question, intent, ac_id, company", INTENT_CASES)
def test_every_intent_carries_the_identity_keys(question, intent, ac_id, company):
    """Consumers read ac_id/company off every intent, so the shape is fixed."""
    assert set(detect_intent(question)) >= {"intent", "ac_id", "company"}


def test_status_intents_carry_the_resolved_status():
    assert detect_intent("how many ac not working")["status"] == "Not Working"
    assert detect_intent("count working ac")["status"] == "Working"


def test_location_intent_carries_the_extracted_location():
    result = detect_intent("show ac in room 102")
    assert result["intent"] == "AC_LOCATION"
    assert result["location"] == "room 102"


def test_my_jobs_is_detected_before_any_global_listing():
    """The Phase 7 rule: 'my X' must never widen into a global list."""
    for question in ("show my work orders", "list my tasks", "display my assignments"):
        assert detect_intent(question)["intent"] == "MY_JOBS"


def test_ac_details_beats_the_generic_show_list_branch():
    assert detect_intent("show details of ac101")["intent"] == "AC_DETAILS"


def test_maintenance_beats_details_for_the_same_ac():
    assert detect_intent("maintenance details of ac101")["intent"] == "AC_MAINTENANCE"


def test_matching_is_case_and_punctuation_insensitive():
    assert detect_intent("HOW MANY AC ARE NOT WORKING?")["intent"] == "AC_STATUS"
    assert detect_intent("Details About AC101.")["intent"] == "AC_DETAILS"
