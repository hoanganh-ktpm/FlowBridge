# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_agent_hub"
down_revision = "0005_chat_projection"
branch_labels = None
depends_on = None

# Frozen migration metadata, independent of future application mappings.
metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)
sa.Table("users", metadata, sa.Column("user_id", sa.UUID(), primary_key=True))
sa.Table("purchase_requests", metadata, sa.Column("request_id", sa.UUID(), primary_key=True))

conversations = sa.Table(
    "hub_conversations",
    metadata,
    sa.Column("id", sa.String(128), primary_key=True),
    sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.user_id"), nullable=False),
    sa.Column("request_id", sa.UUID(), sa.ForeignKey("purchase_requests.request_id")),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("selected_agents", JSONB, nullable=False),
    sa.Column("revision", sa.Integer(), nullable=False),
    sa.Column("next_sequence", sa.BigInteger(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_hub_conversations_owner_updated", "owner_id", "updated_at", "id"),
)
runs = sa.Table(
    "hub_runs",
    metadata,
    sa.Column("id", sa.String(128), primary_key=True),
    sa.Column(
        "conversation_id", sa.String(128), sa.ForeignKey("hub_conversations.id"), nullable=False
    ),
    sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.user_id"), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    sa.Column("result", JSONB, nullable=False),
    sa.Column("input", JSONB, nullable=False),
    sa.Column("identity", JSONB, nullable=False),
    sa.Column("credential", sa.LargeBinary()),
    sa.Column("resources", JSONB, nullable=False),
    sa.Column("job_context", sa.UUID()),
    sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deadline", sa.DateTime(timezone=True)),
    sa.Column("next_event", sa.BigInteger(), nullable=False),
    sa.CheckConstraint(
        "status IN ('queued','running','cancelling',"
        "'completed','needs_input','failed','cancelled')",
        name="state",
    ),
    sa.Index("ix_hub_runs_queue", "status", "accepted_at"),
    sa.Index("ix_hub_runs_conversation", "conversation_id", "accepted_at", "id"),
    sa.Index(
        "uq_hub_runs_active",
        "conversation_id",
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running','cancelling')"),
    ),
)
messages = sa.Table(
    "hub_messages",
    metadata,
    sa.Column("id", sa.String(128), primary_key=True),
    sa.Column(
        "conversation_id", sa.String(128), sa.ForeignKey("hub_conversations.id"), nullable=False
    ),
    sa.Column("run_id", sa.String(128), sa.ForeignKey("hub_runs.id"), nullable=False),
    sa.Column("sequence", sa.BigInteger(), nullable=False),
    sa.Column("body", JSONB, nullable=False),
    sa.UniqueConstraint("conversation_id", "sequence"),
)
events = sa.Table(
    "hub_events",
    metadata,
    sa.Column("run_id", sa.String(128), sa.ForeignKey("hub_runs.id"), primary_key=True),
    sa.Column("sequence", sa.BigInteger(), primary_key=True),
    sa.Column("body", JSONB, nullable=False),
)
cursors = sa.Table(
    "hub_cursors",
    metadata,
    sa.Column("id", sa.String(128), primary_key=True),
    sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.user_id"), nullable=False),
    sa.Column("scope", sa.String(300), nullable=False),
    sa.Column("ids", JSONB, nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("CREATE SCHEMA IF NOT EXISTS extensions")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions")
    for target in (conversations, runs, messages, events, cursors):
        target.create(op.get_bind(), checkfirst=False)
        op.execute(f"ALTER TABLE public.{target.name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON public.{target.name} FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    for target in (cursors, events, messages, runs, conversations):
        target.drop(op.get_bind(), checkfirst=False)
    # Keep the shared pgcrypto extension; other modules may already depend on it.
