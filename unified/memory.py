"""Conversation memory (manual layer 05).

Access-scoped user/project context and summaries — deliberately separate
from the durable task ledger and from source evidence (manual 1.3).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

from .contracts import utcnow


class ConversationMemory:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "project_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,"
                " updated_at TEXT NOT NULL, PRIMARY KEY (project_id, key))")

    def put(self, project_id: str, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO memories(project_id,key,value,updated_at) VALUES(?,?,?,?)",
                (project_id, key, json.dumps(value), utcnow()))

    def get(self, project_id: str, key: str):
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM memories WHERE project_id=? AND key=?",
                (project_id, key)).fetchone()
            return json.loads(row[0]) if row else None

    def list(self, project_id: str) -> list:
        with self._lock:
            return [{"key": r[0], "updated_at": r[1], "value": json.loads(r[2])}
                    for r in self._db.execute(
                        "SELECT key,updated_at,value FROM memories WHERE project_id=? ORDER BY updated_at DESC",
                        (project_id,))]
