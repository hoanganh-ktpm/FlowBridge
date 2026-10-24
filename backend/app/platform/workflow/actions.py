# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Action registry: logical action IDs with versioned implementations.
# System actions are immutable; overrides bind a new version to a scope and
# must stay compatible with the published input/output contract.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.platform.http.errors import CoreError, ErrorCode

ActionStatus = Literal["DRAFT", "ACTIVE", "DEPRECATED", "RETIRED"]
ImplementationType = Literal["SYSTEM", "HTTP", "EVENT"]


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    version: int
    owner_application: str
    implementation_type: ImplementationType
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    status: ActionStatus = "ACTIVE"
    # HTTP custom actions only.
    service_ref: str | None = None
    method: str | None = None
    path: str | None = None
    timeout_seconds: float = 20.0
    # Optional scoped override: replaces implementation for `scope` only.
    scope: str | None = None
    overrides: str | None = None

    def validate_input(self, payload: dict[str, Any]) -> None:
        validator = Draft202012Validator(self.input_schema)
        if not validator.is_valid(payload):
            raise CoreError(ErrorCode.VALIDATION_ERROR)

    def validate_output(self, payload: Any) -> None:
        validator = Draft202012Validator(self.output_schema)
        if not validator.is_valid(payload):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)


SYSTEM_ACTIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        action_id="data.get_record",
        version=1,
        owner_application="platform",
        implementation_type="SYSTEM",
        input_schema={
            "type": "object",
            "required": ["product_key", "contract_version", "filters"],
            "properties": {
                "product_key": {"type": "string", "minLength": 1},
                "contract_version": {"type": "string", "minLength": 1},
                "filters": {"type": "object"},
                "columns": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="data.query",
        version=1,
        owner_application="platform",
        implementation_type="SYSTEM",
        input_schema={
            "type": "object",
            "required": ["product_key", "contract_version"],
            "properties": {
                "product_key": {"type": "string", "minLength": 1},
                "contract_version": {"type": "string", "minLength": 1},
                "filters": {"type": "object"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="data.create",
        version=1,
        owner_application="platform",
        implementation_type="SYSTEM",
        status="DRAFT",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="data.update",
        version=1,
        owner_application="platform",
        implementation_type="SYSTEM",
        status="DRAFT",
        input_schema={
            "type": "object",
            "required": ["product_key", "contract_version", "filters", "patch"],
            "properties": {
                "product_key": {"type": "string", "minLength": 1},
                "contract_version": {"type": "string", "minLength": 1},
                "filters": {"type": "object"},
                "patch": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="data.delete",
        version=1,
        owner_application="platform",
        implementation_type="SYSTEM",
        status="DRAFT",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="notification.send",
        version=1,
        owner_application="platform",
        implementation_type="EVENT",
        status="DRAFT",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="action.invoke",
        version=1,
        owner_application="platform",
        implementation_type="SYSTEM",
        input_schema={
            "type": "object",
            "required": ["target_action", "payload"],
            "properties": {
                "target_action": {"type": "string", "minLength": 1},
                "payload": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    ),
    ActionDefinition(
        action_id="event.publish",
        version=1,
        owner_application="platform",
        implementation_type="EVENT",
        status="DRAFT",
        input_schema={
            "type": "object",
            "required": ["event_type", "payload"],
            "properties": {
                "event_type": {"type": "string", "minLength": 1},
                "payload": {"type": "object"},
            },
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
    ),
)


@dataclass
class ActionRegistry:
    """Resolve versioned actions; system definitions are immutable."""

    _definitions: dict[tuple[str, int], ActionDefinition] = field(default_factory=dict)

    def __init__(self) -> None:
        self._definitions = {(a.action_id, a.version): a for a in SYSTEM_ACTIONS}

    def register(self, definition: ActionDefinition) -> None:
        pending = [definition.input_schema, definition.output_schema]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if any(
                    not isinstance(node[name], str) or not node[name].startswith("#")
                    for name in ("$ref", "$dynamicRef")
                    if name in node
                ):
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)
        if definition.version < 1 or not 0 < definition.timeout_seconds <= 120:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        if definition.action_id in {item.action_id for item in SYSTEM_ACTIONS} and (
            not definition.scope or not definition.overrides
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        try:
            Draft202012Validator.check_schema(definition.input_schema)
            Draft202012Validator.check_schema(definition.output_schema)
        except SchemaError:
            raise CoreError(ErrorCode.VALIDATION_ERROR) from None
        if (
            definition.implementation_type != "HTTP"
            or definition.method not in {"POST", "GET", "PATCH", "PUT", "DELETE"}
            or not definition.service_ref
            or not definition.path
            or not definition.path.startswith("/")
            or definition.path.startswith("//")
            or any(value in definition.path for value in ("..", "?", "#"))
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        key = (definition.action_id, definition.version)
        if key in self._definitions:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        if definition.overrides is not None:
            base_id, _, base_version = definition.overrides.partition("@")
            try:
                base = self._definitions[(base_id, int(base_version))]
            except (KeyError, ValueError):
                raise CoreError(ErrorCode.NOT_FOUND) from None
            if (
                base_id != definition.action_id
                or not definition.scope
                or base.input_schema != definition.input_schema
                or base.output_schema != definition.output_schema
            ):
                raise CoreError(ErrorCode.VALIDATION_ERROR)
        self._definitions[key] = definition

    def resolve(
        self, action_id: str, version: int | None = None, scope: str | None = None
    ) -> ActionDefinition:
        if scope is not None:
            scoped = [
                definition
                for (aid, _), definition in self._definitions.items()
                if aid == action_id
                and definition.scope == scope
                and (version is None or definition.version == version)
            ]
            if scoped:
                return max(scoped, key=lambda d: d.version)
        candidates = [
            definition
            for (aid, ver), definition in self._definitions.items()
            if aid == action_id and definition.scope is None and (version is None or ver == version)
        ]
        if not candidates:
            raise CoreError(ErrorCode.NOT_FOUND)
        return max(candidates, key=lambda d: d.version)

    def list(self, status: ActionStatus | None = None) -> list[ActionDefinition]:
        items = [d for d in self._definitions.values() if d.scope is None]
        if status is not None:
            items = [d for d in items if d.status == status]
        return sorted(items, key=lambda d: (d.action_id, d.version))

    async def register_async(self, definition, *, actor=None, key=None, request_id=None):
        self.register(definition)

    async def resolve_async(self, action_id, version=None, scope=None):
        return self.resolve(action_id, version, scope)
