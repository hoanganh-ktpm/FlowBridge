# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover catalog presentation from verified API receipts, never model-authored rows."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from flowbridge_agent.tools import ToolResult
from flowbridge_agent.ver2_data import CatalogInput, valid_identities


class CatalogPage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["success", "partial", "not_found"]
    resource: Literal["products", "suppliers"]
    items: list[dict] = Field(max_length=20)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=20)
    total_matches: int | None = Field(ge=0)
    matched_count_at_least: int = Field(ge=0)
    has_more: bool
    next_page: int | None = Field(ge=2)
    truncated: bool
    warnings: list[str] = Field(max_length=19)

    @model_validator(mode="after")
    def consistent_page(self):
        if not valid_identities(self.items, self.resource):
            raise ValueError("INVALID_CATALOG_IDENTITIES")
        if self.truncated:
            if self.total_matches is not None:
                raise ValueError("TRUNCATED_TOTAL_MUST_BE_UNKNOWN")
        elif self.total_matches != self.matched_count_at_least:
            raise ValueError("INVALID_CATALOG_TOTAL")
        count = self.matched_count_at_least
        expected_rows = max(0, min(self.page_size, count - (self.page - 1) * self.page_size))
        more = self.page * self.page_size < count
        status = "partial" if self.truncated else "success" if self.items else "not_found"
        if (
            len(self.items) != expected_rows
            or self.has_more != more
            or self.next_page != (self.page + 1 if more else None)
            or self.status != status
        ):
            raise ValueError("INVALID_CATALOG_PAGE")
        return self


def catalog_receipt_reply(raw, scope) -> dict | None:
    if (
        scope.role != "database"
        or scope.request is None
        or scope.request.task != "list_catalog"
        or scope.blocked
        or scope.child_replies
        or len(scope.receipts) != 1
        or isinstance(raw, dict)
        and raw.get("service") not in {None, "database"}
    ):
        return None
    try:
        receipt = scope.receipts[0]
        encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False)
        if encoded not in scope.run.evidence:
            return None
        result = ToolResult.model_validate(receipt)
        if result.tool != "catalog_list" or result.error is not None:
            return None
        args = CatalogInput.model_validate(result.input)
        known = {key: value for key, value in scope.request.known.items() if value is not None}
        if known.get("resource") != args.resource:
            return None
        expected = CatalogInput.model_validate({**args.model_dump(), **known})
        page = CatalogPage.model_validate(result.data)
        if (
            expected != args
            or page.resource != args.resource
            or page.page != args.page
            or page.page_size != args.page_size
            or result.status != page.status
        ):
            return None
        warnings = list(dict.fromkeys(page.warnings))
        if page.has_more:
            warnings.append("CATALOG_PAGE_HAS_MORE")
        return {
            "service": "database",
            "status": "partial" if page.truncated or page.has_more else page.status,
            "results": [result.model_dump(mode="json")],
            "warnings": warnings,
        }
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
        return None
