# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Router tệp: list/upload/search/download mẫu, kiểm tra scan_status khi tải.

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.datastructures import UploadFile

from flowbridge.files.schemas import File, FileSearchHit
from flowbridge.files.service import FilesService
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.shared.http import PageNumber, PageSize, WriteKey
from flowbridge.shared.schemas import Id, Page

router = APIRouter(prefix="/api/v1/core/files", tags=["Files"])


def get_service(request: Request) -> FilesService:
    # Lấy service tệp mẫu đã inject ở lifespan.
    return request.app.state.files_service


Service = Annotated[FilesService, Depends(get_service)]


@router.get("", operation_id="listFiles")
async def list_files(
    service: Service,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    resource_id: Id | None = None,
) -> Page[File]:
    # Liệt kê file mẫu, lọc theo resource_id khi có.
    return service.list_files(page, page_size, resource_id)


@router.post("", operation_id="uploadFile", status_code=202)
async def upload_file(request: Request, service: Service, _key: WriteKey) -> File:
    # Parser upstream, một part file và metadata; giới hạn đọc trước khi tạo DTO.
    limit = request.app.state.settings.sample_upload_max_bytes
    async with request.form(max_files=1, max_fields=1, max_part_size=limit) as form:
        entries = form.multi_items()
        if len({name for name, _ in entries}) != len(entries):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if set(form) - {"file", "metadata"}:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        upload = form.get("file")
        metadata = form.get("metadata", "{}")
        if not isinstance(upload, UploadFile) or not isinstance(metadata, str):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        content = await upload.read(limit + 1)
        if len(content) > limit:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        return service.upload(
            upload.filename or "upload.bin",
            upload.content_type or "application/octet-stream",
            content,
            metadata,
        )


# Route tĩnh phải đăng ký trước /{file_id}.
@router.get("/search", operation_id="searchFiles")
async def search_files(
    service: Service,
    q: Annotated[str, Query(min_length=1, max_length=256, pattern=r"\S")],
    page: PageNumber = 1,
    page_size: PageSize = 20,
    resource_id: Id | None = None,
) -> Page[FileSearchHit]:
    return service.search(q, page, page_size, resource_id)


@router.get("/{file_id}", operation_id="getFile")
async def get_file(file_id: Id, service: Service) -> File:
    # Trả metadata mẫu theo file_id, sai ID trả 404.
    return service.file(file_id)


@router.get("/{file_id}/download", operation_id="downloadFile")
async def download_file(file_id: Id, service: Service) -> Response:
    # Chỉ cho tải file clean, trả bytes mẫu kèm content-type gốc.
    file, content = service.download(file_id)
    return Response(
        content=content,
        media_type=file.content_type,
        headers={"Content-Disposition": 'attachment; filename="sample.txt"'},
    )
