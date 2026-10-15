# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

# Add operational retry state without altering the supplier baseline or its data.
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_api_idempotency"
down_revision = "0001_supplier_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "api_idempotency",
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
    op.execute("ALTER TABLE public.api_idempotency ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON public.api_idempotency FROM PUBLIC, anon, authenticated")


def downgrade() -> None:
    op.drop_table("api_idempotency")
