# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; redistribution/modification under GNU GPL v3 or later.
# Provided without warranty. See LICENSE.

"""Read-only PostgreSQL connectivity check; never print credentials or change schema."""

import asyncio
from pathlib import Path

from pydantic import ValidationError

from flowbridge.db import create_engine, probe_connection
from flowbridge.settings import Settings


async def check_database(settings: Settings) -> int:
    engine = create_engine(settings, use_null_pool=True)
    try:
        await probe_connection(engine)
    except Exception as error:
        # Driver exception messages can contain connection details or credentials.
        print(f"Connection failed ({type(error).__name__}). Check password, host, SSL and network.")
        return 1
    finally:
        await engine.dispose()
    print("Connection successful (SELECT 1). No tables or data were changed.")
    return 0


def main() -> int:
    try:
        settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
    except ValidationError:
        print(
            "Invalid database configuration. Check FLOWBRIDGE_* settings and disable sample mode."
        )
        return 1
    if settings.database_url is None:
        print(
            "Database not configured. Fill FLOWBRIDGE_DATABASE_PASSWORD in .env "
            "with host/user settings, or set FLOWBRIDGE_DATABASE_URL."
        )
        return 1
    return asyncio.run(check_database(settings))


if __name__ == "__main__":
    raise SystemExit(main())
