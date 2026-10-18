# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Operational chat projection, separate from the 27-table supplier baseline.
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from flowbridge.models.base import Base


class ChatSession(Base):
    __table__ = sa.Table(
        "chat_sessions",
        Base.metadata,
        sa.Column("session_id", sa.UUID(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("request_id", sa.UUID(), sa.ForeignKey("purchase_requests.request_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        comment="Application-owned chat sessions; never Langflow authentication sessions.",
    )


class ChatTurn(Base):
    __table__ = sa.Table(
        "chat_turns",
        Base.metadata,
        sa.Column("turn_id", sa.UUID(), primary_key=True),
        sa.Column(
            "session_id", sa.UUID(), sa.ForeignKey("chat_sessions.session_id"), nullable=False
        ),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("result", JSONB),
        sa.Column("error_code", sa.String(64)),
        sa.Column("resources", JSONB, nullable=False),
        sa.Column("job_context", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_id", "idempotency_key"),
        sa.CheckConstraint("state IN ('running','succeeded','failed')", name="state"),
        sa.Index("ix_chat_turns_session_created", "session_id", "created_at", "turn_id"),
        comment="Reserve before external execution; interrupted runs are never auto-retried.",
    )
