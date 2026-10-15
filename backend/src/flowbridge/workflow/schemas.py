# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# DTO workflow: definition/instance/task/action mẫu theo hợp đồng.

from typing import Annotated, Literal

from pydantic import Field

from flowbridge.shared.schemas import DTO, BusinessKey, Id, JsonObject, Timestamp, Version


class WorkflowDefinitionCreate(DTO):
    # Payload đăng ký gồm key/name và BPMN XML mẫu.
    key: Id
    name: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    bpmn_xml: Annotated[str, Field(min_length=1, max_length=1000000, pattern=r"\S")]


class WorkflowDefinition(DTO):
    # Definition mẫu đã version 1, chưa deploy engine thật.
    id: Id
    key: Id
    version: Version
    name: str


class WorkflowStart(DTO):
    # Payload khởi tạo instance theo business_key.
    definition_key: Id
    business_key: BusinessKey
    variables: JsonObject


class WorkflowInstance(DTO):
    # Instance mẫu đang active, gắn business_key mẫu.
    id: Id
    definition_key: Id
    business_key: BusinessKey
    status: Literal["active", "completed", "cancelled"]
    created_at: Timestamp


class WorkflowTask(DTO):
    # Task mẫu chờ duyệt kèm action và role được giao.
    id: Id
    instance_id: Id
    business_key: BusinessKey
    name: str
    status: Literal["pending", "completed"]
    allowed_actions: list[str]
    assigned_role: str
    version: Version


class WorkflowAction(DTO):
    # Action duyệt/từ chối mẫu có kiểm expected_version.
    action: Annotated[str, Field(min_length=1, max_length=100, pattern=r"\S")]
    expected_version: Version
    reason: Annotated[str | None, Field(max_length=2000)] = None
    variables: JsonObject | None = None
