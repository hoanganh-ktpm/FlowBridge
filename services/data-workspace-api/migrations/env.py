# SPDX-License-Identifier: GPL-3.0-or-later
import os

from alembic import context
from sqlalchemy import create_engine

engine = create_engine(os.environ["FLOWBRIDGE_WORKSPACE_DATABASE_URL"], hide_parameters=True)
with engine.begin() as connection:
    # Guard before DDL; this migration history must never run in an application/Core DB.
    actual = connection.exec_driver_sql("SELECT current_database(), current_user").one()
    if actual != ("flowbridge_workspace_db", "workspace_owner"):
        raise RuntimeError("Wrong workspace migration target")
    context.configure(connection=connection, version_table="workspace_alembic_version")
    context.run_migrations()
