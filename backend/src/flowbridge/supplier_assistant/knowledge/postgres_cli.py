# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit operator commands for importing, indexing and testing the PostgreSQL store."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from flowbridge.db import create_engine, create_session_factory
from flowbridge.settings import Settings

from .embeddings import QwenEmbedder
from .models import RetrievalRequest
from .postgres_domain import PgScope, prepare_chunks
from .postgres_service import PgKnowledgeService
from .postgres_store import PgKnowledgeStore
from .sources import LocalSources


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--model-cache", type=Path, default=Path(".cache/models"))
    cli.add_argument("--department", type=UUID)
    cli.add_argument("--supplier", type=UUID)
    cli.add_argument("--as-of", help="Trusted operator replay cutoff, ISO 8601 with offset")
    commands = cli.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import", help="Import local fixtures; availability starts NOW")
    imp.add_argument("--catalog", type=Path, default=Path("examples/rag/catalog.json"))
    imp.add_argument("--actor", default="operator://flowbridge/fixture-import")
    index = commands.add_parser("index", help="Reconcile all published versions; safe to retry")
    index.add_argument("--allow-download", action="store_true")
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    commands.add_parser("references", help="Prepared refs for the HTTP index request")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("reference_json", type=Path)
    return cli


async def run(args):
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError(
            "Configure FLOWBRIDGE_DATABASE_* first; do not paste credentials in chat"
        )
    engine = create_engine(settings)
    service = None
    try:
        store = PgKnowledgeStore(create_session_factory(engine))
        cutoff = (
            datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
            if args.as_of
            else (datetime.now(UTC))
        )
        if cutoff.tzinfo is None or cutoff > datetime.now(UTC):
            raise ValueError("Cutoff must have timezone and must not be in the future")
        scope_value = PgScope(
            cutoff, args.department, args.supplier, operator=args.department is None
        )

        async def scope():
            return scope_value

        if args.command == "import":
            sources = LocalSources(args.catalog)
            # Validate every source before beginning imports. Each supplier publishes atomically.
            specs = sources.catalog().documents
            contents = [(s, sources.read(s)) for s in specs]
            ids = [
                str(await store.import_document(s, content, args.actor, args.department))
                for s, content in contents
            ]
            return {
                "imported_versions": ids,
                "warnings": [
                    "LOCAL_OPERATOR: database credentials authorize import; no HTTP ACL claim",
                    "KNOWN_FROM_IS_IMPORT_TIME: fixture dates do not backdate publication",
                ],
            }
        embedder = QwenEmbedder(
            args.model_cache, allow_download=getattr(args, "allow_download", False)
        )
        service = PgKnowledgeService(store, embedder)
        if args.command == "index":
            versions = [
                v
                for v in await store.versions(scope_value, latest=False)
                if v.status == "published" and v.known_from <= cutoff
            ]
            result = await service.index_versions(versions, scope)
        elif args.command == "references":
            result = {
                "documents": [
                    prepare_chunks(v, embedder.profile)[0].record.reference.model_dump(mode="json")
                    for v in await store.versions(scope_value)
                ]
            }
        elif args.command == "inspect":
            result = await service.resolve(
                json.loads(args.reference_json.read_text("utf-8")), scope
            )
        else:
            result = await service.search(
                RetrievalRequest(
                    query=args.query,
                    supplier_id=args.supplier,
                    top_k=args.top_k,
                )
                if args.supplier
                else RetrievalRequest(query=args.query, top_k=args.top_k),
                scope,
            )
        result.setdefault("warnings", []).append(
            "LOCAL_OPERATOR: scope comes from trusted CLI flags; HTTP uses verified JobContext"
        )
        return result
    finally:
        if service is not None:
            await asyncio.to_thread(service.close)
        await engine.dispose()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Driver and settings errors may contain secrets. Never print raw upstream exceptions.
        print(
            f"PostgreSQL RAG command failed ({type(exc).__name__}); check configuration, "
            "migration, source bindings and model cache.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
