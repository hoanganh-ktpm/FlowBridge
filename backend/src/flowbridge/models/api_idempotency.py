# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Operational API state, separate from the versioned 27-table supplier baseline.
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from flowbridge.models.base import Base


class ApiIdempotency(Base):
    __table__ = sa.Table(
        "api_idempotency",
        Base.metadata,
        sa.Column(
            "scope",
            sa.String(200),
            primary_key=True,
            comment="Server-derived actor and operation namespace.",
        ),
        sa.Column("key", sa.String(160), primary_key=True, comment="Client retry key."),
        sa.Column(
            "request_hash",
            sa.String(64),
            nullable=False,
            comment="SHA-256 of the canonical operation/path/payload.",
        ),
        sa.Column(
            "response", JSONB, nullable=False, comment="Validated response committed atomically."
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Server timestamp; no expiry until a retention policy is defined.",
        ),
        comment="Durable idempotency responses for business API commands.",
    )
