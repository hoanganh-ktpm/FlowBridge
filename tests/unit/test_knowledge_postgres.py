# SPDX-License-Identifier: GPL-3.0-or-later
"""Regressions for UTF-8 citations, temporal leakage, atomic readiness and live revocation."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from flowbridge.shared.errors import CoreError, ErrorCode
from flowbridge.supplier_assistant.knowledge.models import KnowledgeError, RetrievalRequest
from flowbridge.supplier_assistant.knowledge.postgres_domain import (
    PgScope,
    PgVersion,
    generation_for,
    latest_versions,
    prepare_chunks,
    validate_stored_chunks,
    vector_literal,
)
from flowbridge.supplier_assistant.knowledge.postgres_service import PgKnowledgeService
from flowbridge.supplier_assistant.knowledge.sources import sha256

TIME = datetime(2026, 10, 8, tzinfo=UTC)
PROFILE = "test-pinned-profile:1024:normalized"


def version(content="# Nhà cung cấp\n\n## Giao hàng\nNhận xét: trễ 26 ngày.\n", **changes):
    supplier = uuid4()
    row = PgVersion(
        uuid4(),
        uuid4(),
        1,
        supplier,
        "Nhà cung cấp",
        content,
        sha256(content),
        {
            "supplier_id": str(supplier),
            "reviewer_user_id": str(uuid4()),
            "reviewer_name_snapshot": "Nguyễn Minh",
            "report_asof": TIME.isoformat(),
        },
        TIME,
        TIME,
        "published",
    )
    return replace(row, **changes)


def stored(chunks):
    return [
        {
            "chunk_id": c.record.reference.chunk_id,
            "version_id": c.version_id,
            "index_generation": c.generation,
            "chunk_no": number,
            "content": c.content,
            "heading_path": c.record.heading_path,
            "source_start_byte": c.start_byte,
            "source_end_byte": c.end_byte,
            "source_checksum": c.record.reference.checksum,
            "chunk_checksum": c.record.text_sha256,
        }
        for number, c in enumerate(chunks)
    ]


def test_utf8_exact_slice_and_stable_citation():
    source = version()
    chunks = prepare_chunks(source, PROFILE)
    assert len(chunks) >= 2
    assert any(c.start_byte != c.record.start_char for c in chunks)
    for chunk in chunks:
        assert (
            source.content_md.encode()[chunk.start_byte : chunk.end_byte].decode() == chunk.content
        )
        assert f"#bytes={chunk.start_byte}:{chunk.end_byte}" in chunk.record.reference.source_uri
        assert chunk.record.reviewer_name == "Nguyễn Minh"
    assert prepare_chunks(source, PROFILE) == chunks
    assert generation_for(source, PROFILE) != generation_for(source, PROFILE + "new-revision")
    assert prepare_chunks(source, PROFILE + "new-revision")[0].record.reference.chunk_id != (
        chunks[0].record.reference.chunk_id
    )


def test_ab_selects_one_known_version_without_falling_back_after_withdrawal():
    v1 = version()
    v2 = replace(
        v1,
        version_id=uuid4(),
        version_no=2,
        published_at=TIME + timedelta(days=6),
        known_from=TIME + timedelta(days=6),
    )
    assert latest_versions([v1, v2], TIME - timedelta(seconds=1)) == []
    assert latest_versions([v1, v2], TIME + timedelta(days=1)) == [v1]
    assert latest_versions([v1, v2], TIME + timedelta(days=7)) == [v2]
    withdrawn = replace(v2, status="withdrawn", withdrawn_at=TIME + timedelta(days=8))
    assert latest_versions([v1, withdrawn], TIME + timedelta(days=9)) == []
    # Policy: current withdrawal veto also applies to historical replay of that source.
    assert latest_versions([v1, withdrawn], TIME + timedelta(days=7)) == []
    assert latest_versions([v1, withdrawn], TIME + timedelta(days=1)) == [v1]


def test_draft_not_retrievable_and_report_date_does_not_backdate_knowledge():
    source = version(metadata={**version().metadata, "report_asof": "2020-01-01T00:00:00Z"})
    assert latest_versions([source], TIME - timedelta(days=1)) == []
    assert latest_versions([replace(source, status="draft")], TIME + timedelta(days=1)) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("content", "forged quote"),
        ("source_end_byte", 1),
        ("chunk_no", 100),
        ("chunk_checksum", "0" * 64),
        ("index_generation", "staging"),
    ],
)
def test_corrupt_or_partial_generation_is_not_ready(field, value):
    chunks = prepare_chunks(version(), PROFILE)
    rows = stored(chunks)
    assert validate_stored_chunks(chunks, rows)
    assert not validate_stored_chunks(chunks, rows[:-1])
    rows[0][field] = value
    assert not validate_stored_chunks(chunks, rows)


@pytest.mark.parametrize("vector", [[1.0], [0.0] * 1024, [float("nan")] * 1024, [1.0] * 1024])
def test_bad_embeddings_fail_before_write(vector):
    with pytest.raises(KnowledgeError):
        vector_literal(vector)


def test_normalized_1024_vector_is_accepted():
    assert vector_literal([1.0] + [0.0] * 1023).startswith("[1,0,")


def test_checksum_mismatch_fails_before_chunking():
    with pytest.raises(KnowledgeError, match="SOURCE_CHANGED"):
        prepare_chunks(version(checksum="0" * 64), PROFILE)


class Embedder:
    profile = PROFILE
    calls = 0

    def query(self, text):
        self.calls += 1
        return [1.0] + [0.0] * 1023

    def documents(self, texts):
        self.calls += 1
        return [[1.0] + [0.0] * 1023 for _ in texts]


class Store:
    def __init__(self, source, ready=True):
        self.source = source
        self.expected = prepare_chunks(source, PROFILE)
        self.rows = stored(self.expected) if ready else []
        self.writes = 0
        self.revoked = False

    async def versions(self, scope, latest=True):
        return [] if self.revoked else [self.source]

    async def chunks(self, chunks, profile):
        return self.rows

    async def dense(self, chunks, profile, vector):
        return [c.record.reference.chunk_id for c in reversed(chunks)]

    async def write_generation(self, version, chunks, vectors, profile, scope):
        self.rows = stored(chunks)
        self.writes += 1


@pytest.mark.asyncio
async def test_search_waits_for_manager_gate_then_revalidates_scope():
    service = PgKnowledgeService(Store(version()), Embedder())
    await service._gate.acquire()
    calls = 0

    async def scope():
        nonlocal calls
        calls += 1
        return PgScope(TIME)

    task = asyncio.create_task(service.search(RetrievalRequest(query="giao trễ"), scope))
    await asyncio.sleep(0)
    assert not task.done()
    assert calls == 0
    service._gate.release()
    result = await task
    assert result["chunks"]
    assert calls == 2
    assert service._pending_searches == 0
    assert not service._gate.locked()


@pytest.mark.asyncio
async def test_search_queue_timeout_does_not_release_another_workloads_gate():
    service = PgKnowledgeService(Store(version()), Embedder())
    service.SEARCH_WAIT_SECONDS = 0.01
    await service._gate.acquire()
    with pytest.raises(CoreError) as caught:
        async with service.search_slot():
            pytest.fail("Busy slot must not execute")
    assert caught.value.code == ErrorCode.RATE_LIMITED
    assert service._gate.locked()
    assert service._pending_searches == 0
    service._gate.release()


@pytest.mark.asyncio
async def test_search_queue_is_bounded_and_cancellation_cleans_waiters():
    service = PgKnowledgeService(Store(version()), Embedder())
    await service._gate.acquire()

    async def wait():
        async with service.search_slot():
            pytest.fail("Gate has not been released")

    tasks = [asyncio.create_task(wait()) for _ in range(service.MAX_SEARCH_REQUESTS)]
    await asyncio.sleep(0)
    with pytest.raises(CoreError) as caught:
        async with service.search_slot():
            pytest.fail("Queue is full")
    assert caught.value.code == ErrorCode.RATE_LIMITED
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert service._pending_searches == 0
    assert service._gate.locked()
    service._gate.release()
    async with service.search_slot():
        assert service._gate.locked()
    assert not service._gate.locked()


@pytest.mark.asyncio
async def test_query_excludes_partial_index_and_never_calls_embedding():
    source = version()
    store, embedder = Store(source, ready=False), Embedder()
    service = PgKnowledgeService(store, embedder)

    async def scope():
        return PgScope(TIME)

    result = await service.search(RetrievalRequest(query="giao trễ"), scope)
    assert result["chunks"] == []
    assert any(w.startswith("INDEX_NOT_READY") for w in result["warnings"])
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_live_source_revocation_removes_quote_before_response():
    store, embedder = Store(version()), Embedder()
    service = PgKnowledgeService(store, embedder)
    checks = 0

    async def scope():
        nonlocal checks
        checks += 1
        if checks == 2:
            store.revoked = True
        return PgScope(TIME)

    result = await service.search(RetrievalRequest(query="giao trễ"), scope)
    assert result["chunks"] == []
    assert "SOURCE_CHANGED_DURING_QUERY" in result["warnings"]


@pytest.mark.asyncio
async def test_live_role_revocation_propagates_forbidden():
    service = PgKnowledgeService(Store(version()), Embedder())
    checks = 0

    async def scope():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise CoreError(ErrorCode.FORBIDDEN)
        return PgScope(TIME)

    with pytest.raises(CoreError) as caught:
        await service.search(RetrievalRequest(query="giao trễ"), scope)
    assert caught.value.code == ErrorCode.FORBIDDEN


@pytest.mark.asyncio
async def test_index_retry_reuses_verified_generation_without_embedding():
    source, embedder = version(), Embedder()
    store = Store(source, ready=False)
    service = PgKnowledgeService(store, embedder)

    async def scope():
        return PgScope(TIME)

    first = await service.index_versions([source], scope)
    second = await service.index_versions([source], scope)
    assert first == second
    assert store.writes == 1
    assert embedder.calls == 1


@pytest.mark.asyncio
async def test_forged_index_reference_is_rejected_before_embedding():
    source, embedder = version(), Embedder()
    service = PgKnowledgeService(Store(source), embedder)
    ref = prepare_chunks(source, PROFILE)[0].record.reference.model_dump(mode="json")
    ref["source_uri"] = "https://untrusted.example/forged"

    async def scope():
        return PgScope(TIME)

    with pytest.raises(CoreError):
        await service.index_versions([source], scope, references=[ref])
    assert embedder.calls == 0
