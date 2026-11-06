"""
System 2 v0: rule-based. On the current planet, go to the nearest exploration
badge not yet earned, skipping ones that keep failing. Same decide/on_result
interface a learned System 2 will use later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .skill import Result
from .state import WorldState


@dataclass
class Decision:
    skill: str
    args: dict
    why: str


class BadgeCollector:
    name = "system2_badges"

    def __init__(self, world_points: dict, max_failures: int = 2, skip_keys: set[str] | None = None):
        self.world_points = world_points
        self.max_failures = max_failures
        self.skip_keys = set(skip_keys or ())

    def candidates(self, state: WorldState) -> list[dict]:
        planet = state.get("planet")
        return [b for b in self.world_points.get(planet, {}).get("badges", [])
                if not state.has_badge(b["key"]) and b["key"] not in self.skip_keys
                and state.recent_failures("navigate_to", b["key"]) < self.max_failures]

    def decide(self, state: WorldState) -> Decision | None:
        pos = state.position()
        if pos is None:
            return None
        _, x, z = pos
        cands = self.candidates(state)
        if not cands:
            return None
        b = min(cands, key=lambda b: math.hypot(b["x"] - x, b["z"] - z))
        d = math.hypot(b["x"] - x, b["z"] - z)
        return Decision("navigate_to", {"x": b["x"], "z": b["z"], "label": b["key"]},
                        f"nearest unvisited badge {b['key']}, {d:.0f} m ({len(cands)} left)")

    def on_result(self, decision: Decision, result: Result, state: WorldState) -> None:
        # v0: arriving counts as earning it. Reading the "badge earned" chat
        # message comes later.
        if result.ok and decision.skill == "navigate_to":
            state.add_badge(decision.args["label"])
