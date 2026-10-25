# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Flowable REST adapter. Flowable owns BPMN definitions, process instances and
# execution state; Core never duplicates engine execution state.

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.platform.http.errors import CoreError, ErrorCode


def engine_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", value):
        raise CoreError(ErrorCode.VALIDATION_ERROR)
    return value


class FlowablePort(Protocol):
    async def awaits_message(self, instance_id: str, message_name: str) -> bool: ...
    async def deploy(self, name: str, bpmn_xml: str) -> dict[str, Any]: ...
    async def delete_deployment(self, deployment_id: str) -> None: ...
    async def list_definitions(self, key: str | None = None) -> list[dict[str, Any]]: ...
    async def start_process(
        self, key: str, business_key: str, variables: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def get_instance(self, instance_id: str) -> dict[str, Any]: ...
    async def get_task(self, task_id: str) -> dict[str, Any]: ...
    async def find_process(self, key: str, business_key: str, marker=None) -> list[dict]: ...
    async def list_tasks(
        self, *, process_instance_id: str | None = None, assignee: str | None = None
    ) -> list[dict[str, Any]]: ...
    async def complete_task(
        self, task_id: str, variables: dict[str, Any] | None = None
    ) -> None: ...
    async def message_event(
        self, message_name: str, business_key: str, variables: dict[str, Any], *, workflow_key=None
    ) -> dict[str, Any]: ...
    async def close(self) -> None: ...


def _raise_for_status(response: httpx.Response) -> httpx.Response:
    try:
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            raise CoreError(ErrorCode.NOT_FOUND) from exc
        if status == 409:
            raise CoreError(ErrorCode.VERSION_CONFLICT) from exc
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
    except httpx.HTTPError as exc:
        raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
    return response


@dataclass
class FlowableClient:
    """Thin adapter over the Flowable REST API (BPMN 2.0, basic auth)."""

    http: httpx.AsyncClient
    base_url: str
    username: str
    password: str = field(repr=False)
    timeout_seconds: float = 15.0

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(self.username, self.password)

    async def request(self, method, path, *, params=None, payload=None):
        try:
            response = await self.http.request(
                method,
                self._url(path),
                params=params,
                json=payload,
                auth=self._auth(),
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from None
        except httpx.HTTPError:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        _raise_for_status(response)
        if response.status_code == 204:
            return {}
        try:
            result = response.json()
        except ValueError:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from None
        if not isinstance(result, dict):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return result

    async def get_task(self, task_id):
        return await self.request("GET", f"/runtime/tasks/{engine_id(task_id)}")

    async def awaits_message(self, instance_id, message_name):
        result = await self.request(
            "GET",
            "/runtime/executions",
            params={
                "processInstanceId": engine_id(instance_id),
                "messageEventSubscriptionName": message_name,
                "size": 2,
            },
        )
        rows = result.get("data", [])
        if result.get("total", len(rows)) > 1:
            raise CoreError(ErrorCode.VERSION_CONFLICT)
        return len(rows) == 1

    async def find_process(self, key, business_key, marker=None):
        # Flowable 7.2 query DTO uses processBusinessKey; an unknown JSON field
        # is silently ignored by the engine, which would broaden correlation.
        payload = {"processDefinitionKey": key, "processBusinessKey": business_key}
        if marker:
            payload["variables"] = [
                {
                    "name": "internal_start_ref",
                    "value": marker,
                    "operation": "equals",
                    "type": "string",
                }
            ]
        result = await self.request(
            "POST", "/query/process-instances", payload=payload, params={"size": 100}
        )
        matches = result.get("data", [])
        if (
            not isinstance(matches, list)
            or result.get("total", len(matches)) > len(matches)
            or any(item.get("businessKey") != business_key for item in matches)
        ):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return matches

    async def deploy(self, name: str, bpmn_xml: str) -> dict[str, Any]:
        try:
            response = await self.http.post(
                self._url("/repository/deployments"),
                auth=self._auth(),
                files={
                    "file": (
                        name if name.endswith(".bpmn20.xml") else name + ".bpmn20.xml",
                        bpmn_xml.encode("utf-8"),
                        "text/xml",
                    )
                },
                timeout=self.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        return _raise_for_status(response).json()

    async def delete_deployment(self, deployment_id: str) -> None:
        try:
            response = await self.http.delete(
                self._url(f"/repository/deployments/{engine_id(deployment_id)}?cascade=false"),
                auth=self._auth(),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        if response.status_code == 404:
            raise CoreError(ErrorCode.NOT_FOUND)
        _raise_for_status(response)

    async def list_definitions(self, key: str | None = None) -> list[dict[str, Any]]:
        params = {"latest": "true", "size": "100"}
        if key:
            params["key"] = key
        try:
            response = await self.http.get(
                self._url("/repository/process-definitions"),
                auth=self._auth(),
                params=params,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        result = _raise_for_status(response).json()
        if result.get("total", len(result.get("data", []))) > len(result.get("data", [])):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return result.get("data", [])

    async def start_process(
        self, key: str, business_key: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {
            "processDefinitionKey": key,
            "businessKey": business_key,
            "variables": [
                {
                    "name": name,
                    "value": value,
                    **({"type": "json"} if isinstance(value, (dict, list)) else {}),
                }
                for name, value in variables.items()
            ],
        }
        try:
            response = await self.http.post(
                self._url("/runtime/process-instances"),
                auth=self._auth(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        return _raise_for_status(response).json()

    async def get_instance(self, instance_id: str) -> dict[str, Any]:
        try:
            response = await self.http.get(
                self._url(f"/runtime/process-instances/{engine_id(instance_id)}"),
                auth=self._auth(),
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        if response.status_code == 404:
            history = await self.request(
                "GET", f"/history/historic-process-instances/{engine_id(instance_id)}"
            )
            return {**history, "completed": history.get("endTime") is not None}
        return _raise_for_status(response).json()

    async def list_tasks(
        self, *, process_instance_id: str | None = None, assignee: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"size": "100"}
        if process_instance_id:
            params["processInstanceId"] = process_instance_id
        if assignee:
            params["assignee"] = assignee
        try:
            response = await self.http.get(
                self._url("/runtime/tasks"),
                auth=self._auth(),
                params=params,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        result = _raise_for_status(response).json()
        if result.get("total", len(result.get("data", []))) > len(result.get("data", [])):
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
        return result.get("data", [])

    async def complete_task(self, task_id: str, variables: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"action": "complete"}
        if variables:
            payload["variables"] = [
                {"name": name, "value": value} for name, value in variables.items()
            ]
        try:
            response = await self.http.post(
                self._url(f"/runtime/tasks/{engine_id(task_id)}"),
                auth=self._auth(),
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise CoreError(ErrorCode.UPSTREAM_TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE) from exc
        _raise_for_status(response)

    async def message_event(
        self, message_name: str, business_key: str, variables: dict[str, Any], *, workflow_key=None
    ) -> dict[str, Any]:
        if not workflow_key:
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        instances = await self.find_process(workflow_key, business_key)
        matches = []
        for instance in instances:
            result = await self.request(
                "GET",
                "/runtime/executions",
                params={
                    "processInstanceId": instance["id"],
                    "messageEventSubscriptionName": message_name,
                    "size": 100,
                },
            )
            executions = result.get("data", [])
            if result.get("total", len(executions)) > len(executions):
                raise CoreError(ErrorCode.UPSTREAM_UNAVAILABLE)
            matches.extend((instance["id"], item["id"]) for item in executions)
        if len(matches) != 1:
            raise CoreError(ErrorCode.NOT_FOUND if not matches else ErrorCode.VERSION_CONFLICT)
        instance_id, execution_id = matches[0]
        await self.request(
            "PUT",
            "/runtime/executions/" + engine_id(execution_id),
            payload={
                "action": "messageEventReceived",
                "messageName": message_name,
                "variables": [
                    {
                        "name": name,
                        "value": value,
                        **({"type": "json"} if isinstance(value, (dict, list)) else {}),
                    }
                    for name, value in variables.items()
                ],
            },
        )
        return {"id": instance_id}

    async def close(self) -> None:
        await self.http.aclose()


@dataclass
class InMemoryFlowable:
    """Test-only engine double implementing FlowablePort.

    Never used in production paths; production always wires FlowableClient.
    Stores definitions and manually created tasks for HTTP boundary tests only.
    Does not execute BPMN service tasks, gateways or message subscriptions.
    """

    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    _sequence: int = 0

    def _next_id(self, prefix: str) -> str:
        self._sequence += 1
        return f"{prefix}-{self._sequence}"

    async def deploy(self, name: str, bpmn_xml: str) -> dict[str, Any]:
        deployment_id = self._next_id("deployment")
        root = ET.fromstring(bpmn_xml)
        process = root.find("{http://www.omg.org/spec/BPMN/20100524/MODEL}process")
        if process is None or not process.get("id"):
            raise CoreError(ErrorCode.VALIDATION_ERROR)
        self.definitions[process.get("id")] = {"id": deployment_id, "name": name, "bpmn": bpmn_xml}
        return {"id": deployment_id, "name": name}

    async def delete_deployment(self, deployment_id: str) -> None:
        for name, definition in list(self.definitions.items()):
            if definition["id"] == deployment_id:
                del self.definitions[name]
                return
        raise CoreError(ErrorCode.NOT_FOUND)

    async def list_definitions(self, key: str | None = None) -> list[dict[str, Any]]:
        items = [
            {"id": definition["id"], "key": name, "version": 1}
            for name, definition in self.definitions.items()
        ]
        return [item for item in items if key is None or item["key"] == key]

    async def start_process(
        self, key: str, business_key: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        if key not in self.definitions:
            raise CoreError(ErrorCode.NOT_FOUND)
        instance_id = self._next_id("instance")
        self.instances[instance_id] = {
            "id": instance_id,
            "processDefinitionKey": key,
            "businessKey": business_key,
            "variables": dict(variables),
            "suspended": False,
            "completed": False,
        }
        return self.instances[instance_id]

    async def get_instance(self, instance_id: str) -> dict[str, Any]:
        try:
            return self.instances[instance_id]
        except KeyError:
            raise CoreError(ErrorCode.NOT_FOUND) from None

    async def get_task(self, task_id):
        if task_id not in self.tasks:
            raise CoreError(ErrorCode.NOT_FOUND)
        return self.tasks[task_id]

    async def find_process(self, key, business_key, marker=None):
        return [
            item
            for item in self.instances.values()
            if item["businessKey"] == business_key
            and item["processDefinitionKey"] == key
            and (marker is None or item["variables"].get("internal_start_ref") == marker)
        ]

    async def list_tasks(
        self, *, process_instance_id: str | None = None, assignee: str | None = None
    ) -> list[dict[str, Any]]:
        items = list(self.tasks.values())
        if process_instance_id is not None:
            items = [t for t in items if t["processInstanceId"] == process_instance_id]
        if assignee is not None:
            items = [t for t in items if t.get("assignee") == assignee]
        return items

    def create_user_task(
        self, instance_id: str, task_key: str, assignee: str | None = None
    ) -> dict[str, Any]:
        task_id = self._next_id("task")
        task = {
            "id": task_id,
            "taskDefinitionKey": task_key,
            "processInstanceId": instance_id,
            "assignee": assignee,
            "completed": False,
        }
        self.tasks[task_id] = task
        return task

    async def complete_task(self, task_id: str, variables: dict[str, Any] | None = None) -> None:
        try:
            task = self.tasks[task_id]
        except KeyError:
            raise CoreError(ErrorCode.NOT_FOUND) from None
        task["completed"] = True
        if variables:
            self.instances[task["processInstanceId"]]["variables"].update(variables)

    async def message_event(
        self, message_name: str, business_key: str, variables: dict[str, Any], *, workflow_key=None
    ) -> dict[str, Any]:
        for instance in self.instances.values():
            if (
                instance["businessKey"] == business_key
                and not instance["completed"]
                and (workflow_key is None or instance["processDefinitionKey"] == workflow_key)
            ):
                instance["variables"].update(variables)
                instance["variables"][f"message:{message_name}"] = True
                return instance
        raise CoreError(ErrorCode.NOT_FOUND)

    async def close(self) -> None:
        return None
