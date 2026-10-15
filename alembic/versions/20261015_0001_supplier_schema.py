# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Supplier Assistant baseline from the team's standalone installer.

Schema, helper functions and security are versioned here; synthetic rows are seeded separately.
"""

from pathlib import Path

from alembic import context, op

revision = "0001_supplier_schema"
down_revision = None
branch_labels = None
depends_on = None

SQL_DIRECTORY = Path(__file__).resolve().parents[1] / "sql"
TABLES = (
    "suppliers",
    "products",
    "supplier_products",
    "purchase_requests",
    "purchase_request_items",
    "analysis_tasks",
    "risk_predictions",
    "risk_analysis_reports",
    "analysis_supplier_candidates",
    "proposals",
    "proposal_items",
    "approval_history",
    "purchase_orders",
    "purchase_order_items",
    "purchase_order_schedules",
    "goods_receipts",
    "confirmation_tokens",
    "supplier_confirmations",
    "supplier_confirmation_items",
    "email_logs",
    "outbox_events",
    "departments",
    "notifications",
    "inventory_snapshots",
    "users",
    "roles",
    "user_roles",
)
EXPECTED = {
    "tables": 27,
    "columns": 293,
    "primary_keys": 27,
    "foreign_keys": 56,
    "checks": 83,
    "ordinary_uniques": 40,
    "partial_uniques": 3,
    "rls_tables": 27,
    "client_table_grants": 0,
    "policies": 0,
    "user_triggers": 0,
}


def execute_sql(sql: str) -> None:
    if context.is_offline_mode():
        context.get_context().impl.static_output(sql.rstrip())
        return
    connection = op.get_bind()
    # Start the DBAPI transaction before accessing asyncpg directly. Its simple-query
    # API accepts the original SQL batch including dollar-quoted function bodies.
    connection.exec_driver_sql("SET LOCAL search_path = public, pg_catalog")
    connection.connection.dbapi_connection.run_async(lambda driver: driver.execute(sql))


def upgrade() -> None:
    if not context.is_offline_mode():
        connection = op.get_bind()
        connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
        roles = (
            connection.exec_driver_sql(
                "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')"
            )
            .scalars()
            .all()
        )
        if len(roles) != 2:
            raise RuntimeError("This baseline expects a Supabase database with anon/authenticated.")
        existing = set(
            connection.exec_driver_sql(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).scalars()
        )
        if collisions := existing.intersection(TABLES):
            raise RuntimeError("Application tables already exist: " + ", ".join(sorted(collisions)))
    for filename in ("schema_0001.sql", "helper_0001.sql"):
        execute_sql((SQL_DIRECTORY / filename).read_text(encoding="utf-8"))
    if not context.is_offline_mode():
        actual = dict(
            op.get_bind()
            .exec_driver_sql((SQL_DIRECTORY / "verify_0001.sql").read_text(encoding="utf-8"))
            .mappings()
            .one()
        )
        for key, expected in EXPECTED.items():
            if actual[key] != expected:
                raise RuntimeError(
                    f"Schema verification failed for {key}: {actual[key]} != {expected}"
                )


def downgrade() -> None:
    # Only these application objects are removed. External dependencies make RESTRICT fail.
    execute_sql(
        "DROP FUNCTION public.sa_has_role(uuid,text,uuid,timestamptz);\n"
        "DROP FUNCTION public.sa_po_progress(uuid,timestamptz);\n"
        "DROP FUNCTION public.sa_training_rows(timestamptz);\n"
        "DROP TABLE " + ", ".join(f'public."{name}"' for name in TABLES) + " RESTRICT;"
    )
