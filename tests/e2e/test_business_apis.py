"""Real HTTP and PostgreSQL business slice; no dependency overrides or mocks."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from scripts.business_environment import BusinessProcesses, historical_snapshot, prepare
from scripts.database.engine import make_engine
from scripts.e2e_environment import tokens

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def business():
    if os.environ.get("FLOWBRIDGE_BUSINESS_E2E") != "1":
        pytest.skip("Opt-in local Business APIs: FLOWBRIDGE_BUSINESS_E2E=1")
    directory = prepare()
    state = json.loads((directory / "resources.json").read_text())
    processes = BusinessProcesses(directory)
    auth = tokens()
    try:
        for domain in ("sales", "warehouse", "logistics"):
            processes.start(domain)
        yield state, auth
    finally:
        processes.close()
        assert historical_snapshot() == state["historical_before"]


def request(domain, method, path, auth, label="sales", body=None, expected=200, key=None):
    port = {"sales": 18101, "warehouse": 18102, "logistics": 18103}[domain]
    headers = {"Authorization": "Bearer " + auth[label], "Idempotency-Key": key or uuid4().hex}
    response = httpx.request(
        method,
        f"http://127.0.0.1:{port}/api/v1/{domain}/workflow" + path,
        json=body,
        headers=headers,
        timeout=20,
    )
    assert response.status_code == expected, (
        f"{domain} {path}: {response.status_code} {response.text}"
    )
    return response.json()


def create_order(state, auth, suffix="normal", quantities=(5, 3)):
    body = {
        "org_id": state["org_id"],
        "order_number": state["run_id"] + "_" + suffix,
        "warehouse_ref": state["warehouse_id"],
        "lines": [
            {"product_ref": pid, "sku": state["run_id"] + "_" + str(i), "uom": "EA", "quantity": q}
            for i, (pid, q) in enumerate(zip(state["products"], quantities, strict=True), 1)
        ],
    }
    key = uuid4().hex
    order = request("sales", "POST", "/orders", auth, body=body, expected=201, key=key)
    assert request("sales", "POST", "/orders", auth, body=body, expected=201, key=key) == order
    body["lines"][0]["quantity"] += 1
    request("sales", "POST", "/orders", auth, body=body, expected=409, key=key)
    return order


def test_real_reservation_packing_handoff_delivery_and_duplicate_commands(business):
    state, auth = business
    order = create_order(state, auth)
    ref = {"org_id": state["org_id"], "order_id": order["id"]}
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: request(
                    "warehouse", "POST", "/reservations", auth, body=ref, expected=201
                ),
                range(2),
            )
        )
    assert results[0]["id"] == results[1]["id"]
    request("logistics", "POST", "/shipments", auth, body=ref, expected=409)  # packing required
    packed = request("warehouse", "POST", "/pack", auth, label="outsider", body=ref)
    assert packed["dispatch"]["status"] == "PREPARED"
    shipment = request("logistics", "POST", "/shipments", auth, body=ref, expected=201)
    assert (
        request("logistics", "POST", "/shipments", auth, body=ref, expected=201)["id"]
        == shipment["id"]
    )
    request(
        "logistics",
        "POST",
        "/shipments/status",
        auth,
        label="it",
        body={**ref, "status": "DELIVERED"},
        expected=409,
    )
    handoff = request("warehouse", "POST", "/handoff", auth, label="outsider", body=ref)
    assert handoff["status"] == "DISPATCHED"
    request(
        "warehouse", "POST", "/handoff", auth, label="outsider", body=ref
    )  # no second stock exit
    request(
        "logistics",
        "POST",
        "/shipments/status",
        auth,
        label="it",
        body={**ref, "status": "HANDED_OVER"},
    )
    delivered = request(
        "logistics",
        "POST",
        "/shipments/status",
        auth,
        label="it",
        body={**ref, "status": "DELIVERED"},
    )
    assert delivered["status"] == "DELIVERED" and delivered["warehouse_handoff_event_ref"]
    for status in ("RESERVED", "SHIPPED", "DELIVERED"):
        request("sales", "POST", "/orders/status", auth, body={**ref, "status": status})
    engine = make_engine("warehouse")
    try:
        with engine.connect() as conn:
            balances = conn.execute(
                sa.text(
                    "SELECT on_hand_quantity,reserved_quantity FROM inventory_balances "
                    "WHERE org_id=:org ORDER BY product_id"
                ),
                {"org": UUID(state["org_id"])},
            ).all()
            assert sorted(balances) == [(95, 0), (97, 0)]
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM stock_movements "
                        "WHERE org_id=:org AND movement_type='DISPATCH'"
                    ),
                    {"org": UUID(state["org_id"])},
                )
                == 2
            )
    finally:
        engine.dispose()


def test_real_insufficient_order_rolls_back_all_lines_and_unauthorized_actor_denied(business):
    state, auth = business
    order = create_order(state, auth, "short", (1, 9999))
    ref = {"org_id": state["org_id"], "order_id": order["id"]}
    request("warehouse", "POST", "/reservations", auth, body=ref, expected=409)
    request("warehouse", "POST", "/reservations", auth, label="it", body=ref, expected=403)
    request(
        "warehouse",
        "POST",
        "/reservations",
        auth,
        body={**ref, "org_id": str(uuid4())},
        expected=403,
    )
    engine = make_engine("warehouse")
    try:
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text("SELECT count(*) FROM reservations WHERE sales_order_ref=:order"),
                    {"order": UUID(order["id"])},
                )
                == 0
            )
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT sum(reserved_quantity) FROM inventory_balances WHERE org_id=:org"
                    ),
                    {"org": UUID(state["org_id"])},
                )
                == 0
            )
    finally:
        engine.dispose()
