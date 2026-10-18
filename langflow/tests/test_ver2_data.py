# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runtime"), str(ROOT.parent / "backend")]

from flowbridge_agent.config import BackendConfig  # noqa: E402
from flowbridge_agent.ver2_data import DataBackend  # noqa: E402
from flowbridge_agent.ver2_data_nodes import (  # noqa: E402
    BrowseCatalogBase,
    FindPurchaseOrderBase,
    FindPurchaseRequestBase,
    ReadOffersBase,
    ResolveEntitiesBase,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,node_type,resource,key,code",
    [
        (
            "purchase_get_order",
            FindPurchaseOrderBase,
            "purchase-orders",
            "purchase_order_id",
            "po_number",
        ),
        (
            "purchase_get_request",
            FindPurchaseRequestBase,
            "purchase-requests",
            "request_id",
            "request_code",
        ),
    ],
)
async def test_purchase_ambiguity_uses_visible_rows_and_reads_detail_only_after_selection(
    action,
    node_type,
    resource,
    key,
    code,
):
    calls = []
    rows = [
        {key: PRODUCT, code: "DEMO-001", "status": "approved", "notes": "vật tư tháng 10"},
        {key: OTHER, code: "DEMO-002", "status": "approved", "notes": "vật tư tháng 11"},
    ]

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/" + resource:
            return httpx.Response(200, json={"items": rows, "total": 2})
        assert request.url.path == "/api/v1/" + resource + "/" + PRODUCT
        return httpx.Response(200, json={**rows[0], "items": []})

    api = DataBackend(BackendConfig(user_token="scoped-user"), httpx.MockTransport(handle))
    ambiguous = await api.execute(action, {"identifier": "DEMO"})
    assert ambiguous["status"] == "ambiguous" and len(ambiguous["items"]) == 2
    assert len(calls) == 1 and key not in ambiguous
    detail = await api.execute(action, {"identifier": "vật tư tháng 10"})
    assert detail[key] == PRODUCT
    assert calls[-1].endswith(PRODUCT)
    schema = convert_to_openai_tool(node_type().make_tool(), strict=True)
    assert "identifier" in schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_incomplete_purchase_scan_never_selects_a_single_apparent_match():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "items": [
                    {"purchase_order_id": PRODUCT, "po_number": "PO-001", "status": "issued"}
                ],
                "total": 500,
            },
        )

    api = DataBackend(BackendConfig(user_token="test", max_pages=1), httpx.MockTransport(handle))
    result = await api.execute("purchase_get_order", {"identifier": "PO-001"})
    assert result["status"] == "partial" and result["truncated"]
    assert calls == ["/api/v1/purchase-orders"]


PRODUCT = "024b8d0d-3548-54ad-8733-8385905fdbe1"
OTHER = "00000001-0001-4001-8001-000000000001"
SUPPLIER = "00000002-0002-4002-8002-000000000002"
PRODUCTS = [
    dict(
        product_id=PRODUCT,
        product_code="DEMO-P03",
        product_name="Băng keo Scotch 371 vàng, 48 mm × 100 m",
        unit="cuộn",
    ),
    dict(
        product_id=OTHER,
        product_code="DEMO-P19",
        product_name="Băng keo Scotch 371 trong, 48 mm × 100 m",
        unit="cuộn",
    ),
]
SUPPLIERS = [
    dict(
        supplier_id=SUPPLIER,
        supplier_code="DEMO-S06",
        supplier_name="Vật tư Phúc Lâm — NCC-06",
        city="HCM",
        status="active",
    )
]


def backend(*, incomplete=False, denied=False):
    calls = []

    def handle(request):
        calls.append(request)
        if denied:
            return httpx.Response(403, json={"secret": "must-not-leak"})
        resources = {
            "products": PRODUCTS,
            "suppliers": SUPPLIERS,
            "supplier-products": [
                dict(
                    product_id=PRODUCT,
                    supplier_id=SUPPLIER,
                    current_unit_price="25000",
                    currency="VND",
                    min_order_qty="10",
                    quoted_lead_days=2,
                    is_available=True,
                )
            ],
        }
        rows = resources[request.url.path.rsplit("/", 1)[-1]]
        page = int(request.url.params.get("page", 1))
        size = int(request.url.params.get("page_size", 100))
        return httpx.Response(
            200,
            json={
                "items": rows[(page - 1) * size : page * size],
                "total": 1000 if incomplete else len(rows),
            },
        )

    return DataBackend(
        BackendConfig(user_token="offline-test", max_pages=1), httpx.MockTransport(handle)
    ), calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "product",
    [
        "DEMO-P03",
        "demo p03",
        PRODUCT,
        "băng keo Scotch 371 vàng",
        "bang keo vang 48mm x100m",
    ],
)
@pytest.mark.parametrize("supplier", ["Nhà vật tư Phúc Lâm", "phuc lam", "NCC-06", "DEMO-S06"])
async def test_real_user_selectors_resolve_both_ids_in_one_tool(product, supplier):
    api, calls = backend()
    result = await api.execute("catalog_resolve_entities", dict(product=product, supplier=supplier))
    assert result["status"] == "success"
    assert result["resolved"]["product_id"] == PRODUCT
    assert result["resolved"]["supplier_id"] == SUPPLIER
    assert result["resolved"]["unit"] == "cuộn"
    assert len(calls) == 2
    assert all(r.method == "GET" for r in calls)


