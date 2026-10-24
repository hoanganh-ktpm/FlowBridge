"""Persist Workflow action definitions and event dispatch intent; engine owns execution state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "core_0006"
down_revision = "core_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_actions",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("action_id", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("definition_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Uuid, sa.ForeignKey("public.principals.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("updated_by", sa.Uuid, sa.ForeignKey("public.principals.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
        sa.UniqueConstraint("action_id", "version"),
        sa.CheckConstraint("version >= 1 AND revision >= 1", name="version"),
        sa.CheckConstraint("btrim(action_id) <> '' AND jsonb_typeof(definition_json) = 'object'", name="definition"),
        schema="public",
    )
    op.create_table(
        "workflow_event_inbox",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("result_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
        sa.CheckConstraint("status IN ('DISPATCHING','COMPLETED','UNCERTAIN')", name="status"),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash"),
        sa.CheckConstraint("revision >= 1 AND jsonb_typeof(result_json) = 'object'", name="result"),
        schema="public",
    )


def downgrade():
    schema = op.get_context().opts.get("dxos_schema", "public")
    quoted = op.get_context().dialect.identifier_preparer.quote_schema(schema)
    actions, inbox = quoted + ".workflow_actions", quoted + ".workflow_event_inbox"
    op.execute("LOCK TABLE " + actions + ", " + inbox + " IN ACCESS EXCLUSIVE MODE")
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM " + actions + ") OR EXISTS (SELECT 1 FROM " + inbox + ") THEN RAISE EXCEPTION 'Workflow metadata retention approval required before downgrade'; END IF; END $$")
    op.drop_table("workflow_event_inbox", schema="public")
    op.drop_table("workflow_actions", schema="public")
