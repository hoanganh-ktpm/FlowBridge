# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Real persistent Chroma round trip with tiny deterministic vectors; no ML download."""

import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from flowbridge.supplier_assistant.contract import validate_dto
from flowbridge.supplier_assistant.knowledge.indexing import build_index, load_snapshot
from flowbridge.supplier_assistant.knowledge.retrieval import Retriever, local_operator_scope
from flowbridge.supplier_assistant.knowledge.sources import LocalSources, sha256
from flowbridge.supplier_assistant.knowledge.vector_store import ChromaVectors

pytestmark = pytest.mark.integration


def test_real_chroma_persists_reuses_filters_and_switches_generations(tmp_path):
    pytest.importorskip("chromadb")
    pytest.importorskip("rank_bm25")

    class TinyEmbedder:
        profile = "chroma-integration-test-vector-v1"
        calls = 0

        def documents(self, texts):
            self.calls += 1
            return [[1.0, float(index + 1), 0.2] for index, _ in enumerate(texts)]

        def query(self, text):
            return [1.0, 1.0, 0.2]

    documents = []
    for index in range(2):
        text = f"# Supplier {index}\n\n## Giao trễ\n\nPO-{index} đến trễ.\n"
        (tmp_path / f"{index}.md").write_bytes(text.encode("utf-8"))
        documents.append(
            {
                "document_id": str(uuid5(NAMESPACE_URL, f"chroma-doc-{index}")),
                "path": f"{index}.md",
                "supplier_id": str(uuid5(NAMESPACE_URL, f"ncc-{index}")),
                "supplier_code": f"S{index}",
                "supplier_name": f"Supplier {index}",
                "reviewer_user_id": str(uuid5(NAMESPACE_URL, f"author-{index}")),
                "reviewer_name": f"Author {index}",
                "sha256": sha256(text),
                "known_from": "2026-10-15T16:10:00Z",
                "report_asof": "2026-10-15T11:00:00Z",
            }
        )
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps({"dataset_id": "chroma-test", "documents": documents}), encoding="utf-8"
    )
    sources = LocalSources(path)
    root = tmp_path / "index"
    vectors = ChromaVectors(root / "chroma")
    embedder = TinyEmbedder()
    first = build_index(sources, root, embedder, vectors)
    second = build_index(sources, root, embedder, ChromaVectors(root / "chroma"))
    assert first == second and embedder.calls == 1
    snapshot = load_snapshot(root)
    assert len(vectors.ids(snapshot.collection)) == first["indexed_chunks"]
    scope = local_operator_scope(sources, supplier_id=sources.catalog().documents[0].supplier_id)
    response = Retriever(sources, root, embedder, vectors).search("PO-0 giao trễ", scope)
    assert response.chunks
    assert all(chunk.reference.supplier_id == scope.supplier_id for chunk in response.chunks)
    validate_dto(response.model_dump(mode="json"), "RetrievalResponse")
    (tmp_path / "0.md").write_text("# Supplier 0\n\n## Đã cập nhật\n\nPO mới.\n", encoding="utf-8")
    documents[0]["sha256"] = sha256((tmp_path / "0.md").read_bytes())
    path.write_text(
        json.dumps({"dataset_id": "chroma-test", "documents": documents}), encoding="utf-8"
    )
    updated = build_index(sources, root, embedder, vectors)
    assert updated["index_version"] != first["index_version"]
    response = Retriever(sources, root, embedder, vectors).search("PO mới", scope)
    assert all("đến trễ" not in chunk.text for chunk in response.chunks)
