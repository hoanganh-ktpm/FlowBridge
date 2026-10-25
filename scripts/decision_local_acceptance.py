# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local Core/Hasura source acceptance; no hosted/Supabase target fallback."""

import os


def local_targets():
    from scripts.database.config import database_url
    from scripts.database.local import load_local

    load_local()
    for domain in ("sales", "warehouse", "logistics", "core"):
        target = database_url(domain)
        expected = "dxos_core_db" if domain == "core" else domain + "_db"
        if (
            target.host != "127.0.0.1"
            or target.database != expected
            or target.username != domain + "_owner"
            or not target.port
        ):
            raise ValueError("Local acceptance requires explicitly configured local owner targets")


def main():
    import pytest

    local_targets()
    os.environ["FLOWBRIDGE_LOCAL_E2E"] = "1"
    os.environ["FLOWBRIDGE_LOCAL_MACHINE_TESTS"] = "1"
    raise SystemExit(
        pytest.main(["tests/e2e/test_local_postgres.py", "-m", "e2e", "-q", "--tb=short"])
    )


if __name__ == "__main__":
    main()
