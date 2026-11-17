"""SQLite state: per-contact flags and short history, idempotency cache,
signature nonces and daily counters. Safe to call from several threads.

Stored per contact: the ManyChat contact id, opt-out/pause/disclosure flags
and the last few turns (deleted after ``history_ttl_hours``). No names,
emails or phone numbers are stored.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY, opted_out INTEGER DEFAULT 0, paused INTEGER DEFAULT 0,
  disclosed INTEGER DEFAULT 0, history TEXT DEFAULT '[]', updated REAL);
CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, response TEXT, created REAL);
CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, created REAL);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, day TEXT, value INTEGER);
"""


class Store:
    def __init__(self, path: str = ":memory:", clock=time.time):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()
        self.clock = clock

    # contacts ---------------------------------------------------------------
    def contact(self, contact_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT opted_out, paused, disclosed, history, updated FROM contacts WHERE contact_id=?",
                                  (contact_id,)).fetchone()
        if not row:
            return {"opted_out": False, "paused": False, "disclosed": False, "history": [], "updated": None}
        return {"opted_out": bool(row[0]), "paused": bool(row[1]), "disclosed": bool(row[2]),
                "history": json.loads(row[3] or "[]"), "updated": row[4]}

    def _upsert(self, contact_id: str, **cols):
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO contacts (contact_id, updated) VALUES (?, ?)", (contact_id, self.clock()))
            for k, v in cols.items():
                if k not in ("opted_out", "paused", "disclosed", "history"):
                    raise ValueError(k)
                self.db.execute(f"UPDATE contacts SET {k}=?, updated=? WHERE contact_id=?", (v, self.clock(), contact_id))
            self.db.commit()

    def set_flag(self, contact_id: str, flag: str, value: bool):
        self._upsert(contact_id, **{flag: int(bool(value))})

    def add_turns(self, contact_id: str, turns: list[dict], keep: int):
        history = self.contact(contact_id)["history"] + turns
        self._upsert(contact_id, history=json.dumps(history[-keep * 2:] if keep else [], ensure_ascii=False))

    def history(self, contact_id: str, ttl_hours: int) -> list[dict]:
        c = self.contact(contact_id)
        if c["updated"] and self.clock() - c["updated"] > ttl_hours * 3600:
            self._upsert(contact_id, history="[]")
            return []
        return c["history"]

    def purge(self, ttl_hours: int, idem_ttl: int) -> int:
        now = self.clock()
        with self.lock:
            n = self.db.execute("UPDATE contacts SET history='[]' WHERE updated < ? AND history != '[]'",
                                (now - ttl_hours * 3600,)).rowcount
            self.db.execute("DELETE FROM idempotency WHERE created < ?", (now - idem_ttl,))
            self.db.execute("DELETE FROM nonces WHERE created < ?", (now - 3600,))
            self.db.commit()
        return n

    # idempotency and nonces -------------------------------------------------
    def cached(self, key: str, ttl: int) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT response, created FROM idempotency WHERE key=?", (key,)).fetchone()
        if row and self.clock() - row[1] <= ttl:
            return json.loads(row[0])
        return None

    def remember(self, key: str, response: dict):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO idempotency VALUES (?, ?, ?)",
                            (key, json.dumps(response, ensure_ascii=False), self.clock()))
            self.db.commit()

    def use_nonce(self, nonce: str) -> bool:
        """True the first time a signature is seen, False on a replay."""
        with self.lock:
            try:
                self.db.execute("INSERT INTO nonces VALUES (?, ?)", (nonce, self.clock()))
                self.db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    # counters ---------------------------------------------------------------
    def bump(self, name: str, day: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT day, value FROM counters WHERE name=?", (name,)).fetchone()
            value = (row[1] + 1) if row and row[0] == day else 1
            self.db.execute("INSERT OR REPLACE INTO counters VALUES (?, ?, ?)", (name, day, value))
            self.db.commit()
        return value

    def count(self, name: str, day: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT day, value FROM counters WHERE name=?", (name,)).fetchone()
        return row[1] if row and row[0] == day else 0
