# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Local operator commands. No HTTP endpoints or database mutations."""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .embeddings import QwenEmbedder
from .indexing import build_index
from .models import KnowledgeError
from .retrieval import Retriever, local_operator_scope
from .sources import LocalSources
from .vector_store import ChromaVectors


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--catalog", type=Path, default=Path("examples/rag/catalog.json"))
    cli.add_argument("--index", type=Path, default=Path(".cache/rag"))
    cli.add_argument("--model-cache", type=Path, default=Path(".cache/models"))
    commands = cli.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Build or reuse an immutable index generation")
    index.add_argument(
        "--allow-download",
        action="store_true",
        help="Allow first-time download of the pinned PUBLIC embedding model",
    )
    for name in ("search", "inspect"):
        command = commands.add_parser(name)
        command.add_argument("--supplier", type=UUID)
        command.add_argument("--as-of", help="ISO 8601 time with UTC offset; filters known_from")
        command.add_argument(
            "--allow-document",
            type=UUID,
            action="append",
            help="Restrict local operator scope to these document IDs",
        )
        if name == "search":
            command.add_argument("query")
            command.add_argument("--top-k", type=int, default=5)
        else:
            command.add_argument("chunk_id")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        sources = LocalSources(args.catalog)
        embedder = QwenEmbedder(
            args.model_cache, allow_download=getattr(args, "allow_download", False)
        )
        vectors = ChromaVectors(args.index / "chroma")
        if args.command == "index":
            result = build_index(sources, args.index, embedder, vectors)
        else:
            scope = local_operator_scope(
                sources,
                supplier_id=args.supplier,
                as_of=args.as_of,
                allowed=(
                    frozenset(args.allow_document) if args.allow_document is not None else None
                ),
            )
            retriever = Retriever(sources, args.index, embedder, vectors)
            result = (
                retriever.search(args.query, scope, top_k=args.top_k).model_dump(mode="json")
                if args.command == "search"
                else retriever.resolve(args.chunk_id, scope)
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValidationError:
        print(
            "INVALID_ARGUMENT: check catalog bindings and timezone-aware as-of time",
            file=sys.stderr,
        )
    except KnowledgeError as exc:
        print(str(exc), file=sys.stderr)
    return 2
