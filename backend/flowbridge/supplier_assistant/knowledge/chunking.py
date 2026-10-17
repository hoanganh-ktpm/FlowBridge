# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Offset-preserving heading chunks, with stable source/version identities."""

import re
from uuid import NAMESPACE_URL, uuid5

from .models import ChunkRecord, DocumentReference, DocumentSpec
from .sources import sha256

CHUNKER_VERSION = "markdown-h123-offsets-v1-1000-100"
TOKENIZER_VERSION = "unicode-casefold-hyphen-v1"


def tokenize(text: str) -> list[str]:
    # Keep Vietnamese accents and identifiers such as DEMO-PO-00212; no lost negation stopwords.
    return re.findall(r"[^\W_]+(?:[-_][^\W_]+)*", text.casefold(), flags=re.UNICODE)


def _sections(text: str) -> list[tuple[int, int, list[str]]]:
    result = []
    headings: list[tuple[int, str]] = []
    start = 0
    offset = 0
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None and (match := re.match(r"^ {0,3}(#{1,3})\s+(.+?)\s*$", line)):
            if offset > start and text[start:offset].strip():
                result.append((start, offset, [title for _, title in headings]))
            level = len(match.group(1))
            headings = [(depth, title) for depth, title in headings if depth < level]
            headings.append((level, match.group(2).rstrip(" #")))
            start = offset
        offset += len(line)
    if text[start:].strip():
        result.append((start, len(text), [title for _, title in headings]))
    return result


def _spans(text: str, start: int, end: int, size: int = 1000, overlap: int = 100):
    while start < end:
        stop = min(start + size, end)
        if stop < end:
            # Prefer paragraph / line boundaries while still guaranteeing progress.
            boundary = text.rfind("\n", start + size // 2, stop)
            if boundary >= 0:
                stop = boundary + 1
        if text[start:stop].strip():
            yield start, stop
        if stop == end:
            break
        start = max(start + 1, stop - overlap)


def chunk_document(dataset_id: str, spec: DocumentSpec, text: str) -> list[ChunkRecord]:
    chunks = []
    for begin, end, headings in _sections(text):
        for start, stop in _spans(text, begin, end):
            quote = text[start:stop]
            content_hash = sha256(quote)
            version = f"sha256:{spec.sha256}"
            identity = (
                f"{dataset_id}/{spec.document_id}/{version}/{CHUNKER_VERSION}/"
                f"{start}:{stop}/{content_hash}"
            )
            reference = DocumentReference(
                document_id=str(spec.document_id),
                document_version=version,
                chunk_id=str(uuid5(NAMESPACE_URL, identity)),
                source_uri=f"local-document://{spec.document_id}/{version}#chars={start}:{stop}",
                checksum=spec.sha256,
                supplier_id=spec.supplier_id,
            )
            chunks.append(
                ChunkRecord(
                    reference=reference,
                    heading_path=headings,
                    reviewer_user_id=spec.reviewer_user_id,
                    reviewer_name=spec.reviewer_name,
                    start_char=start,
                    end_char=stop,
                    start_line=text.count("\n", 0, start) + 1,
                    end_line=text.count("\n", 0, stop - 1) + 1,
                    text_sha256=content_hash,
                    tokens=tokenize(" ".join(headings) + "\n" + quote),
                )
            )
    return chunks


def embedding_text(spec: DocumentSpec, chunk: ChunkRecord, quote: str) -> str:
    return (
        f"Supplier: {spec.supplier_code} — {spec.supplier_name}\n"
        f"Reviewer: {spec.reviewer_name}\n"
        f"Section: {' > '.join(chunk.heading_path)}\n{quote}"
    )
