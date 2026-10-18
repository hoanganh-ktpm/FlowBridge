# SPDX-License-Identifier: GPL-3.0-or-later
"""Inputs never contain a filesystem path, actor ID, or embedding configuration."""

import re
import unicodedata
from datetime import UTC, datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


def filename(value: str) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    if not value.lower().endswith(".md"):
        value += ".md"
    stem = value[:-3]
    if (
        not 1 <= len(stem) <= 157
        or not re.fullmatch(r"[\w][\w .()\-]*", stem, re.UNICODE)
        or ".." in value
        or stem.endswith((".", " "))
        or stem.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *[f"COM{i}" for i in range(1, 10)],
            *[f"LPT{i}" for i in range(1, 10)],
        }
    ):
        raise ValueError("Invalid Markdown filename")
    return stem + ".md"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Revision(Input):
    expected_revision: int = Field(ge=0, strict=True)


class Edit(Revision):
    filename: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=255, pattern=r"\S")
    content_md: str = Field(min_length=1, max_length=2_097_152, pattern=r"\S")

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value):
        return filename(value)

    @field_validator("content_md")
    @classmethod
    def normalize(cls, value):
        if "\x00" in value:
            raise ValueError("NUL is not Markdown text")
        # UTF-8 strict rejects unpaired JSON surrogates.
        value.encode("utf-8")
        return value.replace("\r\n", "\n").replace("\r", "\n")


class Create(Edit):
    expected_revision: int = Field(default=0, ge=0, le=0, strict=True)
    supplier_id: UUID
    reviewer_user_id: UUID
    report_asof: AwareDatetime
    department_id: UUID | None = None

    @field_validator("report_asof")
    @classmethod
    def not_future(cls, value):
        if value > datetime.now(UTC):
            raise ValueError("Report cutoff is in the future")
        return value
