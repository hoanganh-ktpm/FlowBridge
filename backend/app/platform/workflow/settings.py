# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge, licensed under GNU GPL version 3 or later.
# You may redistribute and modify it under that license. It is provided without
# any warranty, including MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE for the full terms.

# Workflow Engine (Layer 1) settings. Engine credentials stay in local
# environment/secret storage, never in Git.

from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.platform.workflow.events import EventSubscription


class FlowableSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_FLOWABLE_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    base_url: str = "http://127.0.0.1:18081/flowable-rest/service"
    username: str = "rest-admin"
    password: SecretStr = Field(default=SecretStr(""), repr=False)
    timeout_seconds: float = Field(default=15, gt=0, le=120, allow_inf_nan=False)
    # BPMN HttpTask targets must stay inside the FlowBridge boundary.
    allowed_callback_hosts: frozenset[str] = frozenset({"host.docker.internal"})

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
            if parsed.scheme == "http" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "host.docker.internal",
            }:
                valid = False
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use an HTTPS origin or a loopback/host-internal HTTP origin")
        return value.rstrip("/")


class WorkflowApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_WORKFLOW_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    # Public base URL of this Workflow API as seen from inside Flowable
    # containers (BPMN HttpTasks call back here through the Action Gateway).
    enabled: bool = False
    public_base_url: str = "http://host.docker.internal:18000"
    allowed_workflows: frozenset[str] = frozenset()
    start_variable_names: frozenset[str] = frozenset(
        {
            "order_id",
            "sku",
            "assignee",
            "data_request",
            "custom_request",
            "action_requests",
            "required_quantity",
        }
    )
    task_variable_names: frozenset[str] = frozenset({"packed", "approved"})
    service_bases: dict[str, str] = Field(default_factory=dict)
    allowed_http_hosts: frozenset[str] = frozenset()
    event_subscriptions: tuple[EventSubscription, ...] = ()
    event_webhook_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    engine_token: SecretStr = Field(default=SecretStr(""), repr=False)
    task_assignee_subjects: frozenset[str] = frozenset()
    hasura_relay_header_secret: SecretStr = Field(default=SecretStr(""), repr=False)
    hasura_relay_bindings: dict[str, dict] = Field(default_factory=dict)
    hasura_relay_target: str = "http://127.0.0.1:18000/api/v1/workflows/events"
    action_timeout_seconds: float = Field(default=20, gt=0, le=120, allow_inf_nan=False)
