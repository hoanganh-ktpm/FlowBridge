# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO dữ liệu: record/schema/transaction/history/ingestion theo hợp đồng.

from typing import Annotated, Literal

from pydantic import Field

from flowbridge.shared.schemas import (
    DTO,
    CollectionName,
    Id,
    JsonObject,
    Timestamp,
    Version,
)


class Record(DTO):
    # Một bản ghi mẫu kèm version và thời gian cố định.
    id: Id
    version: Version
    data: JsonObject
    created_at: Timestamp
    updated_at: Timestamp


class RecordCreate(DTO):
    # Payload tạo record, chỉ gồm data JSON hợp schema.
    data: JsonObject


class RecordPatch(RecordCreate):
    # Payload cập nhật, bắt buộc kèm expected_version để kiểm xung đột.
    expected_version: Version


class CollectionDefinition(DTO):
    # Định nghĩa collection mẫu và JSON schema của record.
    collection: CollectionName
    application_id: Id
    schema_version: Version
    record_schema: JsonObject
    policy_key: Id


class TransactionCreate(DTO):
    # Một thao tác tạo trong transaction mẫu.
    operation: Literal["create"]
    collection: CollectionName
    record_id: Id | None = None
    data: JsonObject


class TransactionUpdate(DTO):
    # Một thao tác cập nhật trong transaction mẫu.
    operation: Literal["update"]
    collection: CollectionName
    record_id: Id
    expected_version: Version
    data: JsonObject


class TransactionRequest(DTO):
    # Danh sách 1-50 thao tác, phân biệt create/update theo operation.
    operations: Annotated[
        list[Annotated[TransactionCreate | TransactionUpdate, Field(discriminator="operation")]],
        Field(min_length=1, max_length=50),
    ]


class TransactionResult(DTO):
    # Kết quả mẫu của transaction, chưa đảm bảo ghi bền.
    transaction_id: Id
    records: list[Record]
    committed_at: Timestamp


class RecordRevision(DTO):
    # Một revision trong lịch sử record mẫu.
    record_id: Id
    version: Version
    schema_version: Version
    operation: Literal["create", "update", "delete"]
    changed_at: Timestamp
    data: JsonObject | None


class RecordHistoryPage(DTO):
    # Trang lịch sử mẫu, phân trang bằng before_version.
    items: Annotated[list[RecordRevision], Field(max_length=100)]
    next_before_version: Version | None


class IngestionRequest(DTO):
    # Yêu cầu ingestion mẫu cho nguồn IT/OT đã đăng ký.
    collection: CollectionName
    source_kind: Literal["IT", "OT"]
    source_id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    observed_at: Timestamp
    schema_version: Version
    records: Annotated[list[JsonObject], Field(min_length=1, max_length=1000)]


class IngestionResult(DTO):
    # Kết quả tổng hợp mẫu của batch ingestion.
    collection: CollectionName
    schema_version: Version
    source_kind: Literal["IT", "OT"]
    source_id: str
    observed_at: Timestamp
    received: Annotated[int, Field(ge=1, le=1000)]
    accepted: Annotated[int, Field(ge=1, le=1000)]
    rejected: Literal[0]
    lineage_id: Id
    committed_at: Timestamp


class PlatformJob(DTO):
    # Trạng thái job mẫu cho ingestion/archive nền.
    job_id: Id
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: Timestamp
    result: IngestionResult | None
    error: JsonObject | None
