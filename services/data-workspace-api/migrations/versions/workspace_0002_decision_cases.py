# SPDX-License-Identifier: GPL-3.0-or-later
"""Private, organization-scoped decision dossiers with immutable API revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "workspace_0002"
down_revision = "workspace_0001"


def uid(name="id", **kw):
    return sa.Column(name, sa.Uuid(), **kw)


def stamp(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade():
    schema = "data_workspace"
    op.create_unique_constraint(
        "uq_simulation_run_org", "simulation_runs", ["id", "org_id"], schema=schema
    )
    op.create_table(
        "decision_cases",
        uid(primary_key=True),
        uid("org_id", nullable=False),
        uid("warehouse_id", nullable=False),
        uid("order_id", nullable=False),
        uid("coordinator_id", nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("current_revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("workflow_instance_ref", sa.String(128)),
        sa.Column("initial_command_key", sa.String(128), nullable=False),
        sa.Column("initial_command_hash", sa.String(64), nullable=False),
        stamp(),
        stamp("updated_at"),
        sa.UniqueConstraint("org_id", "id", name="uq_decision_cases_org_id"),
        sa.UniqueConstraint(
            "org_id", "coordinator_id", "initial_command_key", name="uq_decision_initial_command"
        ),
        sa.CheckConstraint(
            "version > 0 AND current_revision > 0", name="ck_decision_case_versions"
        ),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_decision_case_title"),
        sa.CheckConstraint(
            "status IN ('DRAFT','VALIDATING','NEEDS_REVISION','PENDING_APPROVAL','APPROVED','EXECUTING','COMPLETED','STOPPED')",
            name="ck_decision_case_status",
        ),
        sa.CheckConstraint(
            "active = (status NOT IN ('COMPLETED','STOPPED'))", name="ck_decision_case_active"
        ),
        schema=schema,
    )
    op.create_index(
        "uq_decision_active_order",
        "decision_cases",
        ["org_id", "warehouse_id", "order_id"],
        schema=schema,
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_table(
        "decision_revisions",
        uid(primary_key=True),
        uid("org_id", nullable=False),
        uid("case_id", nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        uid("simulation_run_id", nullable=False),
        uid("created_by", nullable=False),
        sa.Column("command_key", sa.String(128), nullable=False),
        sa.Column("command_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        stamp(),
        sa.ForeignKeyConstraint(
            ["org_id", "case_id"],
            ["data_workspace.decision_cases.org_id", "data_workspace.decision_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_run_id", "org_id"],
            ["data_workspace.simulation_runs.id", "data_workspace.simulation_runs.org_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "case_id", "number", name="uq_decision_revision_number"),
        sa.UniqueConstraint("org_id", "case_id", "id", name="uq_decision_revision_scope"),
        sa.UniqueConstraint(
            "org_id", "case_id", "created_by", "command_key", name="uq_decision_revision_command"
        ),
        sa.CheckConstraint(
            "number > 0 AND jsonb_typeof(payload) = 'object'", name="ck_decision_revision_payload"
        ),
        schema=schema,
    )
    op.create_table(
        "decision_participants",
        uid("org_id", primary_key=True),
        uid("case_id", primary_key=True),
        uid("principal_id", primary_key=True),
        sa.Column("role", sa.String(24), primary_key=True),
        stamp(),
        sa.ForeignKeyConstraint(
            ["org_id", "case_id"],
            ["data_workspace.decision_cases.org_id", "data_workspace.decision_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "role IN ('coordinator','warehouse','logistics','manager')",
            name="ck_decision_participant_role",
        ),
        schema=schema,
    )
    op.create_table(
        "decision_checks",
        uid(primary_key=True),
        uid("org_id", nullable=False),
        uid("case_id", nullable=False),
        uid("revision_id", nullable=False),
        uid("actor_id", nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        stamp(),
        sa.ForeignKeyConstraint(
            ["org_id", "case_id", "revision_id"],
            [
                "data_workspace.decision_revisions.org_id",
                "data_workspace.decision_revisions.case_id",
                "data_workspace.decision_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "outcome IN ('VALID','STALE','UNAVAILABLE','DENIED')", name="ck_decision_check_outcome"
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_decision_check_payload"),
        schema=schema,
    )
    op.create_table(
        "decision_events",
        uid(primary_key=True),
        uid("org_id", nullable=False),
        uid("case_id", nullable=False),
        uid("revision_id"),
        uid("actor_id", nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        stamp(),
        sa.ForeignKeyConstraint(
            ["org_id", "case_id"],
            ["data_workspace.decision_cases.org_id", "data_workspace.decision_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "case_id", "revision_id"],
            [
                "data_workspace.decision_revisions.org_id",
                "data_workspace.decision_revisions.case_id",
                "data_workspace.decision_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "org_id", "case_id", "actor_id", "event_key", name="uq_decision_event_command"
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_decision_event_payload"),
        schema=schema,
    )
    op.create_index(
        "ix_decision_event_timeline",
        "decision_events",
        ["org_id", "case_id", "created_at", "id"],
        schema=schema,
    )


def downgrade():
    raise RuntimeError("Decision history requires a separately reviewed recovery plan")
