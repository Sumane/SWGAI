"""
recover_from_death: clone and walk out of the cloning facility
(Marc's walkthrough, 16 Nov 2026, Theed):

  death -> window "CLONE ACTIVATED": "Closest: Theed / Pre-Designated: Theed",
  rows "Closest Cloning Facility" / "Pre-Designated Facility", [OK]
  -> loading screen -> you stand in the clone tube -> window "DECAY REPORT" [OK]
  -> walk out. Coordinates indoors are ordinary world coordinates, so the way
  out is a recorded route (jedai/data/clone_routes.json), one per facility.

The bot always picks the pre-designated facility when it has a route for it,
so it only wakes up in a building it has been taught. Anything unexpected is
logged to data/live/anomalies/ with every step's screenshot.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from pathlib import Path

from ...data import load_clone_routes
from ...navigation.route import RouteFollower
from ..skill import Result, Skill
from .datapad import find_text, ocr_boxes

ROOT = Path(__file__).resolve().parents[3]


def clone_window(boxes):
    """
    The CLONE ACTIVATED window from OCR boxes, or None:
    {"title": box, "closest": "theed", "pre": "theed", "rows": {"closest": box, "pre": box}}
    """
    rows = {}
    for t, b in boxes:
        if "facility" in t and "designated" in t:
            rows["pre"] = b
        elif "facility" in t and ("cloning" in t or "closest" in t):
            rows["closest"] = b
    # The title. 16 Nov: OCR read it "clone actiyated", and the chat line
    # "...Requesting clone activation..." matched instead, so OK was clicked
    # off the screen. Accept a wobbly "clone activated" only, and only above the rows.
    title = None
    for t, b in boxes:
        if re.search(r"c[l1i]one\s*act\w{1,3}ated", t) and "request" not in t and "activation" not in t:
            below = [r for r in rows.values() if 60 < r[1] - b[1] < 320 and abs(r[0] - b[0]) < 200]
            if below or not rows:
                title = b
                break
    if title is None and len(rows) < 2 and "pre" not in rows:
        return None

    def value(pattern):
        # "Pre-Designated: Theed" is read in many ways ("pre-des gnated; theed", "predesignated theed"...)
        for t, b in boxes:
            m = re.match(pattern, t)
            if m and "facility" not in t:
                rest = t[m.end():].strip(" :;.,")
                if rest:
                    return rest
                # the value was read as its own box, to the right on the same line
                cy, hh = (b[1] + b[3]) / 2, max(8, b[3] - b[1])
                right = [(bb[0], tt) for tt, bb in boxes if bb[0] >= b[2] - 4 and abs((bb[1] + bb[3]) / 2 - cy) < 0.7 * hh
                         and bb != b]
                if right:
                    return min(right)[1].strip(" :;.,")
        return None
    return {"title": title, "rows": rows,
            "closest": value(r"c[l1i]osest\b"), "pre": value(r"pre.{0,2}des\w*(\s?[gq]nated)?")}


def ok_button(boxes, below, frame_shape):
    """Centre of the OK button of the window whose title box is `below` (OCR, else its usual place)."""
    h, w = frame_shape[:2]
    sx, sy = w / 2560, h / 1440
    best = None
    for t, b in boxes:
        if t.strip(" .") in ("ok", "0k", "dk"):
            dy, dx = b[1] - below[3], (b[0] + b[2]) / 2 - below[0]
            if 100 * sy < dy < 420 * sy and 0 < dx < 420 * sx and (best is None or dy < best[0]):
                best = (dy, ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
    if best:
        return best[1]
    # 16 Nov screenshots: OK sits 203 px right of and 262 px below the title's top-left corner
    return int(below[0] + 203 * sx), int(below[1] + 262 * sy)


def clone_ok(boxes, win, frame_shape):
    """Where the clone window's OK button is."""
    if win["title"] is not None:
        return ok_button(boxes, win["title"], frame_shape)
    sx, sy = frame_shape[1] / 2560, frame_shape[0] / 1440
    if "pre" in win["rows"]:            # 16 Nov screenshot: OK is 189 px right of, 100 px below this row's corner
        r = win["rows"]["pre"]
        return int(r[0] + 189 * sx), int(r[1] + 100 * sy)
    r = win["rows"]["closest"]
    return int(r[0] + 189 * sx), int(r[1] + 122 * sy)


