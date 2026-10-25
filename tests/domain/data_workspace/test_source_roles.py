# SPDX-License-Identifier: GPL-3.0-or-later
import json

import pytest
from workspace_app.reports import recipe
from workspace_app.source_roles import configured_source_names


@pytest.mark.parametrize(
    "overrides",
    [
        [],
        {"org_id": "other"},
        {"demand": ""},
        {"demand": 123},
        {"demand": "warehouse.stock_availability"},
    ],
)
def test_invalid_semantic_source_bindings_fail_closed(monkeypatch, overrides):
    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_SOURCE_NAMES", json.dumps(overrides))
    with pytest.raises(ValueError):
        configured_source_names()


def test_history_alias_preserves_the_sales_recipe(monkeypatch):
    monkeypatch.setenv("FLOWBRIDGE_WORKSPACE_SOURCE_NAMES", '{"sales_history":"acme.order_daily"}')
    names = configured_source_names()
    assert recipe({"name": "acme.order_daily"}, names)[1:] == ("sku", "confirmed_quantity", "EA")
