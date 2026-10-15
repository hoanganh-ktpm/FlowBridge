# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Validated runtime configuration; dotenv paths resolve from the working directory.

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


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
    rag_dev_enabled: bool = False
    rag_dev_catalog_path: Path = Path("examples/rag/catalog.json")
    rag_dev_index_path: Path = Path(".cache/rag")
    rag_dev_model_cache_path: Path = Path(".cache/models")
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

    @field_validator(
        "oidc_issuer", "oidc_audience", "oidc_introspection_url", "oidc_client_id", mode="before"
    )
    @classmethod
    def empty_identity_setting(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("oidc_issuer", "oidc_introspection_url")
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
        if self.rag_dev_enabled:
            if self.environment == "production":
                raise ValueError("rag_dev_enabled is unavailable in production")
            if self.host.lower() not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("rag_dev_enabled requires a loopback host")
            if self.database_url is not None:
                raise ValueError("rag_dev_enabled uses fixture sources and requires an empty DSN")
        if self.environment == "production" and self.database_url is None:
            raise ValueError("database_url is required in production")
        return self
