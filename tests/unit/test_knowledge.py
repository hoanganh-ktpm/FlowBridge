# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Local RAG invariants; synthetic vectors test plumbing, not model accuracy."""

import json
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from pydantic import ValidationError

from flowbridge.supplier_assistant.contract import validate_dto
from flowbridge.supplier_assistant.knowledge.chunking import chunk_document, tokenize
from flowbridge.supplier_assistant.knowledge.cli import main
from flowbridge.supplier_assistant.knowledge.indexing import build_index, load_snapshot
from flowbridge.supplier_assistant.knowledge.models import KnowledgeError, RetrievalScope
from flowbridge.supplier_assistant.knowledge.retrieval import (
    Retriever,
    fuse_ranks,
    local_operator_scope,
)
from flowbridge.supplier_assistant.knowledge.sources import LocalSources, sha256


class FakeEmbedder:
    profile = "deterministic-test-vector-v1"

    def __init__(self):
        self.calls = 0

    def documents(self, texts):
        self.calls += 1
        return [[1.0, float(index + 1)] for index, _ in enumerate(texts)]

    def query(self, text):
        return [1.0, 0.0]


class MemoryVectors:
    def __init__(self):
        self.collections = {}
        self.last_allowed = None

    def put(self, collection, chunks, vectors):
        assert len(chunks) == len(vectors)
        self.collections[collection] = {c.reference.chunk_id: c for c in chunks}

    def ids(self, collection):
        return set(self.collections.get(collection, {}))

    def search(self, collection, vector, documents, limit):
        self.last_allowed = documents
        return [
            key
            for key, chunk in self.collections[collection].items()
            if chunk.reference.document_id in documents
        ][:limit]


@pytest.fixture
def corpus(tmp_path):
    catalog = {"dataset_id": "test-dataset", "documents": []}
    for index in range(2):
        text = (
            f"# Nhà cung cấp {index}\n\n## Giao hàng\n\n"
            f"### Trường hợp {index}\n\nPO-S{index}-012 giao trễ; nhận xét của Nguyễn {index}.\n"
            "Chưa có dữ liệu khiếu nại, không kết luận tỷ lệ lỗi.\n"
            "\n```markdown\n# Đây là ví dụ, không phải tiêu đề\n```\n"
        )
        path = tmp_path / f"supplier-{index}.md"
        path.write_bytes(text.encode("utf-8"))
        catalog["documents"].append(
            {
                "document_id": str(uuid5(NAMESPACE_URL, f"doc-{index}")),
                "supplier_id": str(uuid5(NAMESPACE_URL, f"supplier-{index}")),
                "supplier_code": f"S{index}",
                "supplier_name": f"Supplier {index}",
                "path": path.name,
                "sha256": sha256(path.read_bytes()),
                "reviewer_user_id": str(uuid5(NAMESPACE_URL, f"reviewer-{index}")),
                "reviewer_name": f"Nguyễn {index}",
                "known_from": "2026-10-15T16:10:00Z",
                "report_asof": "2026-10-15T11:00:00Z",
            }
        )
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return LocalSources(path), tmp_path / "index", FakeEmbedder(), MemoryVectors()


def indexed(corpus):
    pytest.importorskip("rank_bm25")
    sources, root, embedder, vectors = corpus
    result = build_index(sources, root, embedder, vectors)
    return Retriever(sources, root, embedder, vectors), result


def save_catalog(sources, mutate):
    data = json.loads(sources.path.read_text(encoding="utf-8"))
    mutate(data)
    sources.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_chunks_preserve_hierarchy_unicode_offsets_reviewer_and_fences(corpus):
    sources, *_ = corpus
    spec = sources.catalog().documents[0]
    text = sources.read(spec)
    chunks = chunk_document("test", spec, text)
    assert len(chunks) == 3
    detail = chunks[-1]
    assert detail.heading_path == ["Nhà cung cấp 0", "Giao hàng", "Trường hợp 0"]
    assert detail.reviewer_name == "Nguyễn 0"
    assert sha256(text[detail.start_char : detail.end_char]) == detail.text_sha256
    assert detail.start_line == 5
    assert "không kết luận" in text[detail.start_char : detail.end_char]
    assert "# Đây là ví dụ" in text[detail.start_char : detail.end_char]
    assert detail.reference == chunk_document("test", spec, text)[-1].reference
    other = spec.model_copy(update={"document_id": uuid5(NAMESPACE_URL, "another-document")})
    assert detail.reference.chunk_id != chunk_document("test", other, text)[-1].reference.chunk_id


