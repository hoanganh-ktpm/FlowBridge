# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Assignment(Strict):
    org_id: UUID
    warehouse_ids: list[UUID] = Field(min_length=1, max_length=20)
    roles: list[Literal["coordinator", "warehouse", "logistics", "manager"]] = Field(
        min_length=1, max_length=4
    )


class FreshCheck(Strict):
    revision_id: UUID
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    checked_at: datetime
    condition_status: Literal["VALID", "STALE"] = "VALID"

    @field_validator("checked_at")
    @classmethod
    def timezone(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Timezone required")
        return value


class DecisionStart(Strict):
    org_id: UUID
    warehouse_id: UUID
    order_id: UUID
    case_id: UUID
    revision_id: UUID
    revision_number: int = Field(ge=1, strict=True)
    participants: dict[Literal["coordinator", "warehouse", "logistics", "manager"], UUID]
    check: FreshCheck
    order_source_id: UUID
    order_contract_id: UUID


class DecisionTaskCommand(Strict):
    actor_principal_id: UUID
    outcome: Literal["confirmed", "changes_required", "approved", "rejected", "acknowledged"]
    check: FreshCheck
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def return_reason(self):
        if self.outcome in {"changes_required", "rejected"} and not (self.note or "").strip():
            raise ValueError("State the reason for returning or rejecting the proposal")
        return self
