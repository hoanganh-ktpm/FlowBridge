# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import os
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import httpx
from dotenv import dotenv_values

LANGFLOW_ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-5.6-luna"


def local_values() -> dict[str, str]:
    path = Path(os.environ.get("FLOWBRIDGE_AGENT_ENV_FILE", LANGFLOW_ROOT / "data/agent.env"))
    return {**{k: v for k, v in dotenv_values(path).items() if v}, **os.environ}


@dataclass(frozen=True)
class BackendConfig:
    origin: str = "http://127.0.0.1:8000"
    user_token: str = field(default="", repr=False)
    ai_token: str = field(default="", repr=False)
    job_context: str = ""
    max_pages: int = 5
    timeout: float = 150

    def __post_init__(self) -> None:
        url = httpx.URL(self.origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("Configure a fixed API origin without credentials, query or path")
        if not 1 <= self.max_pages <= 10:
            raise ValueError("max_pages must be between 1 and 10")
        if not 1 <= self.timeout <= 600:
            raise ValueError("timeout must be between 1 and 600 seconds")
        if self.job_context:
            UUID(self.job_context)

    @classmethod
    def from_local(cls) -> "BackendConfig":
        values = local_values()
        return cls(
            origin=values.get("FLOWBRIDGE_API_ORIGIN", "http://127.0.0.1:8000"),
            user_token=values.get("FLOWBRIDGE_USER_ACCESS_TOKEN", ""),
            ai_token=values.get("FLOWBRIDGE_AI_ACCESS_TOKEN", ""),
            job_context=values.get("FLOWBRIDGE_JOB_CONTEXT", ""),
        )