class RecoverFromDeath(Skill):
    name = "recover_from_death"

    def __init__(self, runner, settings, sleep=time.sleep, clock=time.time, routes=None):
        self.runner, self.settings, self.sleep, self.clock = runner, settings, sleep, clock
        self.routes = routes if routes is not None else load_clone_routes()
        self._step = 0
        self.anomalies = []

    # -- seeing ---------------------------------------------------------------
    def _ocr(self):
        return getattr(getattr(self.runner.hud, "waypoint_reader", None), "ocr", None)

    _run = None

    def _dir(self):
        """This run's folder: data/live/recover/<time>/ (kept, so a dry run can be reviewed later)."""
        if self._run is None:
            self._run = time.strftime("%Y%m%d_%H%M%S")
        return ROOT / "data" / "live" / "recover" / self._run

    def _shot(self, label):
        """Screenshot only (no OCR): cheap enough to take at every blocked step."""
        import cv2
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        self._step += 1
        cv2.imwrite(str(d / f"{self._step:02d}_{label}.jpg"), self.runner.screen.grab(), [cv2.IMWRITE_JPEG_QUALITY, 80])

    def _look(self, label):
        import cv2
        frame = self.runner.screen.grab()
        boxes = ocr_boxes(self._ocr(), frame)
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        self._step += 1
        name = f"{self._step:02d}_{label}"
        cv2.imwrite(str(d / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        (d / f"{name}.json").write_text(json.dumps([[t, b] for t, b in boxes], indent=0))
        return frame, boxes

    def _anomaly(self, reason, **info):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = ROOT / "data" / "live" / "anomalies"
        d = root / f"{stamp}_recover_{reason}"
        d.mkdir(parents=True, exist_ok=True)
        for f in sorted(self._dir().glob("[0-9][0-9]_*")):
            shutil.copy2(f, d / f.name)
        rec = {"time": stamp, "skill": self.name, "reason": reason, "dir": str(d), **info}
        (d / "anomaly.json").write_text(json.dumps(rec, indent=1, default=str))
        with open(root / "index.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"[recover] anomaly '{reason}' logged: {d}")
        self.anomalies.append(reason)

    def is_dead(self) -> bool:
        """Is the CLONE ACTIVATED window on screen right now? (one OCR pass)"""
        if self._ocr() is None:
            return False
        return clone_window(ocr_boxes(self._ocr(), self.runner.screen.grab())) is not None

    # -- the steps ------------------------------------------------------------
    def _route_for(self, city):
        if not city:
            return None, None
        key = re.sub(r"[^a-z0-9]+", "_", city.lower()).strip("_")
        routes = self.routes.get(self.settings.planet, {})
        if key in routes:
            return key, routes[key]
        import difflib
        for k in routes:                       # OCR wobble: "theed," / "tneed" / "teed"
            if (k[:4] == key[:4] or key.startswith(k) or k.startswith(key)
                    or difflib.SequenceMatcher(None, k, key).ratio() >= 0.7):
                return k, routes[k]
        return key, None

    def run(self, state, dry_run: bool = False, route_only: bool = False, wait_s: float = 20.0, **_) -> Result:
        h = self.settings.raw.get("harness", {})
        keys = self.runner.keys
        if self._ocr() is None:
            return Result("failed", "no_ocr")
        self._run, self._step, self.anomalies = None, 0, []
        keys.release_all()
        city, route = None, None
        died_at = state.position()

        if not route_only:
            # 1. the clone window (it appears a few seconds after the death)
            t0, win = self.clock(), None
            while True:
                frame, boxes = self._look("clone_window")
                win = clone_window(boxes)
                if win is not None or self.clock() - t0 > wait_s:
                    break
                self.sleep(1.5)
            if win is None:
                self._anomaly("no_clone_window")
                return Result("failed", "no_clone_window", {"anomalies": self.anomalies})
            # 2. choose: pre-designated if we know its way out, else closest if we know that one
            pre_key, pre_route = self._route_for(win["pre"])
            clo_key, clo_route = self._route_for(win["closest"])
            if pre_route is not None or clo_route is None:
                choice, city, route = "pre", pre_key, pre_route
            else:
                choice, city, route = "closest", clo_key, clo_route
            print(f"[recover] clone window: closest={win['closest']!r} pre-designated={win['pre']!r} "
                  f"-> {choice} ({city}), route {'known' if route else 'NOT known'}")
            if dry_run:
                return Result("ok", "dry_run", {"window": {k: win[k] for k in ("closest", "pre")},
                                                "choice": choice, "city": city, "route_known": route is not None})
            # 3. click the row, click OK, and check the window has gone (16 Nov: it
            #    hadn't, and the bot walked off as if it had cloned). Up to 3 goes.
            start = self.runner.hud.read_coords(frame)
            cloned = False
            for attempt in range(3):
                row = win["rows"].get(choice)
                sx, sy = frame.shape[1] / 2560, frame.shape[0] / 1440
                if row is not None:
                    keys.click((row[0] + row[2]) // 2, (row[1] + row[3]) // 2)
                elif win["title"] is not None:
                    # row not read: it sits 140 / 162 px under the title
                    keys.click(win["title"][0] + int(70 * sx), win["title"][1] + int((162 if choice == "pre" else 140) * sy))
                elif "pre" in win["rows"]:
                    r = win["rows"]["pre"]      # wanted "closest": one row (22 px) above "pre"
                    keys.click((r[0] + r[2]) // 2, (r[1] + r[3]) // 2 - int(22 * sy))
                self.sleep(0.6)
                ok_xy = clone_ok(boxes, win, frame.shape)
                print(f"[recover] clicking OK at {ok_xy} (try {attempt + 1})")
                keys.click(*ok_xy)
                self.sleep(3.0)
                t0 = self.clock()
                while self.clock() - t0 < 6:
                    frame, boxes = self._look("after_ok")
                    again = clone_window(boxes)
                    if again is None:
                        cloned = True
                        break
                    self.sleep(1.0)
                if cloned:
                    break
                win = again
            if not cloned:
                self._anomaly("clone_window_wont_close", city=city)
                return Result("failed", "clone_window_wont_close", {"anomalies": self.anomalies})
            # loading screen, then the tube: wait for the HUD to come back
            t0, back = self.clock(), False
            while self.clock() - t0 < float(h.get("clone_load_wait_s", 60)):
                frame, boxes = self._look("after_clone")
                now = self.runner.hud.read_coords(frame)
                if now[0] is not None and now[2] is not None and clone_window(boxes) is None:
                    back = True
                    break
                self.sleep(1.5)
            if not back:
                self._anomaly("no_hud_after_clone", city=city)
                return Result("failed", "no_hud_after_clone", {"anomalies": self.anomalies})
            print(f"[recover] cloned: was at {start}, now at {now}")
            # 4. the decay report (not shown when nothing decayed)
            for _ in range(3):
                title = find_text(boxes, "decay report", "decayed items")
                if title is None:
                    break
                keys.click(*ok_button(boxes, find_text(boxes, "decay report") or title, frame.shape))
                self.sleep(1.0)
                frame, boxes = self._look("decay_report")
            state.set("deaths", state.get("deaths", 0) + 1)
            if died_at is not None:
                spots = state.get("death_spots", [])
                spots.append({"planet": died_at[0], "x": died_at[1], "z": died_at[2], "t": time.time()})
                state.set("death_spots", spots[-50:])
        else:
            # testing the walk only: nearest known facility
            pos = self.runner.read_position()
            near = [(math.hypot(r["spawn"][0] - pos[0], r["spawn"][1] - pos[1]), k, r)
                    for k, r in self.routes.get(self.settings.planet, {}).items()] if pos else []
            if not near or min(near)[0] > 80:
                return Result("failed", "no_route_near_here")
            _, city, route = min(near)

        # 5. on foot now, wherever we were before
        self.runner.apply_vehicle(self.settings.nav.get("foot_vehicle", "running"))
        state.set("mounted", False)
        # the position jumped to the tube: let the coordinate filter accept it
        gate = getattr(self.runner, "coord_gate", None)
        if gate is not None and hasattr(gate, "reset"):
            gate.reset()
        if route is None:
            self._anomaly("no_route", city=city)
            return Result("failed", "no_route", {"city": city, "anomalies": self.anomalies})
        # 6. walk out
        c = self.settings.nav
        rec = getattr(self.runner, "rec", None)
        follower = RouteFollower(self.runner.read_position, keys,
                                 turn_rate_deg_s=float(c.get("turn_rate_deg_s", 100.0)),
                                 right_turn_sign=int(c.get("right_turn_sign", -1)),
                                 timeout_s=float(h.get("clone_exit_timeout_s", 180)),
                                 should_stop=self.runner.should_stop, sleep=self.sleep, clock=self.clock,
                                 on_event=self._route_event)
        outcome, info = follower.follow(route["waypoints"])
        keys.release_all()
        (self._dir() / "track.json").write_text(json.dumps({"city": city, "outcome": outcome, **info}, default=str))
        if info.get("pos") is not None:
            state.set_position(self.settings.planet, info["pos"][0], info["pos"][1])
        if outcome != "arrived":
            self._look("exit_" + outcome)
            self._anomaly("exit_" + outcome, city=city, **{k: info.get(k) for k in ("waypoint", "of", "pos", "to_end_m")})
            return Result("stuck" if outcome == "stuck" else "failed", "exit_" + outcome,
                          {"city": city, "anomalies": self.anomalies, **{k: info.get(k) for k in ("waypoint", "of", "pos")}})
        print(f"[recover] out of the {city} cloning facility at {info['pos']}")
        return Result("ok", "", {"city": city, "pos": info["pos"]})

    def _route_event(self, kind, **kw):
        print(f"[recover] {kind} {kw}")
        if self._step < 40:
            self._shot(kind)

    def _ok_any(self, boxes, frame):
        for t, b in boxes:
            if t.strip(" .") in ("ok", "0k"):
                return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        return frame.shape[1] // 2, frame.shape[0] // 2
