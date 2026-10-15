# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Service dữ liệu: kiểm tra collection/version/schema trên fixture mẫu.

import json

from jsonschema import Draft202012Validator, SchemaError

from flowbridge.data.adapter import SampleDataAdapter
from flowbridge.data.schemas import (
    CollectionDefinition,
    IngestionRequest,
    PlatformJob,
    Record,
    RecordHistoryPage,
    RecordPatch,
    TransactionRequest,
    TransactionResult,
    TransactionUpdate,
)
from flowbridge.sample import JOB_ID, SAMPLE_TIME
from flowbridge.shared.contract import validate_dto, validate_schema
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.shared.schemas import JsonObject, Page, paginate


class DataService:
    # Use case dữ liệu mẫu, không commit transaction database thật.
    def __init__(self, adapter: SampleDataAdapter) -> None:
        self.adapter = adapter

    def collection(self, collection: str) -> CollectionDefinition:
        # Chặn collection lạ bằng 404, không lộ danh sách thật.
        definition = self.adapter.collection()
        if collection != definition.collection:
            raise CoreError(ErrorCode.NOT_FOUND)
        return definition

    def register(self, definition: CollectionDefinition) -> CollectionDefinition:
        # Chỉ kiểm tra JSON schema hợp lệ rồi phản chiếu DTO.
        try:
            Draft202012Validator.check_schema(definition.record_schema)
        except SchemaError:
            raise CoreError(ErrorCode.VALIDATION_ERROR) from None
        return definition.model_copy(deep=True)

    def record(self, collection: str, record_id: str) -> Record:
        # Tìm record mẫu theo ID trong fixture, sai trả 404.
        self.collection(collection)
        for record in self.adapter.records():
            if record.id == record_id:
                return record
        raise CoreError(ErrorCode.NOT_FOUND)

    def list_records(
        self, collection: str, page: int, page_size: int, filters: str | None
    ) -> Page[Record]:
        # Lọc allowlist eq/in/contains trên field name/status rồi phân trang.
        self.collection(collection)
        records = self.adapter.records()
        if filters is not None:
            try:
                conditions = json.loads(filters)
            except (ValueError, TypeError):
                raise CoreError(ErrorCode.VALIDATION_ERROR) from None
            if not isinstance(conditions, list) or len(conditions) > 20:
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            for condition in conditions:
                validate_dto(condition, "Filter")
                field, op, value = condition["field"], condition["op"], condition["value"]
                if field not in {"name", "status"}:
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
                if op == "eq":
                    records = [record for record in records if record.data[field] == value]
                elif op == "in":
                    records = [record for record in records if record.data[field] in value]
                elif op == "contains":
                    records = [record for record in records if value in str(record.data[field])]
                else:
                    # Fixture hiện chỉ có string thường, không hỗ trợ gte/lte theo ngày/số.
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
        return paginate(records, page, page_size)

    def create(self, collection: str, data: JsonObject, record_id: str = "REC-CREATED") -> Record:
        # Kiểm tra data theo schema rồi trả record mẫu version 1.
        definition = self.collection(collection)
        validate_schema(data, definition.record_schema)
        return Record(
            id=record_id, version=1, data=data, created_at=SAMPLE_TIME, updated_at=SAMPLE_TIME
        )

    def update(self, collection: str, record_id: str, payload: RecordPatch) -> Record:
        # Gộp data mới, kiểm version và schema rồi tăng version mẫu.
        record = self.record(collection, record_id)
        self.check_version(record, payload.expected_version)
        updated = {**record.data, **payload.data}
        validate_schema(updated, self.collection(collection).record_schema)
        return record.model_copy(update={"data": updated, "version": record.version + 1}, deep=True)

    def check_version(self, record: Record, expected_version: int) -> None:
        # Chặn cập nhật cạnh tranh bằng 409 khi version lệch.
        if record.version != expected_version:
            raise CoreError(ErrorCode.VERSION_CONFLICT)

    def delete(self, collection: str, record_id: str, expected_version: int) -> None:
        # Xóa mẫu chỉ kiểm version, không xóa dữ liệu bền.
        self.check_version(self.record(collection, record_id), expected_version)

    def transaction(self, payload: TransactionRequest) -> TransactionResult:
        # Thực thi tuần tự create/update mẫu, trả ID transaction cố định.
        validate_dto(payload.model_dump(mode="json", exclude_unset=True), "TransactionRequest")
        records = []
        for index, operation in enumerate(payload.operations):
            if isinstance(operation, TransactionUpdate):
                record = self.update(
                    operation.collection,
                    operation.record_id,
                    RecordPatch(data=operation.data, expected_version=operation.expected_version),
                )
            else:
                record = self.create(
                    operation.collection,
                    operation.data,
                    operation.record_id or f"REC-CREATED-{index}",
                )
            records.append(record)
        return TransactionResult(
            transaction_id="TX-SAMPLE", records=records, committed_at=SAMPLE_TIME
        )

    def history(
        self, collection: str, record_id: str, page_size: int, before_version: int | None
    ) -> RecordHistoryPage:
        # Lọc revision theo before_version rồi cắt trang mẫu.
        records = self.adapter.history(self.record(collection, record_id))
        revisions = [r for r in records if before_version is None or r.version < before_version]
        items = revisions[:page_size]
        return RecordHistoryPage(
            items=items,
            next_before_version=items[-1].version if len(revisions) > len(items) else None,
        )

    def ingest(self, payload: IngestionRequest) -> PlatformJob:
        # Kiểm schema_version và từng record rồi trả job queued mẫu.
        definition = self.collection(payload.collection)
        if payload.schema_version != definition.schema_version:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        for record in payload.records:
            validate_schema(record, definition.record_schema)
        return PlatformJob(
            job_id=JOB_ID, status="queued", created_at=SAMPLE_TIME, result=None, error=None
        )

    def job(self, job_id: str) -> PlatformJob:
        # Chỉ có một job mẫu, job_id lạ trả 404.
        if job_id != JOB_ID:
            raise CoreError(ErrorCode.NOT_FOUND)
        return self.adapter.job()
