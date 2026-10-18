# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Protocol/error tests using the real LFX component class and controlled upstream IO."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

SOURCE = Path(__file__).resolve().parents[2] / "components/supplier_rag/retrieve_knowledge.py"
SPEC = importlib.util.spec_from_file_location("rag_component_under_test", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TASK = "11111111-1111-4111-8111-111111111111"
SUPPLIER = "22222222-2222-4222-8222-222222222222"
TOKEN = "unit-only-service-token"
SECRET = "unit-only-client-secret"


@pytest.fixture
def wired(monkeypatch):
    for key in list(os.environ):
        if key.startswith("FLOWBRIDGE_RAG_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("FLOWBRIDGE_RAG_API_ORIGIN", "http://127.0.0.1:18083")
    monkeypatch.setenv("FLOWBRIDGE_RAG_TOKEN_URL", "https://identity.test/token")
    monkeypatch.setenv("FLOWBRIDGE_RAG_CLIENT_ID", "flowbridge-ai")
    monkeypatch.setenv("FLOWBRIDGE_RAG_CLIENT_SECRET", SECRET)
    body = {
        "query": "giao trễ?",
        "chunks": [
            {
                "reference": {
                    "document_id": TASK,
                    "document_version": TASK,
                    "chunk_id": TASK,
                    "source_uri": "core-document://fixture/version#bytes=0:20",
                    "checksum": "a" * 64,
                    "supplier_id": SUPPLIER,
                },
                "text": "Nhận xét: PO giao trễ 26 ngày.",
                "relevance_score": 1.0,
            }
        ],
        "index_version": "pgvector:fixture",
        "warnings": [],
    }
    state = {"body": body, "status": 200, "token_status": 200}
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "identity.test":
            return httpx.Response(
                state["token_status"],
                json={"access_token": TOKEN, "token_type": "Bearer"},
            )
        assert request.url == "http://127.0.0.1:18083/internal/v1/tools/retrieve-knowledge"
        return httpx.Response(state["status"], json=state["body"])

    original = httpx.AsyncClient
    monkeypatch.setattr(
        MODULE.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    component = MODULE.RetrieveSupplierKnowledge().set(
        query="giao trễ?", analysis_task_id=TASK, supplier_id=SUPPLIER, top_k=3
    )
    return component, state, requests


def test_component_fetches_service_token_and_preserves_job_and_citations(wired):
    component, state, requests = wired
    result = asyncio.run(component.retrieve_data())
    assert result.data == state["body"]
    assert parse_qs(requests[0].content.decode())["grant_type"] == ["client_credentials"]
    request = requests[1]
    assert request.headers["Authorization"] == "Bearer " + TOKEN
    assert request.headers["X-Job-Context"] == TASK
    assert request.headers["Idempotency-Key"].startswith("langflow-rag-")
    assert json.loads(request.content) == {
        "query": "giao trễ?",
        "supplier_id": SUPPLIER,
        "top_k": 3,
    }
    assert TOKEN not in str(component.status) and SECRET not in str(component.status)


def test_message_output_keeps_original_passage_and_reference(wired):
    component, state, _ = wired
    message = asyncio.run(component.retrieve_message())
    assert state["body"]["chunks"][0]["text"] in message.text
    assert "core-document://fixture/version#bytes=0:20" in message.text
    assert TOKEN not in message.text and SECRET not in message.text


def test_each_retrieval_refreshes_token_and_rechecks_upstream(wired):
    component, _, requests = wired
    asyncio.run(component.retrieve_data())
    asyncio.run(component.retrieve_data())
    assert len(requests) == 4
    assert requests[1].headers["Idempotency-Key"] != requests[3].headers["Idempotency-Key"]


@pytest.mark.parametrize(
    "status,code", [(401, "UNAUTHENTICATED"), (403, "FORBIDDEN"), (404, "NOT_FOUND")]
)
def test_upstream_denial_is_an_error_without_secret_or_empty_success(wired, status, code):
    component, state, _ = wired
    state.update(status=status, body={"code": code, "detail": SECRET + TOKEN})
    with pytest.raises(ValueError, match=f"HTTP {status}, {code}") as error:
        asyncio.run(component.retrieve_data())
    assert SECRET not in str(error.value) and TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("query", "  "),
        ("query", "q" * 2001),
        ("analysis_task_id", "forged-json"),
        ("supplier_id", "arbitrary-path"),
        ("top_k", 0),
        ("top_k", 21),
    ],
)
def test_invalid_inputs_are_rejected_before_credentials_or_network(wired, field, value):
    component, _, requests = wired
    component.set(**{field: value})
    with pytest.raises(ValueError, match="RAG_INPUT"):
        asyncio.run(component.retrieve_data())
    assert requests == []


@pytest.mark.parametrize(
    "origin",
    [
        "http://external.test",
        "https://user:pass@external.test",
        "https://external.test/path",
        "https://external.test?token=anything",
        "https://external.test#fragment",
    ],
)
def test_untrusted_origin_shape_is_rejected_before_credentials(wired, monkeypatch, origin):
    component, _, requests = wired
    monkeypatch.setenv("FLOWBRIDGE_RAG_API_ORIGIN", origin)
    with pytest.raises(ValueError, match="RAG_CONFIG"):
        asyncio.run(component.retrieve_data())
    assert requests == []


def test_wrong_supplier_in_response_is_rejected(wired):
    component, state, _ = wired
    state["body"]["chunks"][0]["reference"]["supplier_id"] = TASK
    with pytest.raises(ValueError, match="RAG_RESPONSE"):
        asyncio.run(component.retrieve_data())


def test_iam_failure_does_not_send_request_to_rag(wired):
    component, state, requests = wired
    state["token_status"] = 401
    with pytest.raises(ValueError, match="RAG_AUTH"):
        asyncio.run(component.retrieve_data())
    assert len(requests) == 1