def test_large_sections_bounded_and_complete(corpus):
    sources, *_ = corpus
    spec = sources.catalog().documents[0]
    text = "# Long\n" + ("Không có dữ liệu lỗi.\n" * 400)
    chunks = chunk_document("test", spec, text)
    covered = set()
    for chunk in chunks:
        assert chunk.end_char - chunk.start_char <= 1000
        covered.update(range(chunk.start_char, chunk.end_char))
    assert covered == set(range(len(text)))


def test_unicode_tokens_keep_codes_and_negation():
    tokens = tokenize("DEMO-PO-00212: CHƯA có lỗi, không kết luận")
    assert "demo-po-00212" in tokens and "chưa" in tokens and "không" in tokens


def test_repeat_index_deduplicates_without_embedding_again(corpus):
    sources, root, embedder, vectors = corpus
    first = build_index(*corpus)
    second = build_index(*corpus)
    assert first == second
    assert embedder.calls == 1
    snapshot = load_snapshot(root)
    assert len(vectors.ids(snapshot.collection)) == first["indexed_chunks"]
    validate_dto(first, "KnowledgeIndexResult")
    assert not (root / ".write.lock").exists()


def test_filter_applies_before_both_channels_and_output_matches_contract(corpus):
    retriever, _ = indexed(corpus)
    sources, _, _, vectors = corpus
    first = sources.catalog().documents[0]
    scope = RetrievalScope(allowed_document_ids=frozenset({first.document_id}))
    response = retriever.search("PO-S0-012 giao trễ", scope, top_k=3)
    validate_dto(response.model_dump(mode="json"), "RetrievalResponse")
    assert response.chunks
    assert vectors.last_allowed == {str(first.document_id)}
    assert all(chunk.reference.supplier_id == first.supplier_id for chunk in response.chunks)
    assert all("Nguyễn 1" not in chunk.text for chunk in response.chunks)
    for chunk in response.chunks:
        source = retriever.resolve(chunk.reference.chunk_id, scope)
        assert source["text"] == chunk.text
        assert source["reviewer_user_id"] == str(first.reviewer_user_id)
        assert 0 <= chunk.relevance_score <= 1


def test_supplier_filter_and_empty_allowlist(corpus):
    retriever, _ = indexed(corpus)
    sources = corpus[0]
    supplier = sources.catalog().documents[1].supplier_id
    response = retriever.search("giao trễ", local_operator_scope(sources, supplier_id=supplier))
    assert all(chunk.reference.supplier_id == supplier for chunk in response.chunks)
    assert not retriever.search("giao trễ", RetrievalScope(allowed_document_ids=frozenset())).chunks


def test_known_from_excludes_future_and_rejects_naive_time(corpus):
    retriever, _ = indexed(corpus)
    scope = local_operator_scope(corpus[0], as_of="2026-10-15T16:09:00Z")
    assert not retriever.search("giao trễ", scope).chunks
    assert retriever.search(
        "giao trễ", local_operator_scope(corpus[0], as_of="2026-10-15T16:10:00Z")
    ).chunks
    with pytest.raises(ValidationError):
        local_operator_scope(corpus[0], as_of=datetime(2026, 10, 15))


def test_changed_source_blocked_before_reindex_and_old_reference_rejected(corpus):
    retriever, first = indexed(corpus)
    sources, root, _, _ = corpus
    doc = sources.catalog().documents[0]
    scope = RetrievalScope(allowed_document_ids=frozenset({doc.document_id}))
    old_id = retriever.search("PO-S0-012", scope).chunks[0].reference.chunk_id
    path = sources.root / doc.path
    path.write_bytes(path.read_bytes().replace(b"PO-S0-012", b"PO-NEW-999"))
    assert not retriever.search("PO-S0-012", scope).chunks
    with pytest.raises(KnowledgeError, match="CHUNK_UNAVAILABLE"):
        retriever.resolve(old_id, scope)
    save_catalog(
        sources, lambda data: data["documents"][0].update(sha256=sha256(path.read_bytes()))
    )
    second = build_index(*corpus)
    assert second["index_version"] != first["index_version"]
    assert retriever.search("PO-NEW-999", scope).chunks
    with pytest.raises(KnowledgeError, match="CHUNK_UNAVAILABLE"):
        retriever.resolve(old_id, scope)
    assert load_snapshot(root).index_version == second["index_version"]


