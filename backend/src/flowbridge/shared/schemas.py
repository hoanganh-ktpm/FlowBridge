# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. You may redistribute and modify it under
# GNU GPL version 3 or later, without any warranty. See LICENSE for full terms.

# Kiểu nền và phân trang dùng chung cho mọi DTO mẫu.

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Id = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
]
CollectionName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
BusinessKey = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"\S")]
Version = Annotated[int, Field(ge=1, strict=True)]
JsonObject = dict[str, JsonValue]


def validate_timestamp(value: str) -> str:
    # Hợp đồng dùng UTC ISO 8601; không nhận chuỗi tùy ý chỉ vì kết thúc bằng Z.
    datetime.fromisoformat(value)
    return value


Timestamp = Annotated[str, StringConstraints(pattern=r"Z$"), AfterValidator(validate_timestamp)]


class DTO(BaseModel):
    # Nền DTO cấm field thừa để khớp schema hợp đồng.
    model_config = ConfigDict(extra="forbid")


class Page[T](DTO):
    # Trang mẫu gồm items/page/page_size/total.
    items: list[T]
    page: Annotated[int, Field(ge=1)]
    page_size: Annotated[int, Field(ge=1, le=100)]
    total: Annotated[int, Field(ge=0)]


def paginate[T](items: list[T], page: int, page_size: int) -> Page[T]:
    # Cắt trang trên list mẫu trong bộ nhớ, không truy vấn database.
    offset = (page - 1) * page_size
    return Page[T](
        items=items[offset : offset + page_size], page=page, page_size=page_size, total=len(items)
    )
