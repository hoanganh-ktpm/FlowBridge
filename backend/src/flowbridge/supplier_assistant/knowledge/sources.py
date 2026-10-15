# SPDX-License-Identifier: GPL-3.0-or-later
# This file is part of FlowBridge. See LICENSE.
"""Read-only fixture adapter. Replace with the real document/ACL service later."""

import hashlib
from pathlib import Path

from pydantic import ValidationError

from .models import Catalog, DocumentSpec, KnowledgeError


def sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


class LocalSources:
    def __init__(self, catalog_path: Path):
        self.path = catalog_path.resolve()
        self.root = self.path.parent

    def catalog(self) -> Catalog:
        try:
            catalog = Catalog.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            raise KnowledgeError("INVALID_CATALOG: cannot read local document bindings") from exc
        if catalog.format_version != "flowbridge-local-knowledge-v1":
            raise KnowledgeError("INVALID_CATALOG: unsupported format")
        ids = [doc.document_id for doc in catalog.documents]
        if len(ids) != len(set(ids)):
            raise KnowledgeError("INVALID_CATALOG: duplicate document IDs")
        paths = [self._path(doc) for doc in catalog.documents]
        if len(paths) != len(set(paths)):
            raise KnowledgeError("INVALID_CATALOG: each source must have one binding")
        return catalog

    def _path(self, spec: DocumentSpec) -> Path:
        path = (self.root / spec.path).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != ".md":
            raise KnowledgeError("INVALID_SOURCE_PATH: expected Markdown within catalog directory")
        return path

    def read(self, spec: DocumentSpec) -> str:
        try:
            path = self._path(spec)
            with path.open("rb") as stream:
                raw = stream.read(2_000_001)
            if len(raw) > 2_000_000:
                raise KnowledgeError("SOURCE_TOO_LARGE: limit is 2 MB per document")
            if sha256(raw) != spec.sha256:
                raise KnowledgeError("SOURCE_CHANGED: update catalog checksum and re-index")
            # Decode bytes directly: universal-newline conversion would invalidate offsets.
            return raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise KnowledgeError("SOURCE_UNAVAILABLE: local Markdown cannot be read") from exc
