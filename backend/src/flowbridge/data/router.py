# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Router dữ liệu: ánh xạ HTTP sang DataService, giữ operation_id hợp đồng.

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from flowbridge.data.schemas import (
    CollectionDefinition,
    IngestionRequest,
    PlatformJob,
    Record,
    RecordCreate,
    RecordHistoryPage,
    RecordPatch,
    TransactionRequest,
    TransactionResult,
)
from flowbridge.data.service import DataService
from flowbridge.shared.http import PageNumber, PageSize, WriteKey
from flowbridge.shared.schemas import CollectionName, Id, Page

router = APIRouter(tags=["Data"])


def get_service(request: Request) -> DataService:
    # Lấy service mẫu đã inject ở lifespan theo sample mode.
    return request.app.state.data_service


Service = Annotated[DataService, Depends(get_service)]


@router.get("/api/v1/core/data-schemas/{collection}", operation_id="getCollectionSchema")
async def get_collection_schema(
    collection: CollectionName, service: Service
) -> CollectionDefinition:
    # Trả định nghĩa schema của collection mẫu.
    return service.collection(collection)


@router.post(
    "/internal/v1/core/data-schemas", operation_id="registerCollectionSchema", status_code=201
)
async def register_collection_schema(
    payload: CollectionDefinition, service: Service, _key: WriteKey
) -> CollectionDefinition:
    # Kiểm tra schema hợp lệ rồi phản chiếu DTO, chưa persistence.
    return service.register(payload)


@router.get("/api/v1/core/data/{collection}", operation_id="listRecords")
async def list_records(
    collection: CollectionName,
    service: Service,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    filters: str | None = None,
) -> Page[Record]:
    # Liệt kê record mẫu có lọc/phân trang, không chạm database thật.
    return service.list_records(collection, page, page_size, filters)


@router.post("/api/v1/core/data/{collection}", operation_id="createRecord", status_code=201)
async def create_record(
    collection: CollectionName, payload: RecordCreate, service: Service, _key: WriteKey
) -> Record:
    # Tạo record mẫu từ payload, trả ID/version cố định.
    return service.create(collection, payload.data)


@router.get("/api/v1/core/data/{collection}/{record_id}", operation_id="getRecord")
async def get_record(collection: CollectionName, record_id: Id, service: Service) -> Record:
    # Đọc một record mẫu theo ID, sai ID trả 404.
    return service.record(collection, record_id)


@router.patch("/api/v1/core/data/{collection}/{record_id}", operation_id="updateRecord")
async def update_record(
    collection: CollectionName, record_id: Id, payload: RecordPatch, service: Service
) -> Record:
    # Cập nhật mẫu có kiểm tra expected_version, sai version trả 409.
    return service.update(collection, record_id, payload)


@router.delete(
    "/api/v1/core/data/{collection}/{record_id}", operation_id="deleteRecord", status_code=204
)
async def delete_record(
    collection: CollectionName,
    record_id: Id,
    service: Service,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    # Xóa mẫu trả 204 không body, sai version trả 409.
    service.delete(collection, record_id, expected_version)
    return Response(status_code=204)


@router.post("/internal/v1/core/data/transactions", operation_id="commitTransaction")
async def commit_transaction(
    payload: TransactionRequest, service: Service, _key: WriteKey
) -> TransactionResult:
    # Gom tối đa 50 thao tác mẫu, trả kết quả tổng hợp không ghi bền.
    return service.transaction(payload)


@router.get("/api/v1/core/data/{collection}/{record_id}/history", operation_id="listRecordHistory")
async def list_record_history(
    collection: CollectionName,
    record_id: Id,
    service: Service,
    page_size: PageSize = 20,
    before_version: Annotated[int | None, Query(ge=1)] = None,
) -> RecordHistoryPage:
    # Trả lịch sử revision mẫu giảm dần theo before_version.
    return service.history(collection, record_id, page_size, before_version)


@router.post("/internal/v1/core/ingestion-jobs", operation_id="createIngestionJob", status_code=202)
async def create_ingestion_job(
    payload: IngestionRequest, service: Service, _key: WriteKey
) -> PlatformJob:
    # Nhận batch mẫu tối đa 1000 record, trả job queued.
    return service.ingest(payload)


@router.get("/internal/v1/core/ingestion-jobs/{job_id}", operation_id="getIngestionJob")
async def get_ingestion_job(job_id: Id, service: Service) -> PlatformJob:
    # Trả trạng thái job mẫu theo job_id cố định.
    return service.job(job_id)
