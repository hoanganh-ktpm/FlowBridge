# SPDX-License-Identifier: GPL-3.0-or-later
"""Export one synthetic supplier and actual adapter SQL for optional PGlite validation."""

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from flowbridge.supplier_assistant.knowledge.embeddings import QwenEmbedder
from flowbridge.supplier_assistant.knowledge.postgres_domain import PgVersion, prepare_chunks
from flowbridge.supplier_assistant.knowledge.sources import LocalSources


def main():
    root = Path(__file__).resolve().parents[1]
    source = LocalSources(root / "examples/rag/catalog.json")
    spec = source.catalog().documents[0]
    content = source.read(spec)
    timestamp = datetime.now(UTC)
    version = PgVersion(
        spec.document_id,
        uuid4(),
        1,
        spec.supplier_id,
        spec.supplier_name,
        content,
        spec.sha256,
        {
            "supplier_id": str(spec.supplier_id),
            "reviewer_user_id": str(spec.reviewer_user_id),
            "reviewer_name_snapshot": spec.reviewer_name,
            "report_asof": spec.report_asof.isoformat(),
        },
        timestamp,
        timestamp,
        "published",
    )
    chunks = prepare_chunks(version, QwenEmbedder.profile)
    fixture = {
        "document_id": str(version.document_id),
        "version_id": str(version.version_id),
        "supplier_id": str(version.supplier_id),
        "content": content,
        "checksum": version.checksum,
        "metadata": version.metadata,
        "profile": QwenEmbedder.profile,
        "generation": chunks[0].generation,
        "chunks": [
            {
                "id": c.record.reference.chunk_id,
                "version": str(c.version_id),
                "number": i,
                "content": c.content,
                "headings": c.record.heading_path,
                "start": c.start_byte,
                "end": c.end_byte,
                "source": version.checksum,
                "checksum": c.record.text_sha256,
                "embedding": "[" + ",".join(["1"] + ["0"] * 1023) + "]",
                "profile": QwenEmbedder.profile,
                "generation": c.generation,
            }
            for i, c in enumerate(chunks)
        ],
    }
    module = ast.parse(
        (root / "backend/src/flowbridge/supplier_assistant/knowledge/postgres_store.py").read_text(
            "utf-8"
        )
    )
    for node in ast.walk(module):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SOURCE_SQL" for t in node.targets
        ):
            fixture["source_sql"] = ast.literal_eval(node.value)
    fixture["queries"] = [
        node.args[0].value
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "text"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]
    destination = root / ".cache/rag/pgvector-sql-fixture.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    print(
        f"Prepared {len(chunks)} synthetic UTF-8 chunks and actual adapter SQL; no database access."
    )


if __name__ == "__main__":
    main()
