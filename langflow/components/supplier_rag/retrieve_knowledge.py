# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Read-only RAG tool. Service credentials and trusted URLs stay on the Langflow server."""

import math
import os
import re
import ssl
from uuid import UUID, uuid4

import httpx
from lfx.custom import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput
from lfx.schema import Data
from lfx.schema.message import Message


class RetrieveSupplierKnowledge(Component):
    display_name = "FlowBridge - Supplier Knowledge"
    description = "Retrieve supplier evidence and citations through the authenticated RAG API."
    icon = "BookOpen"
    name = "FlowBridgeRetrieveKnowledge"
    inputs = [
        MessageTextInput(name="query", display_name="Question", required=True, tool_mode=True),
        MessageTextInput(name="analysis_task_id", display_name="Analysis task UUID", required=True),
        MessageTextInput(
            name="supplier_id", display_name="Supplier UUID (optional)", value="", tool_mode=True
        ),
        IntInput(name="top_k", display_name="Top passages", value=3, tool_mode=True),
        SecretStrInput(
            name="access_token", display_name="Service bearer token (optional)", advanced=True
        ),
    ]
    outputs = [
        Output(name="evidence", display_name="Evidence data", method="retrieve_data"),
        Output(name="passages", display_name="Cited passages", method="retrieve_message"),
    ]

    @staticmethod
    def _origin(value: str) -> str:
        try:
            origin = httpx.URL(value)
        except Exception:
            raise ValueError("RAG_CONFIG: invalid API origin") from None
        if (
            origin.scheme not in {"http", "https"}
            or not origin.host
            or origin.userinfo
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or (origin.scheme == "http" and origin.host not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("RAG_CONFIG: API origin requires HTTPS or local loopback HTTP")
        return str(origin).rstrip("/")

    @staticmethod
    def _error(response: httpx.Response, prefix: str) -> ValueError:
        code = "UPSTREAM_ERROR"
        try:
            candidate = response.json().get("code", "")
            if isinstance(candidate, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", candidate):
                code = candidate
        except (ValueError, AttributeError):
            pass
        return ValueError(f"{prefix}: HTTP {response.status_code}, {code}")

    async def _bearer(self, client: httpx.AsyncClient) -> str:
        value = self.access_token
        if value:
            token = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
            if token.strip():
                return token.strip()
        token_url = os.environ.get("FLOWBRIDGE_RAG_TOKEN_URL", "")
        client_id = os.environ.get("FLOWBRIDGE_RAG_CLIENT_ID", "")
        secret = os.environ.get("FLOWBRIDGE_RAG_CLIENT_SECRET", "")
        try:
            url = httpx.URL(token_url)
        except Exception:
            raise ValueError("RAG_CONFIG: invalid token endpoint") from None
        if (
            url.scheme != "https"
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or not client_id
            or not secret
        ):
            raise ValueError("RAG_CONFIG: configure the service token or HTTPS client credentials")
        response = await client.post(
            str(url),
            data={"grant_type": "client_credentials", "client_id": client_id},
            auth=httpx.BasicAuth(client_id, secret),
        )
        if response.status_code != 200:
            raise self._error(response, "RAG_AUTH")
        try:
            data = response.json()
            token = data["access_token"]
            if (
                not isinstance(token, str)
                or not token
                or data.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("RAG_AUTH: invalid token response") from None
        return token

    async def _retrieve(self) -> dict:
        origin = self._origin(os.environ.get("FLOWBRIDGE_RAG_API_ORIGIN", "http://127.0.0.1:8000"))
        query = str(self.query).strip()
        if not query or len(query) > 2000:
            raise ValueError("RAG_INPUT: question must contain 1 to 2000 characters")
        try:
            task_id = str(UUID(str(self.analysis_task_id)))
            supplier = str(self.supplier_id or "").strip()
            supplier_id = str(UUID(supplier)) if supplier else None
        except (ValueError, TypeError, AttributeError):
            raise ValueError("RAG_INPUT: task and optional supplier must be UUIDs") from None
        if type(self.top_k) is not int or not 1 <= self.top_k <= 20:
            raise ValueError("RAG_INPUT: top_k must be between 1 and 20")
        payload = {"query": query, "top_k": self.top_k}
        if supplier_id:
            payload["supplier_id"] = supplier_id
        try:
            context = ssl.create_default_context(cafile=os.environ.get("FLOWBRIDGE_RAG_CA_BUNDLE"))
            async with httpx.AsyncClient(
                verify=context,
                timeout=httpx.Timeout(180, connect=10),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                token = await self._bearer(client)
                response = await client.post(
                    origin + "/internal/v1/tools/retrieve-knowledge",
                    json=payload,
                    headers={
                        "Authorization": "Bearer " + token,
                        "X-Job-Context": task_id,
                        "Idempotency-Key": "langflow-rag-" + str(uuid4()),
                    },
                )
        except httpx.TimeoutException:
            raise ValueError("RAG_TIMEOUT: upstream request timed out") from None
        except (httpx.HTTPError, OSError):
            raise ValueError(
                "RAG_UNAVAILABLE: check the configured API, IAM and trusted CA"
            ) from None
        if response.status_code != 200:
            raise self._error(response, "RAG_RETRIEVAL")
        try:
            data = response.json()
            if not isinstance(data, dict) or data["query"] != query:
                raise ValueError
            if not isinstance(data["chunks"], list) or len(data["chunks"]) > self.top_k:
                raise ValueError
            if not isinstance(data["index_version"], str) or not isinstance(data["warnings"], list):
                raise ValueError
            for chunk in data["chunks"]:
                reference = chunk["reference"]
                if not isinstance(chunk["text"], str) or not chunk["text"].strip():
                    raise ValueError
                score = chunk["relevance_score"]
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                ):
                    raise ValueError
                for key in (
                    "document_id",
                    "document_version",
                    "chunk_id",
                    "source_uri",
                    "checksum",
                ):
                    if not isinstance(reference[key], str) or not reference[key]:
                        raise ValueError
                if supplier_id and reference.get("supplier_id") != supplier_id:
                    raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError("RAG_RESPONSE: invalid evidence or citation response") from None
        self.status = {"passages": len(data["chunks"]), "index_version": data["index_version"]}
        return data

    async def retrieve_data(self) -> Data:
        return Data(data=await self._retrieve())

    async def retrieve_message(self) -> Message:
        result = await self._retrieve()
        if not result["chunks"]:
            return Message(text="Không tìm thấy tài liệu phù hợp trong phạm vi tác vụ này.")
        passages = []
        for number, chunk in enumerate(result["chunks"], 1):
            reference = chunk["reference"]
            passages.append(
                f"[{number}] {chunk['text']}\n\nNguồn: {reference['source_uri']}\n"
                f"Tài liệu: {reference['document_id']} | Phiên bản: {reference['document_version']}"
            )
        return Message(text="\n\n---\n\n".join(passages))