def test_removed_document_excluded_without_reindex(corpus):
    retriever, _ = indexed(corpus)
    scope = local_operator_scope(corpus[0])
    removed = corpus[0].catalog().documents[0]
    save_catalog(corpus[0], lambda data: data["documents"].pop(0))
    response = retriever.search("giao trễ", scope)
    assert all(c.reference.document_id != str(removed.document_id) for c in response.chunks)
    assert any("SOURCE_BINDING_CHANGED" in warning for warning in response.warnings)


def test_failed_generation_does_not_replace_active_index(corpus):
    retriever, first = indexed(corpus)
    sources, root, embedder, _ = corpus
    embedder.profile = "different-profile"

    class BrokenVectors(MemoryVectors):
        def put(self, *args):
            raise KnowledgeError("VECTOR_STORE_WRITE_FAILED")

    with pytest.raises(KnowledgeError, match="VECTOR_STORE_WRITE_FAILED"):
        build_index(sources, root, embedder, BrokenVectors())
    assert load_snapshot(root).index_version == first["index_version"]
    assert not (root / ".write.lock").exists()
    with pytest.raises(KnowledgeError, match="EMBEDDING_PROFILE_MISMATCH"):
        retriever.search("giao", local_operator_scope(sources))


@pytest.mark.parametrize("bad_order", [[0, 0], [True, False], [99], "0,1", []])
def test_invalid_reranker_falls_back_without_duplicate_chunks(corpus, bad_order):
    retriever, _ = indexed(corpus)

    class BadReranker:
        def order(self, query, passages):
            return bad_order

    retriever.reranker = BadReranker()
    response = retriever.search("giao trễ", local_operator_scope(corpus[0]))
    ids = [chunk.reference.chunk_id for chunk in response.chunks]
    assert len(ids) == len(set(ids))
    assert any("RERANK_FAILED" in warning for warning in response.warnings)


def test_rrf_uses_ranks_once_and_stable_ties():
    scores = fuse_ranks(["a", "a", "b"], ["b", "a"])
    assert scores[0][0] == "a"
    assert scores[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[1][1] == pytest.approx(scores[0][1])


def test_sources_reject_path_escape_duplicates_and_wrong_checksum(corpus):
    sources = corpus[0]
    doc = sources.catalog().documents[0]
    with pytest.raises(KnowledgeError, match="INVALID_SOURCE_PATH"):
        sources.read(doc.model_copy(update={"path": "../outside.md"}))
    with pytest.raises(KnowledgeError, match="SOURCE_CHANGED"):
        sources.read(doc.model_copy(update={"sha256": "0" * 64}))
    save_catalog(sources, lambda data: data["documents"].append(data["documents"][0]))
    with pytest.raises(KnowledgeError, match="duplicate document IDs"):
        sources.catalog()


def test_cli_reports_actionable_missing_index(corpus, capsys):
    sources, root, *_ = corpus
    code = main(["--catalog", str(sources.path), "--index", str(root), "search", "giao trễ"])
    output = capsys.readouterr()
    assert code == 2 and "INDEX_UNAVAILABLE" in output.err and not output.out


def test_missing_vector_backend_never_becomes_empty_success(corpus):
    retriever, _ = indexed(corpus)

    def broken(*args):
        raise KnowledgeError("VECTOR_SEARCH_FAILED")

    retriever.vectors.search = broken
    with pytest.raises(KnowledgeError, match="VECTOR_SEARCH_FAILED"):
        retriever.search("giao trễ", local_operator_scope(corpus[0]))


def test_reviewer_rebinding_requires_new_generation(corpus):
    retriever, first = indexed(corpus)
    sources = corpus[0]
    scope = local_operator_scope(sources)
    doc_id = sources.catalog().documents[0].document_id
    save_catalog(sources, lambda data: data["documents"][0].update(reviewer_name="Reviewer mới"))
    response = retriever.search("giao", scope)
    assert all(c.reference.document_id != str(doc_id) for c in response.chunks)
    assert build_index(*corpus)["index_version"] != first["index_version"]


def test_fixture_catalog_contains_six_exact_sources():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sources = LocalSources(root / "examples/rag/catalog.json")
    catalog = sources.catalog()
    assert len(catalog.documents) == 6
    for spec in catalog.documents:
        text = sources.read(spec)
        assert spec.supplier_code in text and spec.reviewer_name in text
        UUID(str(spec.supplier_id))
