"""
Chains: several skills in a row for one goal, so a test starts from wherever
the character is instead of someone walking it into place (Marc, 16 Nov:
"string together the systems we have built").

Each step goes through the skill log like a harness step. A chain stops at the
first step that fails and says which, so a person can step in and rerun.
"""

from __future__ import annotations

import math

from ..data import load_travel_ports, load_world_points
from .skill import Result
from .skills.travel import match_point


class Chain:
    def __init__(self, registry, state, log, settings, read_position, say=print):
        self.registry, self.state, self.log, self.settings = registry, state, log, settings
        self.read_position, self.say = read_position, say
        self.steps: list[tuple[str, Result]] = []

    def step(self, skill: str, why: str, **args) -> Result:
        self.say(f"[chain] {len(self.steps) + 1}. {skill}: {why}")
        res = self.log.run_logged(self.registry.get(skill), self.state, "chain", **args)
        self.steps.append((skill, res))
        self.say(f"[chain]    -> {res.status} {res.reason}")
        return res

    def _pos(self):
        pos = self.read_position()
        if pos is not None:
            self.state.set_position(self.settings.planet, pos[0], pos[1])
        return pos

    # -- goals ----------------------------------------------------------------
    def recover_if_dead(self) -> Result | None:
        rec = self.registry.get("recover_from_death")
        if self.state.get("dead", False) or rec.is_dead():
            self.state.set("dead", True)
            res = self.step("recover_from_death", "the clone window is up")
            self.state.set("dead", not res.ok)
            return res
        return None

    def go_to(self, x: float, z: float, label: str, stop: float = 5.0, mount_over_m: float = 150.0) -> Result:
        """Get within `stop` metres of (x, z) with the outdoor navigator; mount first if it's far."""
        pos = self._pos()
        if pos is None:
            return Result("failed", "no_position")
        d = math.hypot(x - pos[0], z - pos[1])
        if d <= stop:
            return Result("ok", "already_there", {"dist": round(d, 1)})
        h = self.settings.raw.get("harness", {})
        if d > mount_over_m and h.get("mount_key") and not self.state.get("mounted", False):
            self.step("mount", f"{d:.0f} m to go")          # a failed mount isn't fatal: walk
        return self.step("navigate_to", f"{d:.0f} m to {label} ({x:.0f}, {z:.0f})", x=x, z=z, label=label, stop=stop)

    def travel(self, to: str, start: str | None = None, clean: bool = True) -> Result:
        """Wherever we are: (clone if dead,) get to a ticket terminal, buy, board, arrive."""
        planet = self.settings.planet
        points = load_world_points().get(planet, {}).get("travel", [])
        ports = load_travel_ports().get(planet, {})
        dest = match_point(points, to)
        if dest is None:
            return Result("failed", "unknown_destination", {"to": to})
        res = self.recover_if_dead()
        if res is not None and not res.ok:
            return res
        pos = self._pos()
        if pos is None:
            return Result("failed", "no_position")
        # the departure port: the one named, else the nearest we know where to stand at
        known = {n: p for n, p in ports.items() if p.get("terminal") and n != dest["name"]}
        if start:
            sp = match_point([p for p in points if p["name"] in known], start)
            if sp is None:
                return Result("failed", "unknown_start", {"start": start, "known": sorted(known)})
            name = sp["name"]
        else:
            if not known:
                return Result("failed", "no_recorded_port")
            name = min(known, key=lambda n: math.hypot(known[n]["terminal"][0] - pos[0], known[n]["terminal"][1] - pos[1]))
        tx, tz = known[name]["terminal"]
        self.say(f"[chain] travel to {dest['name']} from {name} (terminal at {tx}, {tz}; we're at {pos})")
        label = "route_" + name.lower().replace(" ", "_").replace("'", "")
        # Already at this port (just arrived, or inside the starport)? Then the
        # travel skill walks to the terminal itself, along the recorded walk if
        # there is one: the outdoor navigator is no use inside a building.
        walk = known[name].get("to_terminal") or []
        near_walk = min((math.hypot(p[0] - pos[0], p[1] - pos[1]) for p in walk), default=1e9)
        if math.hypot(tx - pos[0], tz - pos[1]) <= 40 or near_walk <= 25:
            res = Result("ok", "at_the_port")
        else:
            res = self.go_to(tx, tz, label, stop=5.0)
        if not res.ok and res.reason == "dead":
            # killed on the way: clone, walk out, and set off again (once)
            self.state.set("dead", True)
            rec = self.recover_if_dead()
            if rec is None or not rec.ok:
                return rec or res
            res = self.go_to(tx, tz, label, stop=5.0)
        if not res.ok:
            return res
        res = self.step("travel_to", f"{name} -> {dest['name']}", to=dest["name"])
        if res.ok and clean and "clean_waypoints" in self.registry.names():
            self.step("clean_waypoints", "delete the route waypoint")      # its failure doesn't fail the trip
        return res
