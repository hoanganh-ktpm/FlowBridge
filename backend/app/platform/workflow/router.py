# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Workflow HTTP API: definitions, instances, user tasks and event intake.
# Mount under /api/v1/workflows. Auth reuses Keycloak introspection via
# authenticated_identity; IT publish/admin stays PLATFORM_ADMIN-gated in the
# service layer (deploy path requires approval flag in this module).

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from app.platform.http.correlation import CorrelationMiddleware
from app.platform.http.errors import (
    CoreError,
    ErrorCode,
    ErrorDetail,
    register_exception_handlers,
)
from app.platform.http.request_policy import PrivateJsonMiddleware
from app.platform.identity.authentication import VerifiedIdentity, authenticated_identity
from app.platform.workflow.actions import ActionDefinition, ActionRegistry
from app.platform.workflow.events import EventRouter
from app.platform.workflow.flowable import FlowablePort
from app.platform.workflow.settings import WorkflowApiSettings

workflow_router = APIRouter(prefix="/api/v1/workflows")

Actor = Annotated[VerifiedIdentity, Depends(authenticated_identity)]


@dataclass
class WorkflowRuntime:
    engine: FlowablePort
    registry: ActionRegistry
    gateway: Any
    event_router: EventRouter
    admin_policy: Any
    configuration: WorkflowApiSettings
    runs: Any = None
    decisions: Any = None


def _gateway_state(request: Request) -> WorkflowRuntime:
    state = getattr(request.app.state, "workflow", None)
    if state is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    return state


async def _admin(state, actor, operation):
    if state.admin_policy is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    await state.admin_policy.require_admin(actor, operation)


async def _actor(state, actor):
    if state.admin_policy is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    await state.admin_policy.require_actor(actor)


def _variables(values, allowed):
    if set(values) - allowed or len(json.dumps(values)) > 16384:
        raise CoreError(ErrorCode.VALIDATION_ERROR)


def _key(request):
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", key):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    return key


class StartWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_key: str = Field(min_length=1, max_length=200)
    variables: dict[str, Any] = Field(default_factory=dict)


class CompleteTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variables: dict[str, Any] = Field(default_factory=dict)


class BpmnPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    bpmn_xml: str = Field(min_length=1, max_length=1_000_000)


class ActionRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)
    owner_application: str = Field(min_length=1, max_length=200)
    implementation_type: str = Field(pattern=r"^(SYSTEM|HTTP|EVENT)$")
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    service_ref: str | None = None
    method: str | None = None
    path: str | None = None
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    scope: str | None = None
    overrides: str | None = None


BPMN_NS = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"


def validate_bpmn(bpmn_xml: str) -> str:
    try:
        root = ET.fromstring(bpmn_xml.encode("utf-8"))
    except ET.ParseError:
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None
    processes = root.findall(f".//{BPMN_NS}process")
    if not processes:
        raise CoreError(
            ErrorCode.VALIDATION_ERROR,
            details=(ErrorDetail(field="bpmn_xml", reason="No BPMN process found."),),
        )
    key = processes[0].get("id", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key or ""):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    return key


@workflow_router.get("", operation_id="listWorkflowDefinitions")
async def list_definitions(request: Request, actor: Actor, key: str | None = None):
    state = _gateway_state(request)
    await _admin(state, actor, "workflow.list_definitions")
    definitions = await state.engine.list_definitions(key)
    return {
        "definitions": [
            {name: item.get(name) for name in ("id", "key", "name", "version")}
            for item in definitions
        ]
    }


@workflow_router.post("/definitions", operation_id="publishWorkflowDefinition", status_code=201)
async def publish_definition(body: BpmnPublish, request: Request, actor: Actor):
    state = _gateway_state(request)
    await _admin(state, actor, "workflow.deploy")
    key = validate_bpmn(body.bpmn_xml)
    deployment = await state.engine.deploy(body.name, body.bpmn_xml)
    return {"key": key, "deployment": {"id": deployment.get("id"), "name": deployment.get("name")}}


