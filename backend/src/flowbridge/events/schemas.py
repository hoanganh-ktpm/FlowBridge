# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO sự kiện: event/notification/audit mẫu theo hợp đồng.

from typing import Literal

from flowbridge.shared.schemas import DTO, Id, JsonObject, Timestamp, Version


class Event(DTO):
    # Sự kiện nghiệp vụ mẫu kèm aggregate và correlation.
    event_id: Id
    event_type: str
    schema_version: str
    occurred_at: Timestamp
    tenant_id: Id
    producer: str
    correlation_id: Id
    aggregate_type: str
    aggregate_id: Id
    aggregate_version: Version
    data: JsonObject


class EventAccepted(DTO):
    # Xác nhận mẫu đã nhận event vào hàng đợi.
    event_id: Id
    accepted: Literal[True]


class Notification(DTO):
    # Thông báo mẫu cho đúng principal/tenant.
    id: Id
    title: str
    message: str
    resource_type: str
    resource_id: Id
    read: bool
    created_at: Timestamp


class AuditEntry(DTO):
    # Dòng audit mẫu truy vết theo correlation.
    id: Id
    actor_id: Id
    action: str
    resource_type: str
    resource_id: Id
    correlation_id: Id
    occurred_at: Timestamp
