# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Validated runtime configuration; dotenv paths resolve from the working directory.

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
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
    sample_mode: bool = False
    sample_upload_max_bytes: int = Field(default=1048576, ge=1, le=10485760)
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10, gt=0, le=300)
    database_connect_timeout_seconds: float = Field(default=10, gt=0, le=300)
    database_command_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    http_timeout_seconds: float = Field(default=10, gt=0, le=300)
    http_max_connections: int = Field(default=100, ge=1, le=1000)
    health_timeout_seconds: float = Field(default=2, gt=0, le=30)

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
        # Chặn cấu hình production thiếu DSN dù development/test cho phép bỏ trống.
        if self.sample_mode:
            if self.environment == "production":
                raise ValueError("sample_mode is unavailable in production")
            if self.database_url is not None:
                raise ValueError("sample_mode requires an empty database_url")
        if self.environment == "production" and self.database_url is None:
            raise ValueError("database_url is required in production")
        return self
