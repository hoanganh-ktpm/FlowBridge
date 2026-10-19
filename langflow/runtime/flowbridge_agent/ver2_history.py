# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded history-evidence Data task: no child model, SQL or repeat prediction."""

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from flowbridge.supplier_assistant.prediction.history import HistoryReference, HistoryResponse
from flowbridge_agent.config import BackendConfig
from flowbridge_agent.tools import tool_result
from flowbridge_agent.ver2_coordination import Invocation, Reply, collect, stamp
from flowbridge_agent.ver2_data import DataBackend


class HistorySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str | None = Field(default=None, min_length=1, max_length=255)
    supplier: str | None = Field(default=None, min_length=1, max_length=255)
    product_id: UUID | None = None
    supplier_id: UUID | None = None
    reference: HistoryReference | None = None
    feature_asof: datetime | None = None
    expected_count: int | None = Field(default=None, ge=0, strict=True)
    page: int = Field(default=1, ge=1, le=10000, strict=True)
    page_size: int = Field(default=20, ge=1, le=20, strict=True)

    @model_validator(mode="after")
    def selectors(self):
        if self.reference:
            for key in ("product_id", "supplier_id"):
                if getattr(self, key) and getattr(self, key) != getattr(self.reference, key):
                    raise ValueError("HISTORY_SELECTOR_MISMATCH")
                setattr(self, key, getattr(self.reference, key))
        if not (self.product or self.product_id) or not (self.supplier or self.supplier_id):
            raise ValueError("HISTORY_IDENTITIES_REQUIRED")
        return self


async def history_reply(request, parent, *, client=None) -> dict:
    scope = Invocation(parent.run, "database", (*parent.path, "database"), request)

    def reply(status, **kw):
        return stamp(Reply(service="database", status=status, results=scope.receipts, **kw), scope)

    if parent.role != "central":
        return reply("error", warnings=["PROTOCOL_TASK_NOT_ALLOWED"])
    key = json.dumps(["history_samples", request.model_dump(mode="json")], sort_keys=True)
    if key in scope.run.replies:
        return scope.run.replies[key]
    if parent.run.a2a_calls >= parent.run.max_a2a:
        return reply("unavailable", warnings=["A2A_BUDGET_EXHAUSTED"])
    parent.run.a2a_calls += 1
    parent.delegations += 1
    try:
        selection = HistorySelection.model_validate(
            {k: v for k, v in request.known.items() if v is not None}
        )
    except ValidationError:
        return reply(
            "needs_input",
            missing=["product_and_supplier_or_valid_history_reference"],
            questions=[
                "Bạn muốn xem lịch sử cho sản phẩm và nhà cung cấp nào trong kết quả vừa chấm?"
            ],
            warnings=["HISTORY_SELECTION_INVALID"],
        )
    client = client or DataBackend(BackendConfig.from_local())
    selectors = {
        stem: getattr(selection, stem) or str(getattr(selection, stem + "_id"))
        for stem in ("product", "supplier")
    }
    identity = await client.resolve(**selectors)
    collect(scope, tool_result("catalog_resolve_entities", selectors, identity))
    if identity.get("status") != "success":
        state = identity.get("status")
        return reply(
            state
            if state in {"error", "ambiguous", "not_found"}
            else "needs_input"
            if identity.get("questions")
            else "partial",
            missing=identity.get("missing", []),
            questions=identity.get("questions", []),
            warnings=identity.get("warnings", []),
        )
    resolved = identity["resolved"]
    for stem in ("product", "supplier"):
        expected = getattr(selection, stem + "_id")
        if expected and str(expected) != resolved[stem + "_id"]:
            return reply(
                "needs_input",
                warnings=["HISTORY_SELECTOR_MISMATCH"],
                questions=[
                    "Tên/mã và định danh trong kết quả cũ chưa khớp; "
                    "bạn chọn lại sản phẩm hoặc NCC nhé."
                ],
            )
    body = {
        k: v
        for k, v in selection.model_dump(mode="json").items()
        if k not in {"product", "supplier"} and v is not None
    }
    body.update({stem + "_id": resolved[stem + "_id"] for stem in ("product", "supplier")})
    result = await client.request("POST", "/api/v1/ml/history", body=body)
    if result.get("status") == "error":
        collect(scope, tool_result("ml_feature_history", body, result))
        return reply("error", resolved=resolved, warnings=[result.get("code", "BACKEND_FAILED")])
    try:
        data = HistoryResponse.model_validate(result)
        reference = data.reference
        if (
            str(reference.product_id) != resolved["product_id"]
            or str(reference.supplier_id) != resolved["supplier_id"]
            or data.page != selection.page
            or data.page_size != selection.page_size
            or (
                selection.feature_asof is not None
                and reference.feature_asof != selection.feature_asof
            )
            or (
                selection.reference is not None
                and data.reference_matches is True
                and reference != selection.reference
            )
        ):
            raise ValueError("HISTORY_RESPONSE_MISMATCH")
    except (ValidationError, ValueError, TypeError):
        return reply("error", resolved=resolved, warnings=["INVALID_BACKEND_RESPONSE"])
    status = "partial" if data.status in {"partial", "changed"} else data.status
    collect(
        scope,
        tool_result("ml_feature_history", body, {**data.model_dump(mode="json"), "status": status}),
    )
    value = reply(status, resolved=resolved, warnings=data.warnings)
    scope.run.replies[key] = value
    return value
