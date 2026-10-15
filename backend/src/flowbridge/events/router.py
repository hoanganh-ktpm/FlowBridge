# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Router sự kiện: publish/notification/audit mẫu theo hợp đồng.

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from flowbridge.events.schemas import AuditEntry, Event, EventAccepted, Notification
from flowbridge.events.service import EventsService
from flowbridge.shared.http import PageNumber, PageSize, WriteKey
from flowbridge.shared.schemas import Id, Page

router = APIRouter(tags=["Events"])


def get_service(request: Request) -> EventsService:
    # Lấy service sự kiện mẫu đã inject ở lifespan.
    return request.app.state.events_service


Service = Annotated[EventsService, Depends(get_service)]


@router.post("/internal/v1/core/events", operation_id="publishEvent", status_code=202)
async def publish_event(payload: Event, service: Service, _key: WriteKey) -> EventAccepted:
    # Nhận event mẫu, trả chấp nhận 202 mà không qua broker.
    return service.publish(payload)


@router.get("/api/v1/core/notifications", operation_id="listNotifications")
async def list_notifications(
    service: Service, page: PageNumber = 1, page_size: PageSize = 20
) -> Page[Notification]:
    # Liệt kê notification mẫu có phân trang.
    return service.notifications(page, page_size)


@router.post(
    "/api/v1/core/notifications/{notification_id}/read", operation_id="markNotificationRead"
)
async def mark_notification_read(
    notification_id: Id, service: Service, _key: WriteKey
) -> Notification:
    # Đánh dấu notification mẫu đã đọc, sai ID trả 404.
    return service.read(notification_id)


@router.get("/api/v1/core/audit-entries", operation_id="listAuditEntries", tags=["Audit"])
async def list_audit_entries(
    service: Service,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    resource_id: Id | None = None,
) -> Page[AuditEntry]:
    # Liệt kê audit mẫu, lọc theo resource_id khi có.
    return service.audit(page, page_size, resource_id)
