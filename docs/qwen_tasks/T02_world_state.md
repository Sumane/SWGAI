# T02: world state store

## GOAL
One place where the bot's current situation lives. System 1 skills write to it;
System 2 and 3 only read it. It survives restarts (SQLite).

## FILES TO WRITE
- `jedai/harness/__init__.py` (empty)
- `jedai/harness/state.py`
- `tests/test_harness_state.py`

## INTERFACE (implement exactly)
```python
class WorldState:
    def __init__(self, db_path: str | Path):
        """Open/create the database. Use ':memory:' in tests."""

    # current facts: one value per key, JSON-serialisable values
    def set(self, key: str, value) -> None
    def get(self, key: str, default=None)
    def snapshot(self) -> dict            # all current facts as a dict

    # convenience wrappers over set/get
    def set_position(self, planet: str, x: float, z: float) -> None   # keys: planet, x, z, position_time
    def position(self) -> tuple[str, float, float] | None

    # badges the bot has earned / visited (a set per character)
    def add_badge(self, key: str) -> None
    def has_badge(self, key: str) -> bool
    def badges(self) -> set[str]

    # failures, so System 2 can avoid repeating something that keeps failing
    def record_failure(self, skill: str, target: str, reason: str) -> None
    def recent_failures(self, skill: str, target: str, within_s: float = 3600) -> int

    def close(self) -> None
```

## SCHEMA
- `facts(key TEXT PRIMARY KEY, value TEXT /* json */, updated REAL /* unix time */)`
- `badges(key TEXT PRIMARY KEY, earned REAL)`
- `failures(id INTEGER PRIMARY KEY, skill TEXT, target TEXT, reason TEXT, t REAL)`

Allow an injectable clock: `WorldState(db_path, clock=time.time)` so tests can
control time for `recent_failures`.

Default file in real use: `settings.data_path("harness", "world_state.db")`
(create the folder if missing). The class itself just takes a path.

## DONE WHEN
`python -m pytest -q tests` passes, with tests for:
- set/get round-trip of str, int, float, list, dict; `get` default for a missing key
- values persist after `close()` and reopening the same file (use `tmp_path`)
- `snapshot()` contents
- position set/get; `position()` is None before it's set
- badges add/has/list, adding the same badge twice is fine
- `recent_failures` counts only matching skill+target inside the time window (fake clock)

## DO NOT
Touch any file not listed above.
