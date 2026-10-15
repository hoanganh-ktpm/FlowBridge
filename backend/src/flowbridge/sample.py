# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. You may redistribute and modify it under
# GNU GPL version 3 or later, without any warranty. See LICENSE for full terms.

# Các hằng/bytes tổng hợp do nhóm tạo cho API mẫu; không có dữ liệu bên ngoài.

from dataclasses import dataclass

from fastapi import Request

SAMPLE_TIME = "2026-10-15T03:00:00Z"
APPLICATION_ID = "APP-SAMPLE"
PRINCIPAL_ID = "USER-SAMPLE"
TENANT_ID = "TENANT-SAMPLE"
RECORD_ID = "REC-SAMPLE"
FILE_ID = "FILE-SAMPLE"
PENDING_FILE_ID = "FILE-PENDING"
DEFINITION_ID = "DEF-SAMPLE"
DEFINITION_KEY = "sample_approval"
INSTANCE_ID = "WF-SAMPLE"
TASK_ID = "TASK-SAMPLE"
JOB_ID = "JOB-SAMPLE"
NOTIFICATION_ID = "NOTE-SAMPLE"
COLLECTION = "sample_items"
FILE_BYTES = b"FlowBridge sample document. Synthetic approval data.\n"


@dataclass(frozen=True, slots=True)
class SampleContext:
    # Cố định ở phía server; không suy ra principal/tenant từ request header/body.
    principal_id: str = PRINCIPAL_ID
    tenant_id: str = TENANT_ID
    application_id: str = APPLICATION_ID


def sample_context(request: Request) -> SampleContext:
    return request.app.state.sample_context
