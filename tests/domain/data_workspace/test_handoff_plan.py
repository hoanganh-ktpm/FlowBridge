# SPDX-License-Identifier: GPL-3.0-or-later
from copy import deepcopy

import pytest
from pydantic import ValidationError
from workspace_app.handoff_plan import HandoffPlan, assess_slot


def facts():
    return (
        {
            "deadline": "2026-10-27T08:00:00+07:00",
            "handoff_deadline": "2026-10-27T10:00:00+07:00",
            "fulfilled_at": "2026-10-27T07:30:00+07:00",
        },
        [
            {
                "carrier_code": "CARRIER-SOUTH",
                "capacity_date": "2026-10-27",
                "used_dispatches": 0,
                "max_dispatches": 1,
                "status": "ACTIVE",
            }
        ],
        {"carrier_code": "CARRIER-SOUTH", "scheduled_handoff_at": "2026-10-27T10:00:00+07:00"},
    )


def test_proposed_slot_checks_one_extra_dispatch_without_claiming_a_booking():
    selected, capacity, planned = facts()
    result = assess_slot(selected, [], capacity, {"minutes": 120}, "Asia/Ho_Chi_Minh", planned)
    assert result["confirmable"] is True and result["booking_status"] == "PROPOSED_CAPACITY"
    assert capacity[0]["used_dispatches"] == 0
    assert not assess_slot(selected, [], capacity, {"minutes": 120}, "Asia/Ho_Chi_Minh")[
        "confirmable"
    ]
    booked = [{**planned, "status": "ACCEPTED"}]
    capacity[0]["used_dispatches"] = 1
    assert assess_slot(selected, booked, capacity, {"minutes": 120}, "Asia/Ho_Chi_Minh", planned)[
        "confirmable"
    ]
    assert not assess_slot(selected, [], capacity, {"minutes": 120}, "Asia/Ho_Chi_Minh", planned)[
        "confirmable"
    ]


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "inactive", "late", "preparation", "conflict"]
)
def test_unsafe_or_unproven_slot_does_not_pass(change):
    selected, capacity, planned = facts()
    schedule, policy = [], {"minutes": 120}
    if change == "missing":
        capacity.clear()
    elif change == "duplicate":
        capacity.append(deepcopy(capacity[0]))
    elif change == "inactive":
        capacity[0]["status"] = "INACTIVE"
    elif change == "late":
        planned["scheduled_handoff_at"] = "2026-10-27T11:00:00+07:00"
    elif change == "preparation":
        policy = None
    else:
        schedule = [{**planned, "carrier_code": "OTHER", "status": "ACCEPTED"}]
    assert not assess_slot(selected, schedule, capacity, policy, "Asia/Ho_Chi_Minh", planned)[
        "confirmable"
    ]


def test_planned_time_requires_timezone_and_rejects_forged_booking_status():
    _, _, plan = facts()
    for change in ({"status": "ACCEPTED"}, {"scheduled_handoff_at": "2026-10-27T10:00:00"}):
        with pytest.raises(ValidationError):
            HandoffPlan.model_validate({**plan, **change})
