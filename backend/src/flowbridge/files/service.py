# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Service tệp mẫu: kiểm scan_status/metadata trên fixture, chưa quét thật.

import hashlib
import json

from flowbridge.files.adapter import SampleFilesAdapter
from flowbridge.files.schemas import File, FileSearchHit
from flowbridge.sample import SAMPLE_TIME
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.shared.schemas import Page, paginate


class FilesService:
    # Use case tệp mẫu, không ghi object storage thật.
    def __init__(self, adapter: SampleFilesAdapter) -> None:
        self.adapter = adapter

    def file(self, file_id: str) -> File:
        # Tìm metadata mẫu theo ID, sai trả 404.
        for file in self.adapter.files():
            if file.id == file_id:
                return file
        raise CoreError(ErrorCode.NOT_FOUND)

    def list_files(self, page: int, page_size: int, resource_id: str | None) -> Page[File]:
        # Lọc file mẫu theo resource_id rồi phân trang.
        files = self.adapter.files()
        if resource_id is not None:
            files = [file for file in files if file.metadata.get("resource_id") == resource_id]
        return paginate(files, page, page_size)

    def upload(self, name: str, content_type: str, content: bytes, metadata: str) -> File:
        # Kiểm metadata JSON, loại tenant/owner/acl client gửi rồi trả file pending.
        try:
            parsed = json.loads(metadata)
            if not isinstance(parsed, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise CoreError(ErrorCode.VALIDATION_ERROR) from None
        # Metadata chỉ là dữ liệu minh họa; không đổi danh tính hoặc quyền server.
        parsed = {k: v for k, v in parsed.items() if k not in {"tenant_id", "owner", "acl"}}
        return File(
            id="FILE-UPLOADED",
            name=name,
            content_type=content_type,
            size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            scan_status="pending",
            metadata=parsed,
            created_at=SAMPLE_TIME,
        )

    def download(self, file_id: str) -> tuple[File, bytes]:
        # Chặn tải file chưa clean, chỉ file clean mới trả bytes mẫu.
        file = self.file(file_id)
        if file.scan_status != "clean":
            raise CoreError(ErrorCode.INVALID_STATE)
        return file, self.adapter.content()

    def search(
        self, q: str, page: int, page_size: int, resource_id: str | None
    ) -> Page[FileSearchHit]:
        # Tìm literal trong bytes mẫu của file clean, excerpt tối đa 500 ký tự.
        # Tìm literal trong một fixture tổng hợp; không phải full-text engine.
        text = self.adapter.content().decode("utf-8")
        hits = [
            FileSearchHit(file=file, excerpt=text[:500])
            for file in self.adapter.files()
            if file.scan_status == "clean"
            and (resource_id is None or file.metadata.get("resource_id") == resource_id)
            and q.casefold() in text.casefold()
        ]
        return paginate(hits, page, page_size)
