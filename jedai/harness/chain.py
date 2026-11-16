"""
Chains: several skills in a row for one goal, so a test starts from wherever
the character is instead of someone walking it into place (Marc, 16 Nov:
"string together the systems we have built").

Each step goes through the skill log like a harness step. A chain stops at the
first step that fails and says which, so a person can step in and rerun.
"""

from __future__ import annotations

import math

from ..data import load_terminals, load_travel_ports, load_world_points
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

    def find(self, what: str, dry_run: bool = False, walk_limit_m: float = 1500.0, _hops: int = 1) -> Result:
        """Get to the closest <what>, wherever on the planet it is.
        /find sets a waypoint on the nearest one -> near: go there; far: shuttle to the port
        beside it, then /find again -> /tar for the last few metres.
        /find no help: /tar from here, then the nearest one we already know."""
        res = self.recover_if_dead()
        if res is not None and not res.ok:
            return res
        res = self.step("find_nearest", f"closest {what}", what=what, dry_run=dry_run)
        if dry_run or (res.ok and res.reason not in ("waypoint", "waypoint_no_position")):
            return res
        if not res.ok and res.reason != "none_in_range":
            return res
        data = res.data or {}
        wp, place = data.get("waypoint"), data.get("nearest_known")
        pos = self._pos()
        if res.ok and wp:
            dist = wp.get("dist")
            if "x" in wp and pos is not None:
                dist = math.hypot(wp["x"] - pos[0], wp["z"] - pos[1])
            self.say(f"[chain] /find: {wp.get('name', what)}, {dist:.0f} m away")
            if dist > walk_limit_m:
                # another city: by shuttle, not across open country (no combat yet)
                if "x" not in wp or pos is None or _hops <= 0:
                    return Result("failed", "nearest_too_far", {**data, "hint": "take a shuttle to that city first"})
                points = load_world_points().get(self.settings.planet, {}).get("travel", [])
                port = min(points, key=lambda p: math.hypot(p["x"] - wp["x"], p["z"] - wp["z"]), default=None)
                if port is None or math.hypot(port["x"] - wp["x"], port["z"] - wp["z"]) >= dist:
                    return Result("failed", "no_port_nearer", data)
                self.say(f"[chain] nearest port to it: {port['name']}")
                self.runner_chat("/find clear")
                res = self.travel(port["name"])
                if not res.ok:
                    return res
                return self.find(what, walk_limit_m=walk_limit_m, _hops=_hops - 1)
            if res.reason == "waypoint":
                res = self.go_to(wp["x"], wp["z"], f"route_{what}", stop=6.0)
            else:
                h = self.settings.raw.get("harness", {})
                if dist > 150 and h.get("mount_key") and not self.state.get("mounted", False):
                    self.step("mount", f"{dist:.0f} m to go")
                res = self.step("follow_waypoint", f"the /find waypoint, {dist:.0f} m", expect=dist, stop=8.0)
                self.runner_chat("/find clear")
        elif place is not None:
            self.say(f"[chain] no {what} found from here; nearest known: {place['name']} "
                     f"at ({place['x']:.0f}, {place['z']:.0f}), {place['dist']} m")
            if place["dist"] > walk_limit_m:
                return Result("failed", "nearest_known_too_far", {**data, "hint": "take a shuttle to that city first"})
            res = self.go_to(place["x"], place["z"], f"route_{what}", stop=4.0)
        else:
            return Result("failed", "none_found", data)
        if not res.ok:
            return res
        if "clean_waypoints" in self.registry.names():
            self.step("clean_waypoints", "delete the route waypoint")
        return self.step("find_nearest", f"closest {what}, now we're beside it", what=what, near=True)

    def runner_chat(self, text: str) -> None:
        """A chat command through whichever skill holds the keys (the chain itself has none)."""
        try:
            self.registry.get("find_nearest").runner.keys.chat(text)
        except Exception as e:
            self.say(f"[chain] couldn't send {text!r}: {e}")

    def mission(self, dry_run: bool = False) -> Result:
        """Find the closest mission terminal and take a delivery mission."""
        res = self.find("mission", dry_run=False)
        if not res.ok:
            return res
        return self.step("take_mission", "open the terminal, pick a delivery, accept", dry_run=dry_run)
