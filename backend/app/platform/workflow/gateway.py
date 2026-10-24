# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Action Gateway: single execution entry point used by BPMN service tasks.
# BPMN never calls arbitrary URLs; only registered actions resolve here.
# Data actions go through published Data Products; HTTP actions only call
# registered service endpoints over an allowlisted boundary.

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.platform.http.errors import CoreError, ErrorCode
from app.platform.workflow.actions import ActionDefinition, ActionRegistry


@dataclass
class DataAccessPort:
    """Injected trusted port for governed data reads/writes."""

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)

    async def write(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)

    async def execute(self, action_id, payload, actor, request_id):
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)


class AllowlistHttpPort:
    def __init__(
        self,
        http: httpx.AsyncClient,
        allowed_hosts: frozenset[str],
        timeout_seconds: float = 20.0,
    ) -> None:
        self._http = http
        self._hosts = allowed_hosts
        self._timeout = timeout_seconds

    async def call(
        self, method: str, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        host = (urlsplit(url).hostname or "").lower()
        parsed = urlsplit(url)
        if (
            host not in self._hosts
            or parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.scheme == "http"
            and host not in {"127.0.0.1", "localhost", "host.docker.internal"}
        ):
            raise CoreError(ErrorCode.FORBIDDEN)
        try:
            return await self._http.request(
                method,
                url,
                json=payload,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=False,
                auth=None,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc


@dataclass
class ActionGateway:
    registry: ActionRegistry
    data: DataAccessPort | None = None
    http_port: AllowlistHttpPort | None = None
    service_bases: dict[str, str] | None = None

    def _service_url(self, definition: ActionDefinition) -> str:
        if definition.service_ref is None or definition.method is None or definition.path is None:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        base = (self.service_bases or {}).get(definition.service_ref)
        if not base:
            raise CoreError(ErrorCode.NOT_FOUND)
        if (
            not definition.path.startswith("/")
            or definition.path.startswith("//")
            or (".." in definition.path or "?" in definition.path or "#" in definition.path)
        ):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        return base.rstrip("/") + definition.path

    async def execute(
        self,
        action_id: str,
        payload: dict[str, Any],
        *,
        scope: str | None = None,
        actor=None,
        token: str | None = None,
        request_id: str = "workflow-action",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        definition = await self.registry.resolve_async(action_id, scope=scope)
        if definition.status != "ACTIVE":
            raise CoreError(ErrorCode.INVALID_STATE)
        definition.validate_input(payload)
        if definition.implementation_type == "SYSTEM":
            if actor is None:
                result = await self._execute_system(definition, payload)
            elif definition.action_id in {"data.get_record", "data.query", "data.update"}:
                if self.data is None:
                    raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
                result = await self.data.execute(definition.action_id, payload, actor, request_id)
            elif definition.action_id == "action.invoke":
                target = str(payload["target_action"])
                if target == "action.invoke":
                    raise CoreError(ErrorCode.VALIDATION_ERROR)
                result = await self.execute(
                    target,
                    dict(payload["payload"]),
                    actor=actor,
                    token=token,
                    request_id=request_id,
                    idempotency_key=idempotency_key,
                )
            else:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        elif definition.implementation_type == "HTTP":
            try:
                async with asyncio.timeout(definition.timeout_seconds):
                    result = await self._execute_http(
                        definition, payload, token=token, idempotency_key=idempotency_key
                    )
            except TimeoutError:
                raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        elif definition.implementation_type == "EVENT":
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        else:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        definition.validate_output(result)
        if not isinstance(result, dict):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return result

    async def _execute_system(
        self,
        definition: ActionDefinition,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.data is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        if definition.action_id in {"data.get_record", "data.query"}:
            return await self.data.read(payload)
        if definition.action_id in {"data.update", "data.create", "data.delete"}:
            return await self.data.write(payload)
        if definition.action_id == "action.invoke":
            if payload["target_action"] == "action.invoke":
                raise CoreError(ErrorCode.VALIDATION_ERROR)
            return await self.execute(str(payload["target_action"]), dict(payload["payload"]))
        raise CoreError(ErrorCode.NOT_FOUND)

    async def _execute_http(
        self,
        definition: ActionDefinition,
        payload: dict[str, Any],
        *,
        token=None,
        idempotency_key=None,
    ) -> dict[str, Any]:
        if self.http_port is None:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        url = self._service_url(definition)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = await self.http_port.call(str(definition.method).upper(), url, payload, headers)
        if response.status_code in (200, 201):
            try:
                body = response.json()
            except ValueError:
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
            if not isinstance(body, dict):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            return body
        if response.status_code in (400, 403, 404, 409, 422, 429):
            raise CoreError(ErrorCode.INVALID_STATE)
        if response.status_code == 401:
            raise CoreError(ErrorCode.UNAUTHENTICATED)
        if response.status_code == 504:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT)
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
