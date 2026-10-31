# T05: rule-based System 2 (badge collector)

## GOAL
The first decision-maker: on the current planet, go to the nearest exploration
badge the bot doesn't have yet. Simple rules now; every decision is logged so a
learned System 2 can replace it later with the same interface.

Needs T01 (`jedai.data.load_world_points`), T02, T03.

## FILES TO WRITE
- `jedai/harness/system2.py`
- `tests/test_harness_system2.py`

## INTERFACE
```python
@dataclass
class Decision:
    skill: str                  # registry name, e.g. "navigate_to"
    args: dict
    why: str                    # one line, human-readable: "nearest unvisited badge, 812 m"

class BadgeCollector:
    name = "system2_badges"
    def __init__(self, world_points: dict, max_failures: int = 2,
                 skip_keys: set[str] | None = None):
    def decide(self, state: WorldState) -> Decision | None:
        """None when nothing is left to do on this planet."""
    def on_result(self, decision: Decision, result: Result, state: WorldState) -> None:
        """After the skill ran. ok → state.add_badge(key)."""
```

## RULES
1. Planet = `state.get("planet")`; position = `state.position()`.
   No position yet → `Decision("navigate_to", ...)` is impossible, so return None
   with nothing logged (the supervisor reads the position first).
2. Candidates = that planet's badges where `not state.has_badge(key)`,
   `key not in skip_keys`, and `state.recent_failures("navigate_to", key) < max_failures`.
3. Pick the nearest by straight-line distance on (x, z).
4. Return `Decision("navigate_to", {"x": b["x"], "z": b["z"], "label": b["key"]}, why)`.
5. `on_result`: ok → `add_badge(key)`. Not ok → nothing (the log already recorded the failure).

(v0 counts "arrived within the badge radius" as earned. Checking the in-game
"badge earned" message comes later.)

## DONE WHEN
`python -m pytest -q tests` passes, with tests using a small hand-written
world_points dict (3 badges) and `WorldState(":memory:")`:
- picks the nearest; after `on_result(ok)` picks the next nearest; then None
- skips badges already earned and those in `skip_keys`
- skips a badge with 2 recent failures, but not one with 1
- only considers badges on the current planet
- returns None with no position
- `why` contains the distance in metres, rounded

## DO NOT
Touch any file not listed above.
