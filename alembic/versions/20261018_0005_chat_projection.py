# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Application chat projection; no changes to the supplier baseline or IAM data.
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_chat_projection"
down_revision = "0004_knowledge_manager"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "chat_sessions",
        sa.Column("session_id", sa.UUID(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("request_id", sa.UUID(), sa.ForeignKey("purchase_requests.request_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        comment="Application-owned chat sessions; never Langflow authentication sessions.",
    )
    op.create_table(
        "chat_turns",
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
        comment="Reserve before external execution; interrupted runs are never auto-retried.",
    )
    op.create_index(
        "ix_chat_turns_session_created", "chat_turns", ["session_id", "created_at", "turn_id"]
    )
    for name in ("chat_sessions", "chat_turns"):
        op.execute(f"ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{name} FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    op.drop_table("chat_turns")
    op.drop_table("chat_sessions")
