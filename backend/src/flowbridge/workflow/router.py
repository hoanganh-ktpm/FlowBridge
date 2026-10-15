# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Router workflow: deploy/start/query/action mẫu theo hợp đồng.

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request

from flowbridge.shared.http import PageNumber, PageSize, WriteKey
from flowbridge.shared.schemas import Id, Page
from flowbridge.workflow.schemas import (
    WorkflowAction,
    WorkflowDefinition,
    WorkflowDefinitionCreate,
    WorkflowInstance,
    WorkflowStart,
    WorkflowTask,
)
from flowbridge.workflow.service import WorkflowService

router = APIRouter(tags=["Workflow"])


def get_service(request: Request) -> WorkflowService:
    # Lấy service workflow mẫu đã inject ở lifespan.
    return request.app.state.workflow_service


Service = Annotated[WorkflowService, Depends(get_service)]


@router.post(
    "/internal/v1/core/workflow/definitions", operation_id="registerWorkflow", status_code=201
)
async def register_workflow(
    payload: WorkflowDefinitionCreate, service: Service, _key: WriteKey
) -> WorkflowDefinition:
    # Đăng ký definition mẫu, không deploy BPMN lên engine.
    return service.register(payload)


@router.post("/internal/v1/core/workflow/instances", operation_id="startWorkflow", status_code=201)
async def start_workflow(
    payload: WorkflowStart, service: Service, _key: WriteKey
) -> WorkflowInstance:
    # Khởi tạo instance mẫu theo definition_key và business_key.
    return service.start(payload)


@router.get("/api/v1/core/workflow/instances/{instance_id}", operation_id="getWorkflowInstance")
async def get_workflow_instance(instance_id: Id, service: Service) -> WorkflowInstance:
    # Trả instance mẫu theo ID, sai ID trả 404.
    return service.instance(instance_id)


@router.get("/api/v1/core/workflow/tasks", operation_id="listWorkflowTasks")
async def list_workflow_tasks(
    service: Service,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    status: Literal["pending", "completed"] | None = None,
) -> Page[WorkflowTask]:
    # Liệt kê task mẫu, lọc theo trạng thái khi có.
    return service.tasks(page, page_size, status)


@router.get("/api/v1/core/workflow/tasks/{task_id}", operation_id="getWorkflowTask")
async def get_workflow_task(task_id: Id, service: Service) -> WorkflowTask:
    # Trả task mẫu theo ID, sai ID trả 404.
    return service.task(task_id)


@router.post(
    "/internal/v1/core/workflow/tasks/{task_id}/actions", operation_id="applyWorkflowAction"
)
async def apply_workflow_action(
    task_id: Id, payload: WorkflowAction, service: Service, _key: WriteKey
) -> WorkflowTask:
    # Áp action mẫu có kiểm version và allowed_actions.
    return service.action(task_id, payload)
