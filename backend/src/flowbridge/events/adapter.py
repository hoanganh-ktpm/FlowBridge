# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Adapter sự kiện mẫu: một notification và một audit cố định.

from flowbridge.events.schemas import AuditEntry, Notification
from flowbridge.sample import NOTIFICATION_ID, PRINCIPAL_ID, RECORD_ID, SAMPLE_TIME


class SampleEventsAdapter:
    # Fixture sự kiện tổng hợp, không đọc broker/outbox thật.
    def notifications(self) -> list[Notification]:
        # Một notification mẫu chưa đọc cho sample item.
        return [
            Notification(
                id=NOTIFICATION_ID,
                title="Sample approval",
                message="Synthetic sample item is ready for review.",
                resource_type="sample_item",
                resource_id=RECORD_ID,
                read=False,
                created_at=SAMPLE_TIME,
            )
        ]

    def audit(self) -> list[AuditEntry]:
        # Một dòng audit mẫu gắn correlation cố định.
        return [
            AuditEntry(
                id="AUDIT-SAMPLE",
                actor_id=PRINCIPAL_ID,
                action="sample.created",
                resource_type="sample_item",
                resource_id=RECORD_ID,
                correlation_id="CORR-SAMPLE",
                occurred_at=SAMPLE_TIME,
            )
        ]
