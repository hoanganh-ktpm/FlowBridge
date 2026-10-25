# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer-2 decision records only; business and identity IDs are opaque references."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata = sa.MetaData(schema="data_workspace")


def identifier(name="id", **kw):
    return sa.Column(name, sa.Uuid(), **kw)


def stamp(name="created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


for name in ("analysis_configs", "forecast_runs", "simulation_runs"):
    sa.Table(
        name,
        metadata,
        identifier(primary_key=True),
        identifier("org_id", nullable=False),
        identifier("owner_id", nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        stamp(),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'"),
    )
    old = metadata.tables["data_workspace." + name]
    sa.Index(f"ix_{name}_owner_time", old.c.org_id, old.c.owner_id, old.c.created_at)
metadata.tables["data_workspace.simulation_runs"].append_constraint(
    sa.UniqueConstraint("id", "org_id", name="uq_simulation_run_org")
)

cases = sa.Table(
    "decision_cases",
    metadata,
    identifier(primary_key=True),
    identifier("org_id", nullable=False),
    identifier("warehouse_id", nullable=False),
    identifier("order_id", nullable=False),
    identifier("coordinator_id", nullable=False),
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
    sa.CheckConstraint("version > 0 AND current_revision > 0", name="ck_decision_case_versions"),
    sa.CheckConstraint("length(btrim(title)) > 0", name="ck_decision_case_title"),
    sa.CheckConstraint(
        "status IN ('DRAFT','VALIDATING','NEEDS_REVISION','PENDING_APPROVAL',"
        "'APPROVED','EXECUTING','COMPLETED','STOPPED')",
        name="ck_decision_case_status",
    ),
    sa.CheckConstraint(
        "active = (status NOT IN ('COMPLETED','STOPPED'))", name="ck_decision_case_active"
    ),
)
sa.Index(
    "uq_decision_active_order",
    cases.c.org_id,
    cases.c.warehouse_id,
    cases.c.order_id,
    unique=True,
    postgresql_where=cases.c.active,
)

revisions = sa.Table(
    "decision_revisions",
    metadata,
    identifier(primary_key=True),
    identifier("org_id", nullable=False),
    identifier("case_id", nullable=False),
    sa.Column("number", sa.Integer, nullable=False),
    identifier("simulation_run_id", nullable=False),
    identifier("created_by", nullable=False),
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
)

participants = sa.Table(
    "decision_participants",
    metadata,
    identifier("org_id", primary_key=True),
    identifier("case_id", primary_key=True),
    identifier("principal_id", primary_key=True),
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
)

checks = sa.Table(
    "decision_checks",
    metadata,
    identifier(primary_key=True),
    identifier("org_id", nullable=False),
    identifier("case_id", nullable=False),
    identifier("revision_id", nullable=False),
    identifier("actor_id", nullable=False),
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
)

events = sa.Table(
    "decision_events",
    metadata,
    identifier(primary_key=True),
    identifier("org_id", nullable=False),
    identifier("case_id", nullable=False),
    identifier("revision_id"),
    identifier("actor_id", nullable=False),
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
)
sa.Index(
    "ix_decision_event_timeline",
    events.c.org_id,
    events.c.case_id,
    events.c.created_at,
    events.c.id,
)

NEW_TABLES = (cases, revisions, participants, checks, events)
