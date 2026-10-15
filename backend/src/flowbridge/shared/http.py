# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Kiểu dùng chung cho biên HTTP: phân trang và khóa idempotency.

from typing import Annotated

from fastapi import Header, Query

# Số trang bắt đầu từ 1.
PageNumber = Annotated[int, Query(ge=1)]
# Kích thước trang 1-100 theo hợp đồng.
PageSize = Annotated[int, Query(ge=1, le=100)]
# Khóa chống trùng cho ghi, router không dùng giá trị trực tiếp.
WriteKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
