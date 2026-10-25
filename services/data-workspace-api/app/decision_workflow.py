# SPDX-License-Identifier: GPL-3.0-or-later
"""Workspace-to-Core service credentials, renewable and independent of user sessions."""

import asyncio
import os
import ssl
from time import monotonic
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .decision_freshness import digest


class DecisionWorkflowClient:
    def __init__(self, core_url, token_url, client_id, secret, ca_file=None):
        for value in (core_url, token_url):
            url = urlsplit(value)
            if (
                not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (
                    url.scheme != "https"
                    and not (
                        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
                    )
                )
            ):
                raise ValueError("Use trusted HTTPS or loopback service origins")
        self.core_url, self.token_url = core_url.rstrip("/"), token_url
        self.client_id, self.secret = client_id, secret
        self.http = httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=ca_file), timeout=30, follow_redirects=False
        )
        self.cached, self.expires, self.lock = None, 0, asyncio.Lock()

    @classmethod
    def configured(cls, core_url):
        keys = [
            os.environ.get("FLOWBRIDGE_WORKSPACE_WORKFLOW_" + key)
            for key in ("TOKEN_URL", "CLIENT_ID", "CLIENT_SECRET")
        ]
        if not any(keys):
            return None
        if not all(keys):
            raise ValueError("Configure all Workspace workflow OAuth credentials")
        return cls(core_url, *keys, os.environ.get("FLOWBRIDGE_WORKSPACE_WORKFLOW_CA_FILE"))

    async def token(self):
        async with self.lock:
            if self.cached and monotonic() < self.expires:
                return self.cached
            try:
                response = await self.http.post(
                    self.token_url,
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.secret),
                )
                if response.status_code != 200:
                    raise HTTPException(503, "Workflow service identity unavailable")
                payload = response.json()
                token, lifetime = payload["access_token"], payload["expires_in"]
                if (
                    not isinstance(token, str)
                    or not token
                    or type(lifetime) is not int
                    or lifetime < 10
                ):
                    raise ValueError
                self.cached, self.expires = token, monotonic() + max(1, lifetime - 15)
                return token
            except (httpx.HTTPError, ValueError, KeyError):
                raise HTTPException(503, "Workflow service identity unavailable") from None

    async def request(self, path, body=None, key=None, params=None):
        if not path.startswith("/decision-runs/") or ".." in path or "?" in path or "#" in path:
            raise ValueError("Invalid decision Workflow path")
        try:
            headers = {"Authorization": "Bearer " + await self.token()}
            if key:
                headers["Idempotency-Key"] = digest(key)
            response = await self.http.request(
                "GET" if body is None else "POST",
                self.core_url + "/api/v1/workflows" + path,
                json=body,
                params=params,
                headers=headers,
            )
        except httpx.HTTPError:
            raise HTTPException(
                503, "Workflow command outcome is uncertain; retry the same command"
            ) from None
        if response.status_code not in {200, 201}:
            status = response.status_code if response.status_code in {403, 404, 409, 422} else 503
            raise HTTPException(status, "Core workflow denied or could not confirm the command")
        return response.json()

    async def close(self):
        await self.http.aclose()
