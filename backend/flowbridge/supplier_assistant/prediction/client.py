# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

from uuid import UUID

import httpx

from .schemas import OrderRisk, RankRequest, RankResponse, ScenarioRequest, ScenarioResponse


class MLClientError(Exception):
    def __init__(self, status_code: int, code: str, request_id: str | None) -> None:
        self.status_code, self.code, self.request_id = status_code, code, request_id
        super().__init__(f"ML API failed: HTTP {status_code}, {code}")


class SupplierMLClient:
    # Caller owns the HTTP pool and credential refresh. No automatic replay of writes.
    def __init__(self, http: httpx.AsyncClient, base_url: str, access_token: str) -> None:
        self.http = http
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Use a configured HTTP origin without credentials or a path")
        self.base_url = str(url).rstrip("/")
        self._token = access_token

    async def request(
        self, method: str, path: str, *, body: dict | None = None, key: str | None = None
    ) -> dict:
        headers = {"Authorization": "Bearer " + self._token}
        if key is not None:
            headers["Idempotency-Key"] = key
        response = await self.http.request(
            method,
            self.base_url + path,
            json=body,
            headers=headers,
            follow_redirects=False,
            timeout=httpx.Timeout(150, connect=10),
        )
        if not response.is_success:
            try:
                error = response.json()
                code = error.get("code", "UPSTREAM_ERROR")
                correlation = error.get("request_id")
            except (ValueError, AttributeError):
                code, correlation = "UPSTREAM_ERROR", None
            # Do not include request headers/body or arbitrary upstream messages.
            raise MLClientError(response.status_code, str(code), correlation)
        return response.json()

    async def predict(self, scenario: ScenarioRequest) -> ScenarioResponse:
        return ScenarioResponse.model_validate(
            await self.request(
                "POST",
                "/api/v1/ml/scenarios/predict",
                body=scenario.model_dump(mode="json"),
            )
        )

    async def rank(self, scenario: RankRequest) -> RankResponse:
        return RankResponse.model_validate(
            await self.request(
                "POST",
                "/api/v1/ml/suppliers/rank",
                body=scenario.model_dump(mode="json"),
            )
        )

    async def predict_order(self, order_id: UUID, idempotency_key: str) -> OrderRisk:
        return OrderRisk.model_validate(
            await self.request(
                "POST",
                f"/internal/v1/ml/purchase-orders/{order_id}/predict",
                key=idempotency_key,
            )
        )

    async def order_risk(self, order_id: UUID) -> OrderRisk:
        return OrderRisk.model_validate(
            await self.request(
                "GET",
                f"/api/v1/ml/purchase-orders/{order_id}/risk",
            )
        )