@pytest.mark.asyncio
async def test_color_ambiguity_keeps_supplier_without_choosing_first_product():
    api, _ = backend()
    result = await api.execute(
        "catalog_resolve_entities",
        {
            "product": "băng keo",
            "supplier": "Phúc Lâm",
        },
    )
    assert result["status"] == "partial"
    assert "product_id" not in result["resolved"]
    assert result["resolved"]["supplier_id"] == SUPPLIER
    assert result["entities"]["product"]["status"] == "ambiguous"
    assert len(result["entities"]["product"]["items"]) == 2
    assert result["missing"] == ["product"]
    assert "UUID" not in " ".join(result["questions"])


@pytest.mark.asyncio
async def test_misspelling_or_wrong_spec_only_suggests_and_never_resolves():
    api, _ = backend()
    for query in ("băng keo Scotch 371 vàng 50mm", "bang keo Scoth 371 vang"):
        result = await api.execute("catalog_resolve_entities", {"product": query})
        assert "product_id" not in result["resolved"]
        assert result["entities"]["product"]["suggestions"]


@pytest.mark.asyncio
async def test_incomplete_scan_never_claims_unique_or_complete_total():
    api, _ = backend(incomplete=True)
    result = await api.execute("catalog_resolve_entities", {"product": "DEMO-P03"})
    assert result["truncated"] and not result["resolved"]
    listed = await api.execute("catalog_list", {"resource": "products", "query": "băng keo"})
    assert listed["status"] == "partial" and listed["total_matches"] is None


@pytest.mark.asyncio
async def test_list_without_keyword_uses_core_pagination_not_full_scan():
    api, calls = backend()
    result = await api.execute("catalog_list", {"resource": "products", "page_size": 1})
    assert result["items"][0]["product_id"] == PRODUCT
    assert result["total_matches"] == 2 and result["next_page"] == 2
    assert not result["truncated"] and len(calls) == 1
    assert calls[0].url.params["page_size"] == "1"
    page2 = await api.execute("catalog_list", {"resource": "products", "page_size": 1, "page": 2})
    assert page2["items"][0]["product_id"] == OTHER
    assert not page2["has_more"] and page2["next_page"] is None


@pytest.mark.asyncio
async def test_quotes_take_names_and_return_both_identity_and_price_without_duplicate_reads():
    api, calls = backend()
    result = await api.execute(
        "catalog_read_offers",
        {
            "product": "băng keo vàng",
            "supplier": "Nhà vật tư Phúc Lâm",
        },
    )
    assert result["status"] == "success"
    assert result["resolved"]["product_id"] == PRODUCT
    assert result["items"][0]["supplier_name"] == SUPPLIERS[0]["supplier_name"]
    assert result["items"][0]["currency"] == "VND"
    assert len(calls) == 3
    assert len({r.url.path for r in calls}) == 3


@pytest.mark.asyncio
async def test_ambiguous_quote_never_reads_offers_or_chooses_color():
    api, calls = backend()
    result = await api.execute("catalog_read_offers", {"product": "băng keo"})
    assert result["status"] == "ambiguous"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_auth_error_is_not_retried_or_exposed():
    api, calls = backend(denied=True)
    result = await api.execute("catalog_resolve_entities", {"product": "DEMO-P03"})
    assert result["http_status"] == 403 and "must-not-leak" not in str(result)
    await api.execute("catalog_resolve_entities", {"product": "DEMO-P03"})
    assert len(calls) == 1


