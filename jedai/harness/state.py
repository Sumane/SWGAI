"""
World state: the bot's current situation, in SQLite so it survives restarts.

System 1 skills write it; System 2/3 only read it.
Real use: WorldState(settings.data_path("harness", "world_state.db")).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (key TEXT PRIMARY KEY, value TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS badges (key TEXT PRIMARY KEY, earned REAL);
CREATE TABLE IF NOT EXISTS failures (id INTEGER PRIMARY KEY, skill TEXT, target TEXT, reason TEXT, t REAL);
CREATE INDEX IF NOT EXISTS failures_st ON failures (skill, target, t);
"""


class WorldState:
    def __init__(self, db_path: str | Path, clock=time.time):
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # facts -----------------------------------------------------------------
    def set(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO facts VALUES (?, ?, ?)",
                        (key, json.dumps(value), self.clock()))
        self.db.commit()

    def get(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM facts WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def snapshot(self) -> dict:
        return {k: json.loads(v) for k, v in self.db.execute("SELECT key, value FROM facts")}

    # position --------------------------------------------------------------
    def set_position(self, planet: str, x: float, z: float) -> None:
        now = self.clock()
        rows = [("planet", json.dumps(planet), now), ("x", json.dumps(float(x)), now),
                ("z", json.dumps(float(z)), now), ("position_time", json.dumps(now), now)]
        self.db.executemany("INSERT OR REPLACE INTO facts VALUES (?, ?, ?)", rows)
        self.db.commit()

    def position(self) -> tuple[str, float, float] | None:
        planet, x, z = self.get("planet"), self.get("x"), self.get("z")
        if planet is None or x is None or z is None:
            return None
        return planet, x, z

    # badges ----------------------------------------------------------------
    def add_badge(self, key: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO badges VALUES (?, ?)", (key, self.clock()))
        self.db.commit()

    def has_badge(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM badges WHERE key = ?", (key,)).fetchone() is not None

    def badges(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT key FROM badges")}

    # failures --------------------------------------------------------------
    def record_failure(self, skill: str, target: str, reason: str) -> None:
        self.db.execute("INSERT INTO failures (skill, target, reason, t) VALUES (?, ?, ?, ?)",
                        (skill, target, reason, self.clock()))
        self.db.commit()

    def recent_failures(self, skill: str, target: str, within_s: float = 3600) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM failures WHERE skill = ? AND target = ? AND t >= ?",
            (skill, target, self.clock() - within_s)).fetchone()[0]

    def clear_badges(self) -> None:
        """Forget earned badges (e.g. ones counted before chat confirmation existed)."""
        self.db.execute("DELETE FROM badges")
        self.db.commit()

    def clear_failures(self) -> None:
        """Forget past failures (new code: give everything another go)."""
        self.db.execute("DELETE FROM failures")
        self.db.commit()

    def close(self) -> None:
        self.db.close()
