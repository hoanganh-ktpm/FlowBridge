from copy import deepcopy

import pytest
from scripts.database.fixtures.business import canonical_hash
from scripts.database.simulation import SimulationConfig, simulate
from scripts.database.synthetic_validation import validate


@pytest.fixture(scope="module")
def snapshot():
    config = SimulationConfig(days=60, scale=0.15)
    return config, simulate(config)


def test_deterministic_timeline_and_required_scenarios(snapshot):
    config, rows = snapshot
    assert rows == simulate(config)
    report = validate(rows, config)
    assert report["valid"], report["errors"]
    assert all(report["scenarios"].values())
    assert report["skus"] == 8
    assert rows != simulate(SimulationConfig(seed=config.seed + 1, days=60, scale=0.15))


@pytest.mark.parametrize(
    "group,corrupt",
    [
        (
            "ledger",
            lambda r: r["warehouse"]["inventory_balances"][0].update(on_hand_quantity=999999),
        ),
        (
            "capacity",
            lambda r: [
                c.update(max_dispatches=0) for c in r["logistics"]["handoff_capacity_calendar"]
            ],
        ),
        ("handoff", lambda r: r["warehouse"]["outbox_events"][0].update(payload_hash="0" * 64)),
        (
            "reservations",
            lambda r: r["warehouse"]["reservation_lines"][0].update(allocated_quantity=0),
        ),
        ("inbound", lambda r: r["warehouse"]["inbound_plans"][0].update(planned_quantity=1)),
        (
            "duplicates",
            lambda r: r["sales"]["sales_order_events"].append(
                r["sales"]["sales_order_events"][0].copy()
            ),
        ),
    ],
)
def test_validator_reports_corrupt_record(snapshot, group, corrupt):
    config, source = snapshot
    rows = deepcopy(source)
    corrupt(rows)
    report = validate(rows, config)
    assert not report["valid"]
    assert report["checks"][group] == "FAIL"
    assert report["errors"][group]  # Record identifiers and reasons, not just a boolean.


def test_hash_uses_business_payload_not_dictionary_order():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})