def test_native_tool_schema_has_simple_selectors_and_no_credentials():
    for cls in (ResolveEntitiesBase, BrowseCatalogBase, ReadOffersBase):
        tool = cls().make_tool()
        spec = convert_to_openai_tool(tool, strict=True)["function"]
        assert spec["strict"]
        params = spec["parameters"]
        assert set(params["required"]) == set(params["properties"])
        assert not set(params["properties"]) & {
            "api_origin",
            "access_token",
            "product_id",
            "supplier_id",
        }
        cls().schema_json()


@pytest.mark.asyncio
async def test_defaults_clear_and_locks_do_not_replace_user_selection():
    node = ResolveEntitiesBase(product="DEMO-P03", supplier="DEMO-S06")
    tool = node.make_tool()
    api, calls = backend()
    tool.client = api
    result = await tool._arun(product="DEMO-P19", supplier=None)
    assert result["input"]["product"] == "DEMO-P19"
    result = await tool._arun(product="DEMO-P03", supplier=None, clear_defaults=["supplier"])
    assert result["input"]["supplier"] is None
    locked = ResolveEntitiesBase(product="DEMO-P03", lock_inputs=True).make_tool()
    locked.client = api
    assert "product" not in locked.args_schema.model_fields
    result = await locked._arun(product="DEMO-P19")
    assert result["status"] == "error"
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [{}, {"product": "  "}, {"product": "DEMO-P03", "role": "ADMIN"}])
async def test_invalid_selectors_never_call_network(args):
    api, calls = backend()
    assert (await api.execute("catalog_resolve_entities", args))["status"] == "error"
    assert not calls


@pytest.mark.asyncio
async def test_malformed_backend_identity_is_not_forwarded_to_ml():
    def handle(request):
        return httpx.Response(
            200, json={"items": [{**PRODUCTS[0], "product_id": "DEMO-P03"}], "total": 1}
        )

    api = DataBackend(BackendConfig(user_token="test"), httpx.MockTransport(handle))
    result = await api.execute("catalog_resolve_entities", {"product": "DEMO-P03"})
    assert result["code"] == "INVALID_BACKEND_RESPONSE"


def test_migration_preserves_native_nodes_model_settings_and_unrelated_edges():
    import json
    from copy import deepcopy

    from setup_ver2 import connect, custom_class, node
    from upgrade_ver2_data import REPLACEMENTS, central_graph, data_graph

    original = json.loads((ROOT / "flows/ver2/database.json").read_text(encoding="utf-8"))["data"]
    # Use real old tool templates even after the checked-in exports have been upgraded.
    agent = next(n for n in original["nodes"] if n["data"]["type"] == "Agent")
    old_files = ["find_products.py", "find_suppliers.py", "supplier_offers.py"]
    new_kinds = {new for _, _, new in REPLACEMENTS}
    old_tools = []
    for (old_kind, _, _), filename in zip(REPLACEMENTS, old_files, strict=True):
        cls, code = custom_class(filename, old_kind)
        old_tools.append(node(cls, code, old_kind + "-v2-database", 0, 1000))
    replaced_ids = {n["id"] for n in original["nodes"] if n["data"]["type"] in new_kinds}
    original["nodes"] = [n for n in original["nodes"] if n["id"] not in replaced_ids] + old_tools
    original["edges"] = [e for e in original["edges"] if e["source"] not in replaced_ids]
    original["edges"].extend(connect(n, "tools", agent, "tools") for n in old_tools)
    before = deepcopy(original)
    after = data_graph(original)
    assert original == before
    assert len(after["nodes"]) == len(original["nodes"])
    assert len(after["edges"]) == len(original["edges"])
    native = [
        n
        for n in before["nodes"]
        if n["data"]["type"]
        not in {
            "FlowBridgeFindProducts",
            "FlowBridgeFindSuppliers",
            "FlowBridgeSupplierOffers",
        }
    ]
    for n in native:
        actual = next(a for a in after["nodes"] if a["id"] == n["id"])
        if n["data"]["type"] != "Agent":
            assert actual == n
        else:
            for key in ("model", "api_key", "max_tokens", "openai_reasoning_effort"):
                assert actual["data"]["node"]["template"][key] == n["data"]["node"]["template"][key]
    assert data_graph(after) == after  # Reapplying keeps edited presets and graph intact.
    central = json.loads((ROOT / "flows/ver2/central.json").read_text(encoding="utf-8"))["data"]
    updated = central_graph(central)
    assert updated["edges"] == central["edges"]
    assert [n["id"] for n in updated["nodes"]] == [n["id"] for n in central["nodes"]]
