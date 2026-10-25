# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare scoped obligations, never turn event arrival into approval authority."""

from copy import deepcopy

from .decision_freshness import digest, ordered
from .planning import moment

VERSION = "decision_obligations_v1"


def dependency_evidence(payload, inputs, selected, history_hash, schedule, capacities, policy):
    """Keep the action and original line identities stable; omit incidental read times.

    The planner still reads the entire warehouse, so competing orders are accounted
    for before comparing the selected order's result. No arbitrary SKU row is
    discarded before allocation. Changes to another SKU can be harmless only when
    they leave this order's obligations and all its conditions unchanged.
    """
    scope = payload["scope"]
    order = str(scope["order_id"])
    original = ordered([row for row in inputs["open_rows"] if str(row["order_id"]) == order])
    plan = deepcopy(selected)
    plan.pop("order_number", None)
    plan["lines"] = ordered(plan["lines"])
    plan["shortages_at_deadline"] = ordered(plan["shortages_at_deadline"])
    ready = plan.get("fulfilled_at")
    # Opening stock is available at each new snapshot timestamp. Compare the
    # cutoff obligation rather than treating a later read as changed supply.
    if ready and moment(ready) <= moment(plan["deadline"]):
        plan["fulfilled_at"] = "BY_STOCK_READY_CUTOFF"
    scheduled = ordered(schedule)
    contract = {
        "scope": scope,
        "assumptions": payload["scenario"]["evidence"]["assumptions"],
        "original_order": original,
        "forecast_history": history_hash,
        "preparation_minutes": None if policy is None else policy["minutes"],
        "plan": plan,
        "schedule": scheduled,
    }
    warehouse = str(scope["warehouse_id"])
    dependencies = {}
    for name, values in (
        ("stock", inputs["stocks"]),
        ("inbound", inputs["inbounds"]),
        ("orders", inputs["open_rows"]),
        ("commitments", inputs["commitment_rows"]),
        ("preparation", inputs["preparation_rows"]),
    ):
        dependencies[name] = digest(
            None
            if values is None
            else ordered([r for r in values if str(r["warehouse_id"]) == warehouse])
        )
    dependencies.update(
        history=history_hash, schedule=digest(scheduled), capacity=digest(ordered(capacities))
    )
    return {"version": VERSION, "contract": contract, "dependencies": dependencies}


def assess_change(baseline, current):
    """A changed obligation or broken condition always requires a new revision.

    A matching result is not sufficient to ignore corrected forecast history,
    changed original lines, changed preparation duration or a moved carrier slot.
    These are included explicitly in the immutable obligation contract.
    """
    before, after = (baseline or {}).get("impact_evidence"), current.get("impact_evidence")
    if (
        not before
        or not after
        or before.get("version") != VERSION
        or after.get("version") != VERSION
    ):
        return {
            "classification": "REQUIRES_REVISION",
            "requires_revision": True,
            "diff": [{"path": "evidence_version", "before": None, "after": VERSION}],
        }
    changed = []
    for name in sorted(set(before["contract"]) | set(after["contract"])):
        old, new = before["contract"].get(name), after["contract"].get(name)
        if digest(old) != digest(new):
            changed.append({"path": name, "before": old, "after": new})
    for name in ("warehouse_confirmable", "logistics_confirmable"):
        if baseline.get(name) != current.get(name):
            changed.append({"path": name, "before": baseline.get(name), "after": current.get(name)})
    observed = [
        {
            "dependency": name,
            "before_sha256": before["dependencies"].get(name),
            "after_sha256": after["dependencies"].get(name),
        }
        for name in sorted(set(before["dependencies"]) | set(after["dependencies"]))
        if before["dependencies"].get(name) != after["dependencies"].get(name)
    ]
    return {
        "classification": "REQUIRES_REVISION"
        if changed
        else "UNCHANGED"
        if baseline["input_sha256"] == current["input_sha256"]
        else "NO_EFFECT",
        "requires_revision": bool(changed),
        "diff": changed,
        "observed_changes": observed,
        "warning": "Recomputed from current governed sources; no automatic approval or rollback",
    }
