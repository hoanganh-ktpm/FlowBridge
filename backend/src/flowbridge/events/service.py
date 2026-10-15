# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Service sự kiện mẫu: không ghi outbox/broker bền.

from flowbridge.events.adapter import SampleEventsAdapter
from flowbridge.events.schemas import AuditEntry, Event, EventAccepted, Notification
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.shared.schemas import Page, paginate


class EventsService:
    # Use case sự kiện mẫu trên fixture cố định.
    def __init__(self, adapter: SampleEventsAdapter) -> None:
        self.adapter = adapter

    def publish(self, payload: Event) -> EventAccepted:
        # Chấp nhận event mà không phát ra broker thật.
        return EventAccepted(event_id=payload.event_id, accepted=True)

    def notifications(self, page: int, page_size: int) -> Page[Notification]:
        # Phân trang notification mẫu duy nhất.
        return paginate(self.adapter.notifications(), page, page_size)

    def read(self, notification_id: str) -> Notification:
        # Đánh dấu đã đọc trên bản sao, sai ID trả 404.
        for notification in self.adapter.notifications():
            if notification.id == notification_id:
                return notification.model_copy(update={"read": True}, deep=True)
        raise CoreError(ErrorCode.NOT_FOUND)

    def audit(self, page: int, page_size: int, resource_id: str | None) -> Page[AuditEntry]:
        # Lọc audit mẫu theo resource rồi phân trang.
        items = self.adapter.audit()
        if resource_id is not None:
            items = [item for item in items if item.resource_id == resource_id]
        return paginate(items, page, page_size)