@workflow_router.post("/{key}/start", operation_id="startWorkflow", status_code=201)
async def start_workflow(key: str, body: StartWorkflow, request: Request, actor: Actor):
    state = _gateway_state(request)
    if key not in state.configuration.allowed_workflows:
        raise CoreError(ErrorCode.FORBIDDEN)
    _variables(body.variables, state.configuration.start_variable_names)
    if state.runs is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    instance = await state.runs.start(
        actor, key, body.business_key, body.variables, _key(request), request.state.correlation_id
    )
    return {
        "instance_id": instance.get("id"),
        "business_key": instance.get("businessKey"),
        "variables": {},
    }


@workflow_router.get("/instances/{instance_id}", operation_id="getWorkflowInstance")
async def get_instance(instance_id: str, request: Request, actor: Actor):
    state = _gateway_state(request)
    if state.runs is None:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    await state.runs.authorize(actor, instance_id)
    instance = await state.engine.get_instance(instance_id)
    return {
        "instance_id": instance.get("id"),
        "business_key": instance.get("businessKey"),
        "suspended": instance.get("suspended", False),
        "completed": instance.get("completed", False),
        "variables": {},
    }


@workflow_router.get("/tasks", operation_id="listWorkflowTasks")
async def list_tasks(
    request: Request,
    actor: Actor,
    process_instance_id: str | None = None,
    mine: bool = False,
):
    state = _gateway_state(request)
    await _actor(state, actor)
    engine: FlowablePort = state.engine
    tasks = await engine.list_tasks(
        process_instance_id=process_instance_id,
        assignee=actor.subject,
    )
    return {
        "tasks": [
            {
                name: task.get(name)
                for name in ("id", "name", "assignee", "processInstanceId", "taskDefinitionKey")
            }
            for task in tasks
            if task.get("assignee") == actor.subject
        ]
    }


@workflow_router.post("/tasks/{task_id}/complete", operation_id="completeWorkflowTask")
async def complete_task(task_id: str, body: CompleteTask, request: Request, actor: Actor):
    state = _gateway_state(request)
    await _actor(state, actor)
    _variables(body.variables, state.configuration.task_variable_names)
    task = await state.engine.get_task(task_id)
    if str(task.get("processDefinitionId", "")).split(":", 1)[0] == "supply_handover_exception":
        # Decision approvals require the trusted Workspace freshness port below.
        raise CoreError(ErrorCode.FORBIDDEN)
    assignee = task.get("assignee")
    if assignee != actor.subject:
        raise CoreError(ErrorCode.FORBIDDEN)
    await state.engine.complete_task(task_id, body.variables)
    return {"task_id": task_id, "completed": True}


def decision_port(request):
    state = _gateway_state(request)
    if (
        state.decisions is None
        or "supply_handover_exception" not in state.configuration.allowed_workflows
    ):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
    return state.decisions


from uuid import UUID  # noqa: E402

from app.platform.workflow.decision_schemas import (  # noqa: E402
    DecisionHandoffCommand,
    DecisionReconcileStart,
    DecisionReplanCommand,
    DecisionStart,
    DecisionTaskCommand,
)


@workflow_router.post(
    "/decision-runs/commands/start", operation_id="startDecisionWorkflow", status_code=201
)
async def start_decision_run(body: DecisionStart, request: Request, actor: Actor):
    _key(request)
    return await decision_port(request).start(actor, body, request.state.correlation_id)


@workflow_router.post(
    "/decision-runs/commands/reconcile-start", operation_id="reconcileDecisionStart"
)
async def reconcile_decision_start(body: DecisionReconcileStart, request: Request, actor: Actor):
    return await decision_port(request).reconcile_start(actor, body)


@workflow_router.get("/decision-runs/{instance_id}", operation_id="getDecisionWorkflow")
async def get_decision_run(instance_id: str, principal_id: UUID, request: Request, actor: Actor):
    return await decision_port(request).state(actor, instance_id, principal_id)


@workflow_router.post(
    "/decision-runs/{instance_id}/tasks/{task_id}/complete",
    operation_id="completeDecisionWorkflowTask",
)
async def complete_decision_task(
    instance_id: str, task_id: str, body: DecisionTaskCommand, request: Request, actor: Actor
):
    return await decision_port(request).complete(actor, instance_id, task_id, body, _key(request))


