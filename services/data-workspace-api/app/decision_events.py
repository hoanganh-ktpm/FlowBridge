# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict owner-event summaries obtained only through fixed Core read actions."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator

from .schemas import DTO


class OwnerEvent(DTO):
    id: UUID
    org_id: UUID
    warehouse_id: UUID
    producer: Literal["sales", "warehouse", "logistics"]
    event_type: str = Field(pattern=r"^(sales|warehouse|logistics)\.[a-z_]{1,70}$")
    schema_version: int = Field(ge=1, le=1, strict=True)
    aggregate_type: str = Field(min_length=1, max_length=80)
    aggregate_id: UUID
    aggregate_version: int = Field(ge=1, strict=True)
    correlation_id: UUID
    occurred_at: datetime
    source_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("occurred_at")
    @classmethod
    def timezone(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Event timestamp requires timezone")
        return value


def validate_event(raw, producer, org_id, warehouse_id):
    event = OwnerEvent.model_validate(raw)
    # Hash the wire representation: ISO8601 equivalent spellings must not alter
    # the owner-supplied checksum while decoding/re-encoding a datetime.
    summary = {key: value for key, value in raw.items() if key != "summary_hash"}
    hashed = hashlib.sha256(
        json.dumps(
            summary, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=False
        ).encode()
    ).hexdigest()
    if (
        event.producer != producer
        or event.org_id != org_id
        or event.warehouse_id != warehouse_id
        or not event.event_type.startswith(producer + ".")
        or hashed != event.summary_hash
    ):
        raise HTTPException(502, "Owner event summary scope or hash mismatch")
    return dict(raw)
