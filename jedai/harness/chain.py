"""
Chains: several skills in a row for one goal, so a test starts from wherever
the character is instead of someone walking it into place (Marc, 16 Nov:
"string together the systems we have built").

Each step goes through the skill log like a harness step. A chain stops at the
first step that fails and says which, so a person can step in and rerun.
"""

from __future__ import annotations

import math
import re

from ..data import load_terminals, load_travel_ports, load_world_points
from .skill import Result
from .skills.travel import match_point


def norm_city(s: str) -> str:
    """'Dee'ja Peak Shuttleport' / "dee'ja peak" -> 'deejapeakshuttleport' / 'deejapeak'."""
    return re.sub(r"[^a-z]", "", (s or "").lower())


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
        if not res.ok and skill != "recover_from_death" and self._died(res):
            # a step failed because we died: clone and walk out, then report the failure
            self.state.set("dead", True)
            rec = self.step("recover_from_death", f"died during {skill}")
            self.state.set("dead", not rec.ok)
            return Result("failed", "dead", {**(res.data or {}), "failed_step": skill, "recovered": rec.ok})
        return res

    def _died(self, res: Result) -> bool:
        """Only asked when a step has failed: did it fail because we're dead?"""
        if res.reason == "dead":
            return True
        try:
            return bool(self.registry.get("recover_from_death").is_dead())
        except Exception:
            return False

    def start(self) -> Result | None:
        """Every run starts clean (Marc, 16 Nov): no /find waypoint, none of the bot's old ones.
        Returns a failed Result if the waypoints couldn't be cleaned (the run must not go on), else None."""
        # dead from the last run (16 Nov: killed, then Ctrl+C)? One look for the clone window;
        # recovery only runs if it's really up.
        try:
            dead = bool(self.registry.get("recover_from_death").is_dead())
        except Exception:
            dead = False
        if dead:
            self.state.set("dead", True)
            rec = self.step("recover_from_death", "the clone window is up at the start")
            self.state.set("dead", not rec.ok)
        self.runner_chat("/find clear")
        # a mission left from an earlier run goes first: its waypoint isn't in the datapad's
        # Waypoints tab, it goes when the mission is aborted on the Data tab (16 Nov)
        self.abort_held()
        res = self.clean_waypoints("start of the run", all_active=True)
        return res if res is not None and not res.ok else None

    def _pos(self):
        pos = self.read_position()
        if pos is not None:
            self.state.set_position(self.settings.planet, pos[0], pos[1])
        return pos

    # -- goals ----------------------------------------------------------------
    def abort_held(self, also=()) -> None:
        """Abort the missions the bot knows it holds, plus any named in `also` (names still on the panel)."""
        if "abort_missions" not in self.registry.names():
            return
        held = [t for t in list(self.state.get("active_missions", []) or []) + list(also) if t and t != "?"]
        if not held:
            return
        res = self.step("abort_missions", f"drop the mission(s) left from an earlier run: {held}", titles=held)
        if res.ok:
            self.state.set("active_missions", [])

    def _showing(self) -> list:
        try:
            return [nm or "?" for nm, _ in self.registry.get("find_nearest")._hud_waypoints()]
        except Exception:
            return []

    def clean_waypoints(self, why: str, tries: int = 4, all_active: bool = False) -> Result | None:
        """Delete the bot's waypoints, and keep at it (Marc, 16 Nov): if it fails, turn a quarter
        round so there's a different background behind the datapad for the OCR, and try again.
        After `tries` it gives up (logged) and the run carries on; it never stops the run."""
        if "clean_waypoints" not in self.registry.names():
            return None
        res = None
        for n in range(tries):
            # all_active (the start of a run; Marc, 16 Nov: always a fresh start): every waypoint
            # showing on the HUD panel goes too, whoever made it. Not mid-run: then the panel
            # holds the mission's own waypoint.
            showing = [nm for nm in self._showing() if nm != "?"] if all_active else []
            res = self.step("clean_waypoints", why if n == 0 else f"{why} (try {n + 1}, turned for a different background)",
                            extra=showing)
            if res.ok and all_active:
                left = self._showing()
                if left:                                  # said done, but the panel isn't empty
                    self.say(f"[chain] still on the panel after cleaning: {left}")
                    # not a datapad waypoint, so most likely a mission's: abort a mission named like it
                    self.abort_held(also=left)
                    left = self._showing()
                    if left:
                        res = Result("failed", "panel_not_empty", {"left": left})
            if res.ok:
                return res
            try:
                rate = float(self.settings.nav.get("standing_turn_deg_s", 42))
                self.registry.get("clean_waypoints").runner.keys.tap("RIGHT", 90.0 / rate)
            except Exception as e:
                self.say(f"[chain] couldn't turn for another try: {e}")
        # Marc, 16 Nov: left alone the waypoint list fills up and the panel becomes unreadable, so
        # this stops the run and is handed up to be fixed by hand (the VLM operator, or Marc).
        self.say(f"[chain] waypoints still not cleaned after {tries} tries: stopping and escalating")
        where = self.escalate("waypoints_not_cleaned",
                              "Open the datapad (Ctrl+D), Waypoints tab, and delete every waypoint whose name starts "
                              "bot_, route_ or badge_ (right-click it, Destroy, confirm Yes). Close the datapad.",
                              tries=tries, last=res.reason if res else None)
        return Result("failed", "waypoints_not_cleaned", {"escalation": where})

    def escalate(self, reason: str, task: str, **info) -> str | None:
        """Something the scripts can't fix: leave a request for an operator (the local VLM, or a
        person) in data/live/escalations/<time>_<reason>/ - request.json and the screen as it is."""
        import json
        import time as _t
        from pathlib import Path
        try:
            skill = self.registry.get("clean_waypoints")
            root = Path(skill._dir()).parent / "escalations"
            d = root / f"{_t.strftime('%Y%m%d_%H%M%S')}_{reason}"
            d.mkdir(parents=True, exist_ok=True)
            try:
                import cv2
                cv2.imwrite(str(d / "screen.jpg"), skill.runner.screen.grab(), [cv2.IMWRITE_JPEG_QUALITY, 90])
            except Exception as e:
                info["screenshot_error"] = str(e)
            req = {"reason": reason, "task": task, "status": "open", "time": _t.time(),
                   "steps": [(n, r.status, r.reason) for n, r in self.steps[-8:]], **info}
            (d / "request.json").write_text(json.dumps(req, indent=1, default=str))
            with open(root / "index.jsonl", "a") as fh:
                fh.write(json.dumps({"dir": d.name, "reason": reason, "status": "open"}) + "\n")
            self.say(f"[chain] ESCALATED '{reason}': {d}")
            return str(d)
        except Exception as e:
            self.say(f"[chain] couldn't write the escalation for {reason}: {e}")
            return None

    def recover_if_dead(self) -> Result | None:
        """For `go.py recover` only: the chains don't check up front, a failed step does (step())."""
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
        if not res.ok and res.reason == "dead" and (res.data or {}).get("recovered"):
            # killed on the way and cloned (step() did that): set off again, once
            res = self.go_to(tx, tz, label, stop=5.0)
        if not res.ok:
            return res
        res = self.step("travel_to", f"{name} -> {dest['name']}", to=dest["name"])
        if res.ok and clean:
            cleaned = self.clean_waypoints("delete the route waypoint")
            if cleaned is not None and not cleaned.ok:
                return cleaned                                             # arrived, but the run stops here
        return res

    def find(self, what: str, dry_run: bool = False, walk_limit_m: float = 1500.0, _hopped: bool = False,
             _avoid_known: bool = False) -> Result:
        """Get to the closest <what>, wherever on the planet it is.
        /find -> the waypoint panel gives "<City> (<Category>)" and the distance.
          over walk_limit_m: shuttle to that city's port, /find again (once)
          otherwise (or already shuttled): follow the waypoint, then /tar for the last few metres.
        No /find for this kind (or none on the planet): /tar from here, else the nearest place we know."""
        res = self.step("find_nearest", f"closest {what}", what=what, dry_run=dry_run)
        if dry_run or (res.ok and res.reason != "waypoint"):
            return res
        # "waypoint_not_read" (16 Nov: 9 m from /find's waypoint, and single-digit distances don't
        # read): not the end if we know a place of this kind ourselves - handled below like "none_in_range"
        if not res.ok and res.reason not in ("none_in_range", "waypoint_not_read"):
            return res
        if not res.ok and res.reason == "waypoint_not_read":
            if (res.data or {}).get("nearest_known") is None:
                return res
            self.runner_chat("/find clear")
        data = res.data or {}
        wp, place = data.get("waypoint"), data.get("nearest_known")
        pos = self._pos()
        known_near = place is not None and place.get("dist", 1e9) <= walk_limit_m
        if res.ok and known_near and not _avoid_known:
            # 16 Nov: /find led to a wall with no terminal behind it. A place we've stood at and
            # used ourselves is better ground truth than /find's waypoint: go there.
            self.say(f"[chain] we know a {what} {place['dist']:.0f} m away at ({place['x']:.0f}, {place['z']:.0f}) "
                     f"({place.get('source', 'known')}): going there, not to /find's waypoint")
            self.runner_chat("/find clear")
            res = self.go_to(place["x"], place["z"], f"route_{what}", stop=4.0)
            if res.ok:
                res = self.step("find_nearest", f"closest {what}, at the place we know", what=what, near=True)
                if res.ok:
                    return res
            self.say(f"[chain] the known {what} didn't work out ({res.reason}): trying /find's waypoint")
            return self.find(what, walk_limit_m=walk_limit_m, _hopped=_hopped, _avoid_known=True)
        if res.ok:
            dist, city = wp["dist"], wp.get("city", "")
            self.say(f"[chain] /find: {wp.get('name', what)} ({city}), {dist} m away")
            if dist > walk_limit_m and not _hopped:
                # another city: by shuttle, not across open country (no combat yet)
                port = self.port_in(city, dist, pos, known=place)
                if port is None:
                    return Result("failed", "no_port_in_city", {**data, "city": city})
                self.say(f"[chain] shuttle to {port['name']}")
                self.runner_chat("/find clear")
                res = self.travel(port["name"])
                if not res.ok:
                    return res
                return self.find(what, walk_limit_m=walk_limit_m, _hopped=True)
            h = self.settings.raw.get("harness", {})
            if dist > 150 and h.get("mount_key") and not self.state.get("mounted", False):
                self.step("mount", f"{dist} m to go")          # a failed mount isn't fatal: walk
            res = self.step("follow_waypoint", f"the /find waypoint, {dist} m", expect=dist, stop=8.0)
            self.runner_chat("/find clear")
        elif place is not None:
            self.say(f"[chain] no {what} found from here; nearest known: {place['name']} "
                     f"at ({place['x']:.0f}, {place['z']:.0f}), {place['dist']} m")
            if place["dist"] > walk_limit_m:
                if pos is None or _hopped:
                    return Result("failed", "nearest_known_too_far", {**data, "hint": "take a shuttle to that city first"})
                hop = self._shuttle_towards(place["x"], place["z"], place["dist"], data)
                if hop is None:
                    return Result("failed", "nearest_known_too_far", {**data, "hint": "take a shuttle to that city first"})
                if not hop.ok:
                    return hop
                return self.find(what, walk_limit_m=walk_limit_m, _hopped=True)
            res = self.go_to(place["x"], place["z"], f"route_{what}", stop=4.0)
        else:
            return Result("failed", "none_found", data)
        if not res.ok:
            return res
        res = self.step("find_nearest", f"closest {what}, now we're beside it", what=what, near=True)
        if not res.ok and wp is not None:
            # nothing to target where /find pointed: remember the spot so it can be reviewed
            duds = self.state.get("find_duds", []) or []
            duds.append({"what": what, "waypoint": wp.get("name"), "pos": self._pos(), "reason": res.reason})
            self.state.set("find_duds", duds[-50:])
            self.say(f"[chain] /find's waypoint for {what} had nothing to target ({res.reason}): logged as a dud")
        return res

    def port_in(self, city: str, dist: float, pos, known: dict | None = None) -> dict | None:
        """The travel point in `city` (from the waypoint's name) to shuttle to. A city can have
        several: the one nearest a place of this kind we already know in that city, else the
        one whose distance from here is nearest the waypoint's (a rough guess: ~100 m apart at 4 km)."""
        import difflib
        points = load_world_points().get(self.settings.planet, {}).get("travel", [])
        key = norm_city(city)
        if not key:
            return None
        same = [p for p in points if norm_city(p["name"]).startswith(key)
                or difflib.SequenceMatcher(None, norm_city(p["name"])[:len(key)], key).ratio() >= 0.75]
        if not same:
            return None
        if known and key in norm_city(known.get("name", "")) and "x" in known:
            return min(same, key=lambda p: math.hypot(p["x"] - known["x"], p["z"] - known["z"]))
        if pos is None:
            return same[0]
        return min(same, key=lambda p: abs(math.hypot(p["x"] - pos[0], p["z"] - pos[1]) - dist))

    def _shuttle_towards(self, x: float, z: float, dist: float, data: dict) -> Result | None:
        """Shuttle to the travel point nearest (x, z). None if no port is nearer to it than we are."""
        points = load_world_points().get(self.settings.planet, {}).get("travel", [])
        port = min(points, key=lambda p: math.hypot(p["x"] - x, p["z"] - z), default=None)
        if port is None or math.hypot(port["x"] - x, port["z"] - z) >= dist:
            return None
        self.say(f"[chain] nearest port to ({x:.0f}, {z:.0f}): {port['name']}")
        self.runner_chat("/find clear")
        return self.travel(port["name"])

    # -- delivery missions (16 Nov) ----------------------------------------------------
    def deliver(self, dry_run: bool = False, walk_limit_m: float = 1500.0) -> Result:
        """Take a delivery mission and finish it: terminal -> pickup NPC -> (shuttle) -> destination NPC."""
        if not dry_run and "abort_missions" in self.registry.names():
            # one mission at a time (16 Nov: a failed run left one, the next run took a second
            # and two mission waypoints confused the panel). Its failure doesn't stop the run.
            self.abort_held()                      # normally nothing: start() has already done it
        res = self.mission(dry_run=dry_run)
        if dry_run or not res.ok:
            return res
        mission = (res.data or {}).get("mission") or {}
        title = mission.get("title", "")
        self.state.set("active_missions", (self.state.get("active_missions", []) or []) + [title])
        self.say(f"[chain] mission: {title!r}, destination {mission.get('destination_m')} m {mission.get('destination_dir')}")
        res = self._mission_leg(title, mission, "pickup", walk_limit_m)
        if not res.ok:
            return res
        if res.reason != "completed":
            res = self._mission_leg(title, mission, "destination", walk_limit_m)
        if res.ok and res.reason == "completed":
            self.say(f"[chain] mission complete: {title!r}, reward {(res.data or {}).get('reward')} cr")
            self.state.set("missions_done", int(self.state.get("missions_done", 0) or 0) + 1)
            self.state.set("active_missions", [t for t in (self.state.get("active_missions", []) or []) if t != title])
        elif res.ok:
            return Result("failed", "not_completed", {**(res.data or {}), "mission": mission})
        return res

    def _mission_leg(self, title: str, mission: dict, leg: str, walk_limit_m: float, hopped: bool = False) -> Result:
        npc = self.registry.get("mission_npc")
        d = npc.panel_dist(title)
        if not isinstance(d, int):
            return Result("failed", "mission_waypoint_not_read", {"title": title, "leg": leg, "panel": d})
        self.say(f"[chain] {leg}: '{title}' waypoint {d} m")
        if d > walk_limit_m:
            if hopped:
                return Result("failed", "mission_target_still_far", {"title": title, "leg": leg, "dist": d})
            port = self.port_for_mission(d, mission, self._pos())
            if port is None:
                return Result("failed", "no_port_for_mission", {"title": title, "leg": leg, "dist": d})
            self.say(f"[chain] {leg} is {d} m away: shuttle to {port['name']}")
            res = self.travel(port["name"])
            if not res.ok:
                return res
            return self._mission_leg(title, mission, leg, walk_limit_m, hopped=True)
        # 16 Nov (Marc): the navigator brings us to 10 m (D* round whatever is in the way); the
        # last stretch is close_in's: stop-and-go by the arrow. If close_in can't get round
        # something, the navigator has one more go from where we are, then close_in again.
        near = 10.0
        h = self.settings.raw.get("harness", {})
        target = None
        for attempt in range(2):
            if d > near + 4 or attempt:
                if d > 150 and h.get("mount_key") and not self.state.get("mounted", False):
                    self.step("mount", f"{d} m to go")
                res = self.step("follow_waypoint", f"'{title}' ({leg}), {d} m" + (" (again: close_in was blocked)" if attempt else ""),
                                expect=None if attempt else d, label=title, stop=near)
                target = res.data.get("target") or target
                if not res.ok:
                    return res
            if self.state.get("mounted", False) and "dismount" in self.registry.names():
                self.step("dismount", "the last stretch and the talk are on foot")
            if "close_in" not in self.registry.names():
                break
            res = self.step("close_in", f"the last metres to '{title}' ({leg})", title=title, stop_m=5.0)
            if res.ok:
                break
            if res.reason != "blocked" or attempt:
                return res
        res = self.step("mission_npc", f"talk to the {leg} NPC", title=title)
        if res.ok or res.reason != "npc_not_talked" or target is None:
            return res
        # 17 Nov (Marc): "something is blocking us from following or reaching the target: revert
        # to D*". Couldn't talk to him from beside the waypoint = there's something between us
        # (a wall, a terrace edge, a railing), whatever it is. If the navmesh shows he's on another
        # level, its route round to his level is the quick way; otherwise, or if that walk fails,
        # D* to his exact spot, on foot, marking whatever it bumps, then talk again.
        lvl = None
        if "level_route" in self.registry.names():
            lvl = self.step("level_route", f"the {leg} NPC may be on another level: go round by the navmesh", goal=target)
            if lvl.ok:
                again = self.step("mission_npc", f"talk to the {leg} NPC, from his level", title=title)
                if again.ok:
                    return again
        if self.state.get("mounted", False) and "dismount" in self.registry.names():
            self.step("dismount", "D* to the NPC on foot")
        nv = self.step("navigate_to", f"something between us and the {leg} NPC: D* to his spot "
                       f"({target[0]:.0f}, {target[1]:.0f})", x=target[0], z=target[1], label="route_npc", stop=3.0)
        if not nv.ok:
            return nv
        return self.step("mission_npc", f"talk to the {leg} NPC again, after D*", title=title)

    _COMPASS = {"n": (0, 1), "ne": (0.7071, 0.7071), "e": (1, 0), "se": (0.7071, -0.7071),
                "s": (0, -1), "sw": (-0.7071, -0.7071), "w": (-1, 0), "nw": (-0.7071, 0.7071)}

    def port_for_mission(self, dist: float, mission: dict, pos) -> dict | None:
        """The travel point to shuttle to for a mission target `dist` m away (the waypoint panel).
        The details window said "Destination: 6937m to the E" from where the mission was taken:
        the port nearest that spot, among those about the right distance from here."""
        points = load_world_points().get(self.settings.planet, {}).get("travel", [])
        if not points or pos is None:
            return None
        est = None
        at, dm, dd = mission.get("taken_at"), mission.get("destination_m"), mission.get("destination_dir")
        if at and dm and dd in self._COMPASS:
            ux, uz = self._COMPASS[dd]
            est = (at["x"] + ux * dm, at["z"] + uz * dm)
        tol = 300 + 0.08 * dist
        ring = [p for p in points if abs(math.hypot(p["x"] - pos[0], p["z"] - pos[1]) - dist) <= tol]
        if est is not None:
            return min(ring or points, key=lambda p: math.hypot(p["x"] - est[0], p["z"] - est[1]))
        if not ring:
            return None
        return min(ring, key=lambda p: abs(math.hypot(p["x"] - pos[0], p["z"] - pos[1]) - dist))

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
        res = self.step("take_mission", "open the terminal, pick a delivery, accept", dry_run=dry_run)
        if res.reason == "terminal_too_far":
            # the game said so itself: walk right up to it (the find skill works out where it is
            # from three distance readings) and ask again, once
            near = self.step("find_nearest", "the terminal says we're too far: walk right up to it",
                             what="mission", near=True, use_range=3.0)
            if not near.ok:
                return near
            res = self.step("take_mission", "open the terminal again", dry_run=dry_run)
        return res