@workflow_router.post(
    "/decision-runs/{instance_id}/verify-handoff", operation_id="verifyDecisionCarrierHandoff"
)
async def verify_decision_handoff(
    instance_id: str, body: DecisionHandoffCommand, request: Request, actor: Actor
):
    _key(request)
    return await decision_port(request).confirm_handoff(
        actor,
        instance_id,
        body,
        request.headers.get("Authorization", "").partition(" ")[2],
        request.state.correlation_id,
    )


@workflow_router.post("/decision-runs/{instance_id}/replan", operation_id="requestDecisionReplan")
async def replan_decision(
    instance_id: str, body: DecisionReplanCommand, request: Request, actor: Actor
):
    _key(request)
    return await decision_port(request).request_replan(actor, instance_id, body)


@workflow_router.post("/events", operation_id="receiveWorkflowEvent", status_code=202)
async def receive_event(request: Request):
    state = _gateway_state(request)
    raw = await request.body()
    signature = request.headers.get("X-Event-Signature")
    router: EventRouter = state.event_router
    router.verify(raw, signature)
    try:
        event = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise CoreError(ErrorCode.VALIDATION_ERROR) from None
    outcomes = await router.receive(event if isinstance(event, dict) else {})
    return {"outcomes": outcomes}


@workflow_router.post("/hasura-events", operation_id="relayHasuraWorkflowEvent")
async def hasura_event(request: Request):
    from app.platform.workflow.relay import forward_hasura

    state = _gateway_state(request)
    return await forward_hasura(
        await request.body(),
        request.headers.get("X-Hasura-Event-Secret"),
        state.configuration,
        request.app.state.resources.http,
        request.headers.get("X-FlowBridge-Source"),
    )


@workflow_router.post("/actions", operation_id="registerCustomAction", status_code=201)
async def register_action(body: ActionRegister, request: Request, actor: Actor):
    state = _gateway_state(request)
    await _admin(state, actor, "workflow.register_action")
    registry: ActionRegistry = state.registry
    definition = ActionDefinition(
        action_id=body.action_id,
        version=body.version,
        owner_application=body.owner_application,
        implementation_type=body.implementation_type,  # type: ignore[arg-type]
        input_schema=body.input_schema,
        output_schema=body.output_schema,
        status="ACTIVE",
        service_ref=body.service_ref,
        method=body.method,
        path=body.path,
        timeout_seconds=body.timeout_seconds,
        scope=body.scope,
        overrides=body.overrides,
    )
    if definition.implementation_type == "HTTP" and (
        not definition.service_ref or not definition.method or not definition.path
    ):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    await registry.register_async(
        definition, actor=actor, key=_key(request), request_id=request.state.correlation_id
    )
    return {"action_id": definition.action_id, "version": definition.version}


@workflow_router.post("/actions/{action_id}/execute", operation_id="executeWorkflowAction")
async def execute_action(action_id: str, payload: dict[str, Any], request: Request, actor: Actor):
    state = _gateway_state(request)
    await _actor(state, actor)
    token = request.headers.get("Authorization", "").partition(" ")[2]
    return {
        "result": await state.gateway.execute(
            action_id,
            payload,
            actor=actor,
            token=token,
            request_id=request.state.correlation_id,
            idempotency_key=request.headers.get("Idempotency-Key"),
        )
    }


def create_workflow_app(
    engine: FlowablePort,
    registry: ActionRegistry,
    gateway,
    event_router: EventRouter,
    settings,
    *,
    admin_policy=None,
    configuration=None,
    runs=None,
    testing: bool = False,
) -> FastAPI:
    if not testing and (
        not getattr(registry, "durable", False) or not getattr(event_router.inbox, "durable", False)
    ):
        raise ValueError("Workflow production requires durable action registry and event inbox")
    app = FastAPI(title="FlowBridge Workflow API", version="0.1.0")
    app.state.settings = settings
    app.state.workflow = WorkflowRuntime(
        engine,
        registry,
        gateway,
        event_router,
        admin_policy,
        configuration or WorkflowApiSettings(_env_file=None),
        runs,
    )
    register_exception_handlers(app)
    app.add_middleware(PrivateJsonMiddleware)
    app.add_middleware(CorrelationMiddleware)
    app.include_router(workflow_router)
    return app
