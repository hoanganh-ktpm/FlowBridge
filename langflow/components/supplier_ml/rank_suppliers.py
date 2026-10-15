# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Paste into a Langflow Custom Component or register LANGFLOW_COMPONENTS_PATH.
# This component only calls read-only ML APIs. Tokens and URL are operator inputs.
import json
from uuid import UUID

import httpx
from lfx.custom import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data


class RankSupplierML(Component):
    display_name = "FlowBridge - Rank Suppliers"
    description = "Find eligible suppliers and compare their ML delivery risk."
    icon = "ListOrdered"
    name = "FlowBridgeRankSupplierML"
    inputs = [
        MessageTextInput(
            name="api_origin",
            display_name="API origin",
            value="http://127.0.0.1:8000",
            advanced=True,
        ),
        SecretStrInput(name="access_token", display_name="Delegated bearer token", required=True),
        MessageTextInput(
            name="product_id", display_name="Product UUID", required=True, tool_mode=True
        ),
        MessageTextInput(
            name="quantity", display_name="Quantity (decimal string)", required=True, tool_mode=True
        ),
        MessageTextInput(
            name="deadline",
            display_name="Required delivery date (YYYY-MM-DD)",
            required=True,
            tool_mode=True,
        ),
        IntInput(name="top_k", display_name="Top suppliers", value=3, tool_mode=True),
    ]
    outputs = [Output(name="result", display_name="Supplier ranking", method="rank_suppliers")]

    async def rank_suppliers(self) -> Data:
        origin = httpx.URL(str(self.api_origin))
        if (
            origin.scheme not in {"http", "https"}
            or not origin.host
            or origin.userinfo
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("Invalid configured API origin")
        token = self.access_token
        token = token.get_secret_value() if hasattr(token, "get_secret_value") else str(token)
        payload = {
            "product_id": str(UUID(str(self.product_id))),
            "quantity": str(self.quantity),
            "requested_delivery_date": str(self.deadline),
            "top_k": self.top_k,
        }
        async with httpx.AsyncClient(
            timeout=150, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                str(origin).rstrip("/") + "/api/v1/ml/suppliers/rank",
                json=payload,
                headers={"Authorization": "Bearer " + token},
            )
        if not response.is_success:
            try:
                code = response.json().get("code", "UPSTREAM_ERROR")
            except (ValueError, AttributeError):
                code = "UPSTREAM_ERROR"
            raise ValueError(f"ML ranking failed: HTTP {response.status_code}, {code}")
        result = response.json()
        self.status = json.dumps(
            {"status": result["status"], "candidates": len(result["candidates"])}
        )
        return Data(data=result)
