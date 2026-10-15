# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# You may redistribute and modify it under GNU GPL version 3 or later.
# It is provided without any warranty, including MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See LICENSE for the full terms.

# Process liveness and bounded readiness of the backend runtime only.

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from flowbridge.operations.service import check_readiness

# Ẩn khỏi schema nghiệp vụ không thay cơ chế chặn route trên gateway/mạng vận hành.
router = APIRouter(prefix="/_ops", include_in_schema=False)


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"
    scope: Literal["process"] = "process"


class ReadinessResponse(BaseModel):
    # Chỉ xác nhận runtime backend, không xác nhận bốn engine Open-Core.

    status: Literal["ready"] = "ready"
    scope: Literal["backend_runtime"] = "backend_runtime"


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    # Respond without dependency I/O, even if readiness fails.
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(request: Request) -> ReadinessResponse:
    # Probe runtime resources; this does not certify the four Open-Core engines.
    await check_readiness(
        request.app.state.resources, request.app.state.settings.health_timeout_seconds
    )
    return ReadinessResponse()
