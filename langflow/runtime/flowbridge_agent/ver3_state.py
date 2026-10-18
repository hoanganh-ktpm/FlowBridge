# SPDX-License-Identifier: GPL-3.0-or-later
# FlowBridge; see LICENSE.

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4


class StateStore:
    # SQLite transactions provide CAS across Langflow workers. All I/O runs via to_thread.
    def __init__(self, path: Path, ttl: int = 1800, lease_seconds: int = 600) -> None:
        self.path, self.ttl, self.lease_seconds = path, ttl, lease_seconds

    def connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute(
            "CREATE TABLE IF NOT EXISTS conversations "
            "(scope TEXT PRIMARY KEY, version INTEGER, expires REAL, "
            "lease TEXT, lease_until REAL, body TEXT)"
        )
        return db

    @staticmethod
    def scope(user: str, flow: str, session: str) -> str:
        if not all((user, flow, session)):
            raise ValueError("SESSION_SCOPE_REQUIRED")
        return hashlib.sha256(json.dumps([user, flow, session]).encode()).hexdigest()

    def acquire(self, scope: str) -> dict:
        now, lease = time.time(), uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM conversations WHERE expires < ? AND lease_until < ?", (now, now)
            )
            row = db.execute(
                "SELECT version, expires, lease_until, body FROM conversations WHERE scope=?",
                (scope,),
            ).fetchone()
            if row and row[2] > now:
                raise ValueError("SESSION_BUSY")
            version, state = (row[0], json.loads(row[3])) if row else (0, {})
            db.execute(
                "INSERT OR REPLACE INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
                (
                    scope,
                    version,
                    now + self.ttl,
                    lease,
                    now + self.lease_seconds,
                    json.dumps(state, ensure_ascii=False),
                ),
            )
        return {"scope": scope, "version": version, "lease": lease, "state": state}

    def save(self, ticket: dict, state: dict) -> None:
        now = time.time()
        with self.connection() as db:
            changed = db.execute(
                "UPDATE conversations SET version=version+1, expires=?, lease='', "
                "lease_until=0, body=? WHERE scope=? AND version=? AND lease=? AND lease_until>?",
                (
                    now + self.ttl,
                    json.dumps(state, ensure_ascii=False),
                    ticket["scope"],
                    ticket["version"],
                    ticket["lease"],
                    now,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("STATE_VERSION_CONFLICT")

    def release(self, ticket: dict) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE conversations SET lease='', lease_until=0 WHERE scope=? AND lease=?",
                (ticket["scope"], ticket["lease"]),
            )
