# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Service workflow mẫu: kiểm definition/version/action trên fixture.

from flowbridge.sample import DEFINITION_ID, SAMPLE_TIME
from flowbridge.shared.contract import validate_dto
from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.shared.schemas import Page, paginate
from flowbridge.workflow.adapter import SampleWorkflowAdapter
from flowbridge.workflow.schemas import (
    WorkflowAction,
    WorkflowDefinition,
    WorkflowDefinitionCreate,
    WorkflowInstance,
    WorkflowStart,
    WorkflowTask,
)


class WorkflowService:
    # Use case workflow mẫu, không gọi engine BPMN thật.
    def __init__(self, adapter: SampleWorkflowAdapter) -> None:
        self.adapter = adapter

    def register(self, payload: WorkflowDefinitionCreate) -> WorkflowDefinition:
        # Chỉ trả DTO mẫu; không parse/thực thi XML hoặc deploy engine.
        return WorkflowDefinition(id=DEFINITION_ID, key=payload.key, version=1, name=payload.name)

    def start(self, payload: WorkflowStart) -> WorkflowInstance:
        # Chặn definition_key lạ bằng 404, trả instance active mẫu.
        if payload.definition_key != self.adapter.instance().definition_key:
            raise CoreError(ErrorCode.NOT_FOUND)
        return WorkflowInstance(
            id=self.adapter.instance().id,
            definition_key=payload.definition_key,
            business_key=payload.business_key,
            status="active",
            created_at=SAMPLE_TIME,
        )

    def instance(self, instance_id: str) -> WorkflowInstance:
        # Trả instance mẫu duy nhất, sai ID trả 404.
        instance = self.adapter.instance()
        if instance.id != instance_id:
            raise CoreError(ErrorCode.NOT_FOUND)
        return instance

    def tasks(self, page: int, page_size: int, status: str | None) -> Page[WorkflowTask]:
        # Lọc task mẫu theo status rồi phân trang.
        tasks = self.adapter.tasks()
        if status is not None:
            tasks = [task for task in tasks if task.status == status]
        return paginate(tasks, page, page_size)

    def task(self, task_id: str) -> WorkflowTask:
        # Tìm task mẫu theo ID, sai trả 404.
        for task in self.adapter.tasks():
            if task.id == task_id:
                return task
        raise CoreError(ErrorCode.NOT_FOUND)

    def action(self, task_id: str, payload: WorkflowAction) -> WorkflowTask:
        # Kiểm DTO, version và action cho phép rồi trả task completed mẫu.
        validate_dto(payload.model_dump(mode="json", exclude_unset=True), "WorkflowAction")
        task = self.task(task_id)
        if payload.expected_version != task.version:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        if payload.action not in task.allowed_actions:
            raise CoreError(ErrorCode.INVALID_STATE)
        return task.model_copy(
            update={"status": "completed", "allowed_actions": [], "version": task.version + 1},
            deep=True,
        )
