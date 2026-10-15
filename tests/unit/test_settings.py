# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge.
# FlowBridge is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
# FlowBridge is distributed without any warranty; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE.

# Configuration validation, precedence and secret handling.

import os

import pytest
from pydantic import ValidationError

from flowbridge.settings import Settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    for name in os.environ:
        if name.startswith("FLOWBRIDGE_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def test_dotenv_environment_and_explicit_precedence(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("FLOWBRIDGE_PORT=8123\nFLOWBRIDGE_LOG_LEVEL=WARNING\n")
    assert Settings().port == 8123
    monkeypatch.setenv("FLOWBRIDGE_PORT", "8124")
    assert Settings().port == 8124
    assert Settings(port=8125).port == 8125
    assert Settings(_env_file=None).log_level == "INFO"


def test_template_loads_without_credentials():
    settings = Settings(database_url="")
    assert settings.database_url is None
    assert settings.docs_enabled is False
    assert settings.environment == "development"


@pytest.mark.parametrize(
    "configuration",
    [
        {"port": 0},
        {"port": 65536},
        {"http_timeout_seconds": 0},
        {"health_timeout_seconds": 0},
        {"health_timeout_seconds": 31},
        {"database_pool_size": 0},
        {"database_max_overflow": -1},
        {"environment": "staging"},
        {"log_level": "secret-invalid-level"},
        {"environment": "production"},
    ],
)
def test_invalid_configuration_is_rejected(configuration):
    with pytest.raises(ValidationError):
        Settings(**configuration)


@pytest.mark.parametrize(
    "url",
    [
        "invalid-sensitive-url",
        "sqlite+aiosqlite:///database",
        "postgresql://user:secret-password@localhost/database",
        "postgresql+asyncpg://user:secret-password@localhost",
        "postgresql+asyncpg://user:secret-password@localhost:99999/database",
        "postgresql+asyncpg://user:secret-password@localhost:bad/database",
    ],
)
def test_invalid_dsn_is_rejected_without_printing_input(url):
    with pytest.raises(ValidationError) as error:
        Settings(database_url=url)
    assert url not in str(error.value)
    assert "secret-password" not in str(error.value)


def test_valid_secret_is_redacted_and_settings_are_immutable():
    settings = Settings(
        environment="production",
        database_url="postgresql+asyncpg://user:secret-password@localhost/database",
    )
    assert "secret-password" not in repr(settings)
    assert "secret-password" not in settings.model_dump_json()
    with pytest.raises(ValidationError):
        settings.port = 8123
