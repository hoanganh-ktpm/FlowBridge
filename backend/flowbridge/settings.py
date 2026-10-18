# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Validated runtime configuration; dotenv paths resolve from the working directory.

from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from flowbridge.identity.binding import IdentityBinding


class Settings(BaseSettings):
    # Cấu hình bất biến; ưu tiên constructor → biến môi trường → .env → mặc định.

    model_config = SettingsConfigDict(
        env_prefix="FLOWBRIDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    docs_enabled: bool = False
    http_access_log_enabled: bool = False
    # Empty by default: direct clients cannot supply trusted forwarding metadata.
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    rag_postgres_enabled: bool = False
    rag_postgres_model_cache_path: Path = Path(".cache/models")
    rag_dev_enabled: bool = False
    rag_dev_catalog_path: Path = Path("examples/rag/catalog.json")
    rag_dev_index_path: Path = Path(".cache/rag")
    rag_dev_model_cache_path: Path = Path(".cache/models")
    ml_enabled: bool = False
    ml_context_path: Path = Path(".cache/ml/context.json")
    ml_checkpoint_path: Path = Path(".cache/models/tabicl-classifier-v2-20260212.ckpt")
    ml_checkpoint_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    ml_model_version: Literal["tabicl-classifier-v2-20260212.ckpt"] = (
        "tabicl-classifier-v2-20260212.ckpt"
    )
    ml_device: Literal["cpu", "cuda"] = "cpu"
    ml_n_estimators: int = Field(default=8, ge=1, le=32)
    ml_cpu_threads: int = Field(default=4, ge=1, le=64)
    ml_max_context_rows: int = Field(default=5000, ge=2, le=50000)
    ml_max_candidates: int = Field(default=100, ge=1, le=1000)
    ml_max_history_rows: int = Field(default=20000, ge=100, le=1000000)
    ml_history_days: int = Field(default=180, ge=1, le=3650)
    ml_queue_timeout_seconds: float = Field(default=5, gt=0, le=60)
    ml_request_timeout_seconds: float = Field(default=120, gt=0, le=600)
    ml_prediction_max_age_seconds: int = Field(default=86400, ge=1, le=604800)
    ml_risk_threshold: float = Field(default=0.70, ge=0, le=1, allow_inf_nan=False)
    # False until operations confirms receipt reporting coverage; absence is not zero.
    ml_receipt_coverage_confirmed: bool = False
    database_url: SecretStr | None = None
    database_host: str | None = Field(default=None, min_length=1)
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = Field(default="postgres", min_length=1)
    database_user: str | None = Field(default=None, min_length=1)
    database_password: SecretStr | None = None
    database_ssl: Literal["disable", "require", "verify-full"] = "require"
    database_pool_mode: Literal["queue", "null"] = "queue"
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10, gt=0, le=300)
    database_connect_timeout_seconds: float = Field(default=10, gt=0, le=300)
    database_command_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    http_timeout_seconds: float = Field(default=10, gt=0, le=300)
    http_max_connections: int = Field(default=100, ge=1, le=1000)
    health_timeout_seconds: float = Field(default=2, gt=0, le=30)
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_introspection_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_ca_bundle: Path | None = None
    oidc_allowed_clients: list[str] = Field(default_factory=list)
    oidc_service_clients: list[str] = Field(default_factory=list)
    oidc_admin_url: str | None = None
    oidc_admin_client_id: str | None = None
    oidc_admin_client_secret: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)
    core_identity: IdentityBinding | None = None
    chat_enabled: bool = False
    langflow_url: str = "http://127.0.0.1:7860"
    langflow_flow_id: str = Field(default="flowbridge-ver2-central", pattern=r"^[A-Za-z0-9_-]+$")
    langflow_api_key: SecretStr | None = None
    langflow_bridge_secret: SecretStr | None = None
    langflow_timeout_seconds: float = Field(default=180, gt=0, le=600)
    langflow_ai_client_id: str | None = None
    langflow_ai_client_secret: SecretStr | None = None

    @field_validator("langflow_url")
    @classmethod
    def validate_langflow_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise ValueError("langflow_url must be a fixed HTTP origin")
        return value.rstrip("/")

    def validate_chat(self) -> Self:
        if self.chat_enabled and not all(
            (
                self.database_url,
                self.oidc_issuer,
                self.oidc_audience,
                self.oidc_introspection_url,
                self.oidc_client_id,
                self.oidc_client_secret,
                self.langflow_api_key,
                self.langflow_bridge_secret,
            )
        ):
            raise ValueError("chat requires database, OIDC, Langflow API key and bridge secret")
        if self.langflow_bridge_secret and len(self.langflow_bridge_secret.get_secret_value()) < 32:
            raise ValueError("langflow_bridge_secret must contain at least 32 characters")
        if bool(self.langflow_ai_client_id) != bool(self.langflow_ai_client_secret):
            raise ValueError("Configure both Langflow AI client credentials or neither")
        return self

    @field_validator("trusted_proxy_ips")
    @classmethod
    def validate_trusted_proxy_ips(cls, values: list[str]) -> list[str]:
        for value in values:
            if "/" in value:
                if ip_network(value, strict=True).prefixlen == 0:
                    raise ValueError("Proxy trust must not include every address")
            else:
                ip_address(value)
        return values

    @field_validator(
        "oidc_ca_bundle", "oidc_client_secret", "oidc_admin_client_secret", mode="before"
    )
    @classmethod
    def empty_secret_or_path(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("oidc_allowed_clients", "oidc_service_clients")
    @classmethod
    def validate_client_registry(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Client IDs must be nonblank without surrounding whitespace")
        if len(values) != len(set(values)):
            raise ValueError("Client IDs must be unique")
        return values

    @field_validator(
        "oidc_issuer",
        "oidc_audience",
        "oidc_introspection_url",
        "oidc_client_id",
        "oidc_admin_url",
        "oidc_admin_client_id",
        mode="before",
    )
    @classmethod
    def empty_identity_setting(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("oidc_issuer", "oidc_introspection_url", "oidc_admin_url")
    @classmethod
    def validate_identity_url(cls, value: str | None) -> str | None:
        if value is not None:
            parts = urlsplit(value)
            if (
                parts.scheme != "https"
                or not parts.netloc
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Identity URLs must be HTTPS without credentials/query/fragment")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = urlsplit(value)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.netloc
                or parts.username
                or parts.password
                or parts.path
                or parts.query
                or parts.fragment
                or "*" in value
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths")
        return values

    @field_validator("database_url", mode="before")
    @classmethod
    def empty_database_url(cls, value: object) -> object:
        # Cho phép DSN trống ở development/test mà không tạo database giả.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        # Chỉ kiểm tra cấu trúc DSN, không kết nối hoặc trả lại URL trong lỗi.
        if value is None:
            return value
        try:
            url = make_url(value.get_secret_value())
            valid = (
                url.drivername == "postgresql+asyncpg"
                and bool(url.host)
                and bool(url.database)
                and (url.port is None or 1 <= url.port <= 65535)
            )
        except (ArgumentError, ValueError):
            valid = False
        if not valid:
            raise ValueError("database_url must be a postgresql+asyncpg URL with host and database")
        return value

    @model_validator(mode="after")
    def require_production_database(self) -> Self:
        # URL.create accepts a raw password, including @, /, %, # and other special characters.
        # An explicit DSN takes precedence; a blank password keeps development unconfigured.
        password = self.database_password.get_secret_value() if self.database_password else ""
        if self.database_url is None and password:
            if not self.database_host or not self.database_user:
                raise ValueError(
                    "database_host and database_user are required with database_password"
                )
            url = URL.create(
                "postgresql+asyncpg",
                username=self.database_user,
                password=password,
                host=self.database_host,
                port=self.database_port,
                database=self.database_name,
            )
            object.__setattr__(
                self, "database_url", SecretStr(url.render_as_string(hide_password=False))
            )
        # Chặn cấu hình production thiếu DSN dù development/test cho phép bỏ trống.
        if self.rag_postgres_enabled:
            if self.database_url is None:
                raise ValueError("rag_postgres_enabled requires a configured PostgreSQL database")
            if self.rag_dev_enabled:
                raise ValueError("Choose PostgreSQL RAG or the local fixture RAG mode")
        if self.core_identity is not None:
            binding = self.core_identity
            required_clients = {
                binding.web_client_id,
                binding.mobile_client_id,
                *binding.service_clients,
            }
            if not all(
                (
                    self.database_url,
                    self.oidc_issuer,
                    self.oidc_audience,
                    self.oidc_introspection_url,
                    self.oidc_client_id,
                    self.oidc_client_secret,
                )
            ):
                raise ValueError("core_identity requires database and complete OIDC introspection")
            if not required_clients <= set(self.oidc_allowed_clients):
                raise ValueError("Core clients must be in oidc_allowed_clients")
            if not set(binding.service_clients) <= set(self.oidc_service_clients):
                raise ValueError("Core service clients must be in oidc_service_clients")
            if {binding.web_client_id, binding.mobile_client_id} & set(self.oidc_service_clients):
                raise ValueError("Public core clients cannot be service clients")
            if self.oidc_introspection_url != (
                self.oidc_issuer.rstrip("/") + "/protocol/openid-connect/token/introspect"
            ):
                raise ValueError("Core introspection must belong to the configured Keycloak issuer")
        if self.rag_dev_enabled:
            if self.environment == "production":
                raise ValueError("rag_dev_enabled is unavailable in production")
            if self.host.lower() not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("rag_dev_enabled requires a loopback host")
            if self.database_url is not None:
                raise ValueError("rag_dev_enabled uses fixture sources and requires an empty DSN")
        if self.environment == "production" and self.database_url is None:
            raise ValueError("database_url is required in production")
        if self.ml_enabled and self.database_url is None:
            raise ValueError("ml_enabled requires the application database")
        return self.validate_chat()
