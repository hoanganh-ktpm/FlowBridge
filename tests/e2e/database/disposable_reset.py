# SPDX-License-Identifier: GPL-3.0-or-later
"""Recreate owned objects only in fixture-created loopback databases.

Published-view migrations deliberately refuse destructive downgrades. Offline
fresh-install tests must reset their disposable objects rather than weaken that
production guard. No CASCADE or external schema is used.
"""

import re

from scripts.database.config import schema_for
from scripts.database.products import PRODUCTS
from scripts.database.registry import metadata_for
from sqlalchemy import inspect, text


def reset_owned_disposable(engine, domain):
    if (
        domain not in PRODUCTS
        or engine.url.host not in {"127.0.0.1", "localhost"}
        or not re.fullmatch(r"dxos_it_[a-z0-9_]+", engine.url.database or "")
    ):
        raise ValueError("Only fixture-created local business databases can be reset")
    schema = schema_for(domain)
    allowed = {"v_" + key for key in PRODUCTS[domain]}
    if set(inspect(engine).get_view_names(schema=schema)) != allowed:
        raise ValueError("Unexpected views in disposable owned schema; refusing reset")
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT current_database()")) == engine.url.database
        quote = conn.dialect.identifier_preparer.quote_identifier
        for view in sorted(allowed):
            conn.exec_driver_sql(f"DROP VIEW {quote(schema)}.{quote(view)}")
        for table in reversed(metadata_for(domain).sorted_tables):
            conn.exec_driver_sql(f"DROP TABLE {quote(schema)}.{quote(table.name)}")
        conn.exec_driver_sql(f"DELETE FROM {quote(schema)}.alembic_version")
