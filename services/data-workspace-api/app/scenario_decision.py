# SPDX-License-Identifier: GPL-3.0-or-later
"""Decision measures from the same forecast and event-based supply ledger."""

from datetime import date
from decimal import ROUND_CEILING, Decimal


def measures(side):
    series, metrics = side["series"], side["metrics"]
    return {
        "peak_shortage": metrics.get("peak_supply_shortage", metrics["peak_shortage"]),
        "shortage_days": sum(
            p.get("daily_peak_supply_shortage", p["unmet_cumulative"]) > 1e-9 for p in series
        ),
        "ending_inventory": series[-1]["physical_inventory"],
        "new_demand": metrics["forecast_demand"],
        "known_orders_at_risk": metrics["known_orders_at_risk"],
        "demand_headroom_percent": metrics.get("demand_headroom_percent"),
    }


def assess(reference, stressed, proposed, evaluate, method, additional):
    original, before, after = map(measures, (reference, stressed, proposed))
    # Three decimal places match the source quantity contracts, without inventing
    # supplier minimum order sizes or a service probability.
    quantity = float(
        Decimal(str(max(0, before["peak_shortage"] - 1e-9))).quantize(
            Decimal(".001"), rounding=ROUND_CEILING
        )
    )
    first = next(
        (
            p["day"]
            for p in stressed["series"]
            if p.get("daily_peak_supply_shortage", p["unmet_cumulative"]) > 1e-9
        ),
        None,
    )
    suggestion = evaluate(quantity, date.fromisoformat(first)) if first else stressed
    late = after["known_orders_at_risk"] or 0
    if before["peak_shortage"] <= 1e-9 and not (before["known_orders_at_risk"] or 0):
        verdict = "extra_without_risk_reduction" if additional > 0 else "no_extra_needed"
    elif after["peak_shortage"] <= 1e-9 and not late:
        verdict = "covered"
    elif after["peak_shortage"] < before["peak_shortage"] - 1e-9 or late < (
        before["known_orders_at_risk"] or 0
    ):
        verdict = "partial"
    else:
        verdict = "still_at_risk"
    return {
        "reference": original,
        "stressed": before,
        "proposed": after,
        "minimum_additional_quantity": quantity,
        "latest_arrival_date": first,
        "remaining_late_orders_after_suggestion": suggestion["metrics"]["known_orders_at_risk"],
        "verdict": verdict,
        "model_method": method,
    }
