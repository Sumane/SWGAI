"""
find_nearest: "find our closest <thing>" (Marc, 16 Nov 2026).

  1. /tar <name>; the target bar (top right) answers "<Name> (<n>m)". /tar only
     takes what is in front of the camera, so turn a quarter and try again, all
     the way round.
  2. Close enough to use? Done.
  3. Seen but further off: take the distance from three spots, work out where
     the thing is (navigation/places.locate), walk there, check again.
  4. Not seen at all: "/find <category>" makes the game set an active waypoint
     on the nearest one on the planet ("Theed (Mission Terminal)"). Its
     position is read from the datapad (details pane, "Position: x z") and
     checked against the distance the waypoint itself shows. The chain then
     travels there and runs this skill again for the last few metres.
  5. Remember the spot, so next time it's a known place.

If neither works, the answer is the nearest place we already know (recorded,
wiki, or found earlier).
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from ...navigation import places
from ..skill import Result
from .datapad import CleanWaypoints, datapad_region, find_text, ocr_boxes
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

    def _pane_position(self, dp, frame, region):
        """'Position: x z' in the datapad's details pane (left ~22% of the window), read at 2x and 3x."""
        x0, y0, x1, y1 = region
        cx, cy = x0 + int(0.12 * (x1 - x0)), (y0 + y1) // 2
        reads = []
        for scale in (2.0, 3.0):
            boxes = dp._zoom_ocr(frame, cx, cy, half=max(cx - x0, (y1 - y0) // 2) + 20, scale=scale)
            lab = find_text(boxes, "position", "positlon", "posltion")
            if lab is None:
                continue
            hh = lab[3] - lab[1]
            line = sorted((b[0], t) for t, b in boxes
                          if b != lab and abs(b[1] - lab[1]) <= hh and lab[2] - 4 <= b[0] <= lab[2] + 12 * hh)
            got = places.parse_position([t for _, t in line])
            if got:
                reads.append(got)
        return reads

    def _find_waypoint(self, info, pos):
        """/find <category>, then read where its waypoint is. Returns a dict (maybe without x, z) or None."""
        h = self.settings.raw.get("harness", {})
        keys = self.runner.keys
        before = self._hud_waypoints()
        mark = self.runner.chat_mark() if hasattr(self.runner, "chat_mark") else None
        keys.chat(f"/find {info['find']}")
        self.sleep(2.0)
        lines = [ln.lower() for ln in (self.runner.chat_since(mark) if mark is not None else [])]
        if any("not registered" in ln or "no registered" in ln or "unable to complete" in ln for ln in lines):
            return None
        out = {"category": info["find"], "chat": lines[-3:]}
        m = next((re.search(r"nearest\s*['\"`]?([^'\"`]+)", ln) for ln in lines if "nearest" in ln), None)
        if m:
            out["name"] = m.group(1).strip(" .")
        # the distance the new waypoint shows
        after = self._hud_waypoints()
        new = [(n, d) for n, d in after if not any(abs(d - d0) <= 3 for _, d0 in before)]
        named = [(n, d) for n, d in new if info["word"] in n.lower()]
        pick = (named or new or [(n, d) for n, d in after if info["word"] in n.lower()])
        if pick:
            out["dist"] = pick[0][1]
        out["hud"] = after
        # where it is: datapad, select the waypoint, read the details pane
        dp = CleanWaypoints(self.runner, self.settings, sleep=self.sleep)
        dp._dir = self._dir
        dp._step, dp.anomalies = self._step, []
        words = {info["word"]} | {w for w in re.findall(r"[a-z]{4,}", out.get("name", ""))}
        keys.combo(*h.get("datapad_keys", ["CTRL", "d"]))
        self.sleep(1.5)
        try:
            frame, boxes = dp._look("find_datapad")
            region = datapad_region(boxes, frame.shape)
            if region is None:
                out["why"] = "datapad_not_found"
                return out
            x0, y0, x1, y1 = region
            gx0 = x0 + int(0.225 * (x1 - x0))
            k = frame.shape[0] / 1440
            # 16 Nov: the datapad opened on its Data tab (vehicles). Go to Waypoints first.
            tab = next((b for t, b in boxes if t.startswith("waypoint") and b[1] < y0 + 70 * k and b[0] >= gx0 - 20 * k), None)
            keys.click(*(((tab[0] + tab[2]) // 2, (tab[1] + tab[3]) // 2) if tab else (x0 + int(271 * k), y0 + int(38 * k))))
            self.sleep(0.8)
            frame, boxes = dp._look("find_waypoints_tab")
            grid = [(t, b) for t, b in boxes if gx0 <= b[0] and b[2] <= x1 and y0 + 70 * k <= b[1] and b[3] <= y1 - 90 * k
                    and len(t.strip()) >= 3 and t.strip(" .") != "waypoint"]
            hits = [(t, b) for t, b in grid if any(w in t for w in words)]
            # the ones that look right first; then any other, accepted only if its position fits the distance
            cands = hits + [tb for tb in grid if tb not in hits]
            out["labels"] = [t for t, _ in cands]
            for t, b in cands[:8]:
                keys.click((b[0] + b[2]) // 2, b[1] - int(1.6 * (b[3] - b[1])))
                self.sleep(0.7)
                frame, _ = dp._look("find_selected")
                reads = self._pane_position(dp, frame, region)
                out.setdefault("reads", []).extend(reads)
                for r in reads:
                    ok = places.check_position(r, pos, out.get("dist"))
                    if ok:
                        out.update(x=ok[0], z=ok[1], checked=True, label=t)
                        return out
                if (t, b) in hits and len(reads) == 2 and reads[0] == reads[1] and "dist" not in out:
                    out.update(x=reads[0][0], z=reads[0][1], checked=False, label=t)   # two reads agree, nothing to check against
                    return out
            out["why"] = "no_label_in_datapad" if not cands else "position_not_confirmed"
            return out
        finally:
            self._step = dp._step
            dp._close(h)

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
        # 1. /find: the game sets a waypoint on the nearest one on the planet (Marc, 16 Nov: use this first)
        if not near and info.get("find"):
            wp = self._find_waypoint(info, pos)
            if wp is not None:
                data["waypoint"] = wp
                dist = math.hypot(wp["x"] - pos[0], wp["z"] - pos[1]) if "x" in wp else wp.get("dist")
                print(f"[find] /find {info['find']}: {wp.get('name', '?')}, {dist if dist is None else round(dist)} m, "
                      f"position {(wp.get('x'), wp.get('z')) if 'x' in wp else 'not read (' + str(wp.get('why')) + ')'}")
                if dist is None:
                    self._anomaly("find_waypoint_not_read", **data)
                elif dist > 40 or not info.get("target"):
                    if "x" in wp:
                        keys.chat("/find clear")       # we have the position: no need for its waypoint
                        return Result("ok", "waypoint", data)
                    return Result("ok", "waypoint_no_position", data)
                else:
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
