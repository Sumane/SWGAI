"""
find_nearest: "find our closest <thing>" (Marc, 16 Nov 2026).

  1. "/find <category>": the game sets a waypoint on the nearest one on the
     planet, named "<City> (<Category>)", e.g. "Theed (Terminal)". The waypoint
     panel (YOLO) gives its name and distance straight away: the city tells the
     chain where to shuttle, the distance whether to shuttle at all.
  2. Within 40 m: /find clear, then /tar <name>, looking all round; the target
     bar answers "<Name> (<n>m)". Close enough to use? Done.
  3. Seen but further off: the distance from three spots works out where it is
     (navigation/places.locate); walk there, check again.
  4. Remember the spot, so next time it's a known place.

Waypoints from earlier runs are cleared at the start of every run (chain.start),
so the panel only shows what this /find made.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from ...navigation import places
from ..skill import Result
from .datapad import norm, ocr_boxes
from .travel import TravelTo

ROOT = Path(__file__).resolve().parents[3]


class FindNearest(TravelTo):
    name = "find_nearest"

    def __init__(self, runner, settings, sleep=time.sleep, clock=time.time, learned_path=None):
        super().__init__(runner, settings, sleep=sleep, clock=clock, world_points={}, ports={})
        self.learned_path = learned_path

    @staticmethod
    def _dir():
        return ROOT / "data" / "live" / "find"

    # -- the target bar ---------------------------------------------------------------
    def _target(self, label="target"):
        """What's targeted and how far: (name, metres) or None. The bar is the top-right strip."""
        import cv2
        frame = self.runner.screen.grab()
        fh, fw = frame.shape[:2]
        x0, y1 = int(fw * 0.74), int(fh * 0.035)
        big = cv2.resize(frame[0:y1, x0:fw], None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        boxes = sorted(ocr_boxes(self._ocr(), big), key=lambda tb: tb[1][0])
        text = " ".join(t for t, _ in boxes)
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        self._step += 1
        cv2.imwrite(str(d / f"{self._step:02d}_{label}.png"), big)
        got = places.parse_target(text)
        with open(d / "reads.txt", "a") as fh_:
            fh_.write(f"{self._step:02d} {label}: {text!r} -> {got}\n")
        return got

    def _probe(self, info, label):
        """/tar <name> and read the bar. None unless the target is the kind of thing asked for."""
        self.runner.keys.chat(f"/tar {info['target']}")
        self.sleep(0.8)
        for _ in range(2):
            got = self._target(label)
            if got and info["word"] in got[0]:
                return got
            self.sleep(0.5)
        return None

    def _turn(self, deg):
        """Turn on the spot. 16 Nov: four 0.9 s taps (meant as 4 x 90 deg at the walking figure of
        100 deg/s) came to about 150 deg in all, so standing turns are ~42 deg/s."""
        rate = float(self.settings.nav.get("standing_turn_deg_s", 42.0))
        self.runner.keys.tap("RIGHT", deg / rate)
        self.sleep(0.4)

    def _probe_round(self, info, looks=6):
        """_probe, turning 60 degrees each time it fails, all the way round (/tar only takes what's in view)."""
        if not info.get("target"):
            return None
        for i in range(looks):
            got = self._probe(info, f"look{i}")
            if got:
                return got
            self._turn(360.0 / looks)
        return None

    # -- /find and the datapad ----------------------------------------------------------
    def _hud_waypoints(self):
        try:
            return [(w.name, w.distance) for w in self.runner.hud.waypoints(self.runner.screen.grab(), self.runner.should_stop)
                    if w.distance]
        except Exception as e:
            print(f"[find] waypoint list not read: {e}")
            return []

    def _find_hud(self, info, reads: int = 3):
        """/find <category>, then the waypoint panel: {"category", "name", "city", "dist"}.
        None if the game has none on this planet; no "dist" if the panel never showed it."""
        keys = self.runner.keys
        mark = self.runner.chat_mark() if hasattr(self.runner, "chat_mark") else None
        keys.chat(f"/find {info['find']}")
        self.sleep(2.0)
        lines = [ln.lower() for ln in (self.runner.chat_since(mark) if mark is not None else [])]
        if any("not registered" in ln or "no registered" in ln or "unable to complete" in ln for ln in lines):
            return None
        out = {"category": info["find"], "chat": lines[-3:]}
        m = next((re.search(r"nearest\s*['\"`]?([^'\"`]+)", ln) for ln in lines if "nearest" in ln), None)
        said = norm(m.group(1).strip(" .")) if m else None
        for i in range(reads):
            wps = self._hud_waypoints()
            out["hud"] = wps
            # the one /find made: the name chat gave, else "<city> (<something>)" - the run started
            # with no waypoints but the player's own, and those don't look like that
            pick = [(n, d) for n, d in wps if said and (norm(n) == said or norm(n)[:8] == said[:8])]
            pick = pick or [(n, d) for n, d in wps if re.search(r"\(.+\)", n or "")]
            if pick:
                name, dist = pick[0]
                out.update(name=name, dist=dist, city=re.split(r"\s*\(", name)[0].strip())
                return out
            self.sleep(0.7)
        if not said:
            return None            # no answer in chat and nothing on the panel: /find did nothing here
        out.update(name=said, city=said.split("_(")[0].replace("_", " "))
        return out                 # chat named it but the panel never showed it: no distance

    def _reading(self, info, label, readings):
        pos = self._raw_pos()
        got = self._probe(info, label)
        if pos is None or got is None:
            return pos, None
        readings.append((pos[0], pos[1], got[1]))
        print(f"[find] {got[0]} is {got[1]} m from ({pos[0]:.0f}, {pos[1]:.0f})")
        return pos, got

    # -- the skill -----------------------------------------------------------------------
    def run(self, state, what: str = "mission", use_range: float = 5.0, approach: bool = True,
            dry_run: bool = False, near: bool = False, **_) -> Result:
        """near=True: we're already beside it (the chain brought us): /tar only, no /find."""
        if self._ocr() is None:
            return Result("failed", "no_ocr")
        self._step, self.anomalies = 0, []
        c = self.settings.nav
        keys = self.runner.keys
        planet = self.settings.planet
        info = places.kind_info(what)
        keys.release_all()
        readings = []
        pos = self._raw_pos()
        if pos is None:
            return Result("failed", "no_position")
        place, far = places.nearest(planet, what, pos, self.learned_path)
        data = {"what": what, "pos": pos,
                "nearest_known": ({**place, "dist": round(far)} if place else None)}
        # 1. /find: the waypoint panel says which city and how far (Marc, 16 Nov)
        if not near and info.get("find"):
            wp = self._find_hud(info)
            if wp is not None:
                data["waypoint"] = wp
                dist = wp.get("dist")
                print(f"[find] /find {info['find']}: {wp.get('name', '?')}, {dist} m")
                if dist is None:
                    # a waypoint we can't read is no use, and /tar can't reach 4 km: stop and log
                    self._anomaly("find_waypoint_not_read", **data)
                    return Result("failed", "waypoint_not_read", {**data, "anomalies": self.anomalies})
                if dist > 40 or not info.get("target"):
                    return Result("ok", "waypoint", data)
                keys.chat("/find clear")           # it's right here: /tar does the rest
        # 2. /tar, looking all round: the last few metres
        got = self._probe_round(info)
        if got is None:
            return Result("failed", "none_in_range", data)
        readings.append((pos[0], pos[1], got[1]))
        print(f"[find] {got[0]} is {got[1]} m from ({pos[0]:.0f}, {pos[1]:.0f})")
        data.update(found=got[0], dist=got[1])
        if got[1] <= use_range:
            places.learn(planet, what, got[0], pos, learned_path=self.learned_path)
            return Result("ok", "in_reach", data)
        if dry_run or not approach:
            return Result("ok", "seen", data)
        self._on_foot(state)
        # two more spots, at a right angle, so the three aren't in a line
        turn = "RIGHT"
        for i in range(2):
            keys.tap("FORWARD", 1.4)
            self.sleep(0.4)
            p, g = self._reading(info, f"spot{i + 2}", readings)
            if g is None:
                break
            self._turn(90.0)
        est = None
        for attempt in range(6):
            if len(readings) < 3 or places.spread(readings) < 1.0:
                # still in a line (walked into a wall?): turn and step again
                self._turn(120.0)
                keys.tap("FORWARD", 1.4)
                self.sleep(0.4)
                self._reading(info, f"extra{attempt}", readings)
                continue
            est = places.locate(readings)
            print(f"[find] {got[0]} worked out at ({est[0]:.1f}, {est[1]:.1f}), fit {est[2]:.1f} m, from {len(readings)} readings")
            self._walk([(est[0], est[1])], f"{what} at ({est[0]:.0f}, {est[1]:.0f})")
            pos, g = self._reading(info, f"after_walk{attempt}", readings)
            if g is None:
                break
            got = g
            data.update(dist=g[1], pos=pos)
            if g[1] <= use_range:
                break
        data.update(readings=readings, estimate=est)
        self._dir().mkdir(parents=True, exist_ok=True)
        (self._dir() / "last.json").write_text(json.dumps(data, indent=1, default=str))
        if data["dist"] <= use_range and pos is not None:
            new = places.learn(planet, what, got[0], pos, at=est, learned_path=self.learned_path)
            state.set_position(planet, pos[0], pos[1])
            print(f"[find] at the {got[0]} ({data['dist']} m){' - remembered' if new else ''}")
            return Result("ok", "", data)
        self._look("not_reached")
        self._anomaly("find_not_reached", **data)
        return Result("failed", "not_reached", {**data, "anomalies": self.anomalies})
