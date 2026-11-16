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

    def __init__(self, world_points: dict, max_failures: int = 2, skip_keys: set[str] | None = None,
                 can_mount: bool = False, clean_every: int = 0):
        self.world_points = world_points
        self.can_mount = can_mount      # get on the vehicle before a trip when on foot
        self.mount_min_dist = 150.0     # ...unless it's closer than this: walk (Marc, 7 Nov)
        self.max_failures = max_failures
        self.skip_keys = set(skip_keys or ())
        # each trip leaves a waypoint in the datapad (cap 100): delete the
        # bot's own after every `clean_every` trips; 0 = never (Marc, 7 Nov)
        self.clean_every = clean_every

    def candidates(self, state: WorldState) -> list[dict]:
        planet = state.get("planet")
        return [b for b in self.world_points.get(planet, {}).get("badges", [])
                if not state.has_badge(b["key"]) and b["key"] not in self.skip_keys
                and state.recent_failures("navigate_to", b["key"]) < self.max_failures]

    def decide(self, state: WorldState) -> Decision | None:
        if state.get("dead", False):
            return Decision("recover_from_death", {}, "the character died: clone and walk out of the facility")
        pos = state.position()
        if pos is None:
            return None
        _, x, z = pos
        if (self.clean_every and state.get("waypoint_trips", 0) >= self.clean_every
                and state.recent_failures("clean_waypoints", "clean_waypoints", within_s=900) < 2):
            return Decision("clean_waypoints", {},
                            f"{state.get('waypoint_trips')} trip(s) since the datapad was cleaned")
        cands = self.candidates(state)
        if not cands:
            return None
        b = min(cands, key=lambda b: math.hypot(b["x"] - x, b["z"] - z))
        d = math.hypot(b["x"] - x, b["z"] - z)
        if (self.can_mount and not state.get("mounted", False) and d > self.mount_min_dist
                and state.recent_failures("mount", "mount", within_s=600) < self.max_failures):
            return Decision("mount", {}, f"on foot, {d:.0f} m to {b['key']}: mount first")
        return Decision("navigate_to", {"x": b["x"], "z": b["z"], "label": b["key"], "radius": b["radius"]},
                        f"nearest unvisited badge {b['key']}, {d:.0f} m ({len(cands)} left)")

    def on_result(self, decision: Decision, result: Result, state: WorldState) -> None:
        # v0: arriving counts as earning it. Reading the "badge earned" chat
        # message comes later.
        if result.ok and decision.skill == "navigate_to":
            state.add_badge(decision.args["label"])
        if decision.skill == "navigate_to":
            state.set("waypoint_trips", state.get("waypoint_trips", 0) + 1)
        if decision.skill == "recover_from_death":
            state.set("dead", not result.ok)
        if decision.skill == "clean_waypoints":
            # whatever happened: the anomaly is logged, the trip goes on. Two
            # failures in 15 min and it stops trying until they age out.
            # (run_logged records the failure)
            state.set("waypoint_trips", 0)
