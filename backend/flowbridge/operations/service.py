# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# You may redistribute and modify it under GNU GPL version 3 or later.
# It is provided without any warranty, including MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See LICENSE for the full terms.

# Readiness policy and a bounded database probe for the backend runtime.

import asyncio
from typing import TYPE_CHECKING

from flowbridge.db import probe_connection
from flowbridge.shared.errors import CoreError, ErrorCode, ErrorDetail

if TYPE_CHECKING:
    from flowbridge.main import Resources


async def check_readiness(resources: "Resources | None", timeout_seconds: float) -> None:
    # Check runtime resources without certifying the four Open-Core engines.
    if resources is None:
        raise CoreError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            details=(ErrorDetail(field="runtime", reason="not_started"),),
        )
    if resources.http.is_closed:
        raise CoreError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            details=(ErrorDetail(field="http", reason="closed"),),
        )
    if resources.engine is None or resources.sessions is None:
        # Bootstrap không DB vẫn chạy được, nhưng chưa đủ điều kiện nhận lưu lượng.
        raise CoreError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            details=(ErrorDetail(field="database", reason="not_configured"),),
        )
    try:
        # Một deadline bao cả chờ pool, mở kết nối và chạy SELECT 1.
        async with asyncio.timeout(timeout_seconds):
            await probe_connection(resources.engine)
    except TimeoutError:
        raise CoreError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            details=(ErrorDetail(field="database", reason="timeout"),),
        ) from None
    except Exception:
        # Chỉ công bố lý do cố định; lỗi driver có thể chứa thông tin kết nối.
        raise CoreError(
            ErrorCode.UPSTREAM_UNAVAILABLE,
            details=(ErrorDetail(field="database", reason="unavailable"),),
        ) from None
