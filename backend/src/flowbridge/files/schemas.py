# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO tệp: metadata và kết quả tìm kiếm mẫu theo hợp đồng.

from typing import Annotated, Literal

from pydantic import Field

from flowbridge.shared.schemas import DTO, Id, JsonObject, Timestamp


class File(DTO):
    # Metadata mẫu kèm checksum và trạng thái quét.
    id: Id
    name: str
    content_type: str
    size: Annotated[int, Field(ge=0)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    scan_status: Literal["pending", "clean", "rejected", "failed"]
    metadata: JsonObject
    created_at: Timestamp


class FileSearchHit(DTO):
    # Kết quả mẫu gồm file và đoạn trích thuần tối đa 500 ký tự.
    file: File
    excerpt: Annotated[str, Field(max_length=500)]
