"""
NavigationRunner: wires vision, keys, grid map and the Navigator together and
runs the interactive "drive to a waypoint" loop. (Was SWGMasterNavigator.)
"""

from __future__ import annotations

import math
import os
import sys
import time

from ..control.channel import CommandChannel
from ..control.keys import KeyClient, kill_pressed
from ..perception.screen import Screen
from ..perception.text import fuzzy_match, repair_extra_digit
from .dstar_lite import DStarLite
from .grid_db import CLEAR, NavDatabase
from .navigator import Navigator
from .reading_filter import DistanceGate, MotionGate
from .recorder import RunRecorder
from .velocity import VelocityTracker


class NavigationRunner:
    def __init__(self, settings, hud=None, keys=None, screen=None, db=None):
        """hud/keys/screen/db can be injected for testing."""
        self.s = settings
        self.cfg = settings.nav
        self.planet = settings.planet

        if hud is None:
            from ..perception.hud import HudReader
            print("--- Initialising Vision Systems ---")
            hud = HudReader.from_settings(settings)
        self.hud = hud
        self.screen = screen or Screen.from_settings(settings)
        self.keys = keys or KeyClient(CommandChannel(settings.path("command_file")))

        self.vehicle_speeds = settings.load_vehicles()
        print(f"   Loaded speed config for: {list(self.vehicle_speeds)}")
        self.vel_tracker = VelocityTracker()
        self.current_vehicle = None
        self.max_speed = 16.0
        self.mounted = False
        self.apply_vehicle(self.cfg["default_vehicle"])

        db_path = settings.path("nav_db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = db or NavDatabase(str(db_path), resolution=int(settings.planner.get("resolution_m", 5)))
        self.base_planet = self.planet
        self.area = None                    # problem area whose layer we're mapping, or None
        self.planner = self._make_planner(self.planet, prior=True)
        self.planner_active = False
        self.navigator = Navigator(self, self.cfg)
        print(f"[runner] variant: {'NAVMESH PRIOR' if self.planner.prior else 'navmesh-free'}")

        self.target_name = ""
        self.last_valid_dist = 99999
        self.min_dist_seen = 99999
        self.close_read_count = 0
        self.player = (None, None, None)   # last accepted (x, y, z)
        # Drop readings that would need the bot to move faster than its vehicle
        self.coord_gate = MotionGate(self.cfg["max_coord_jump"] / 5.0, lambda: self.max_speed,
                                     clock=lambda: time.time())
        # Distance misreads are systematic (887 -> 8987 several times in a row),
        # so a jump is only believed once it has held for 8 s with nothing
        # plausible in between.
        self.dist_gate = DistanceGate(clock=lambda: time.time())
        self.rejected_dists = 0
        self.rec = RunRecorder(None, enabled=False)   # replaced per run in navigate()
        self.last_raw_coords = None
        self.last_raw_dist = None

    # =========================================================================
    def _navmesh_prior(self, p):
        """
        Two variants, one switch (planner.navmesh_prior):
          false (default) - navmesh-free: the bot only knows what it has seen.
          true            - Core3 navmesh maps (data/navmesh_priors) price
                            unexplored cells. For comparison runs only.
        Either way the navmesh is still used offline to grade the vision model.
        """
        if not p.get("navmesh_prior", False):
            return None
        from .navmesh import PriorIndex
        idx = PriorIndex(p.get("navmesh_prior_dir", "data/navmesh_priors"))
        if not idx.maps.get(self.planet):
            print(f"[runner] navmesh_prior on, but no maps for {self.planet}: running navmesh-free")
            return None
        planet = self.planet
        return lambda wx, wz: idx.fraction(planet, wx, wz)

    def should_stop(self) -> bool:
        return kill_pressed(self.s.kill_key, self.s)

    def apply_vehicle(self, key):
        cfg = self.vehicle_speeds.get(key) or self.vehicle_speeds.get("swoop", {"max": 16.0})
        self.current_vehicle = key
        self.max_speed = cfg["max"]
        self.mounted = key not in self.cfg["on_foot_vehicles"]
        self.vel_tracker.reset()
        mode = "mounted: maps clear only" if self.mounted else "on foot: maps clear + blocked"
        print(f"   🏍  Vehicle: {key} | max: {self.max_speed} m/s | {mode}")

    def dismount(self):
        """Get off the vehicle and switch to the on-foot profile (mapping on)."""
        c = self.cfg
        print("\n🚶 Dismounting...")
        self.rec.event("dismount", pos=self.player[::2])
        self.keys.stop_movement()
        self.keys.dismount(c["dismount_method"], c.get("dismount_command", "/dismount"),
                           c.get("dismount_key", ""))
        time.sleep(c["dismount_settle_secs"])
        self.store_vehicle()
        self.apply_vehicle(c["foot_vehicle"])
        self.navigator.after_dismount()

    def store_vehicle(self):
        """Put the vehicle away after getting off (Marc: it must be stored, not
        left standing). Pressing the vehicle's toolbar key again stores it."""
        h = self.s.raw.get("harness", {})
        key = h.get("store_key", h.get("mount_key", ""))
        if not key:
            return
        self.keys.press(key)
        time.sleep(float(h.get("store_settle_s", 1.5)))

    def _make_planner(self, planet: str, prior: bool = False):
        p = self.s.planner
        return DStarLite(self.db, planet,
                         resolution=self.db.res,
                         cost_suspect=p.get("cost_suspect", 15.0),
                         bound_margin_m=p.get("bound_margin_m", 150.0),
                         cost_clear=p["cost_clear"],
                         cost_unexplored=p["cost_unexplored"],
                         search_radius_m=p["search_radius_m"],
                         goal_reach_m=p["goal_reach_m"],
                         cost_near_blocked=p.get("cost_near_blocked", 10.0),
                         prior=self._navmesh_prior(p) if prior else None,
                         cost_prior_blocked=p.get("cost_prior_blocked", 30.0))

    # -- problem areas: "stuck in a box" ------------------------------------
    # Marc, 7 Nov: when the bot believes it's boxed in, flag it, and instead
    # of stopping carry on with a separate D* layer that maps that area from
    # scratch (the main map there may be wrong: a bridge over a path looks
    # like one 2D cell that is both clear and blocked). The area is reviewed
    # later (scripts/review_areas.py) and a decision made.

    def _switch_layer(self, planet_key: str, pos):
        self.planet = planet_key
        self.planner = self._make_planner(planet_key, prior=(planet_key == self.base_planet))
        self.planner_active = False
        nav = self.navigator
        nav.after_dismount()                       # fresh stuck detection
        nav._stuck_hist.clear()
        if nav._goal_xy is not None and pos is not None:
            nav._set_target(nav._goal_xy[0], nav._goal_xy[1], nav._est_dist_at_last, pos)

    def enter_area_layer(self, pos) -> bool:
        """Boxed in: switch to (or create) this area's own layer. False if already in one."""
        if self.area is not None or pos is None:
            return False
        c = self.cfg
        height = self.player[1]
        a = self.db.find_area(self.base_planet, pos[0], pos[1])
        if a is None:
            aid = self.db.add_area(self.base_planet, pos[0], pos[1], c.get("area_radius_m", 120.0), height,
                                   f"boxed in at ({pos[0]:.0f},{pos[1]:.0f}) height {height}\n")
            a = {"id": aid, "cx": pos[0], "cz": pos[1], "radius": c.get("area_radius_m", 120.0)}
            print(f"\n🚩 [AREA {aid}] Boxed in at ({pos[0]:.0f},{pos[1]:.0f}): flagged for review, "
                  f"mapping it on its own layer")
        else:
            self.db.note_area(a["id"], f"boxed in again at ({pos[0]:.0f},{pos[1]:.0f}) height {height}", visit=True)
            print(f"\n🚩 [AREA {a['id']}] Boxed in again: using its layer")
        self.area = a
        self.rec.event("area_enter", area=a["id"], pos=pos, height=height)
        layer = f"{self.base_planet}@area{a['id']}"
        # 7 Nov: the layer kept the walls from the last visit, the start was
        # sealed in, and there was no route at all. A boxed-in layer is meant
        # to map the place afresh, so forget the old cells first.
        n = self.db.clear_layer(layer)
        if n:
            print(f"   (area {a['id']}: forgot {n} cells from the last visit, mapping it afresh)")
            self.rec.event("area_reset", area=a["id"], cells=n)
        self._switch_layer(layer, pos)
        return True

    def leave_area_layer(self, pos, why: str):
        a, self.area = self.area, None
        self.db.note_area(a["id"], why)
        self.rec.event("area_leave", area=a["id"], pos=pos, why=why)
        print(f"\n🏳️  [AREA {a['id']}] {why}: back on the main map")
        self._switch_layer(self.base_planet, pos)

    def _wait_for_vehicle(self, mark, h):
        """
        Outside cities the vehicle takes ~15 s to arrive after F1 (Marc, 7 Nov);
        in a city it's there at once. Watch the chat for a countdown ("... 15
        seconds") and wait that long; with no countdown in the first few
        seconds, carry on after mount_settle_s. If chat can't be read at all,
        wait the full vehicle_call_delay_s to be safe.
        """
        import re
        settle = float(h.get("mount_settle_s", 3.0))
        full = float(h.get("vehicle_call_delay_s", 16.0))
        if self.chatlog is None and not self.chat_lines():
            print(f"   (no chat to read: waiting {full:.0f} s for the vehicle)")
            time.sleep(full)
            return
        t0 = time.time()
        while time.time() - t0 < max(settle, 3.0):
            time.sleep(0.7)
            for ln in self.chat_since(mark):
                m = re.search(r"(\d+)\s*sec", ln)
                if m and ("vehicle" in ln or "call" in ln or "arriv" in ln or "ready" in ln
                          or "generat" in ln or "wait" in ln):
                    wait = min(float(m.group(1)) + 1.5, 40.0) - (time.time() - t0)
                    print(f"   vehicle on its way: '{ln[:60]}' -> waiting {max(0, wait):.0f} s")
                    self.rec.event("vehicle_wait", line=ln, wait=wait)
                    time.sleep(max(0.0, wait))
                    return
        # no countdown seen: in a city, or the message was missed
        self.rec.event("vehicle_wait", line=None, wait=settle)

    def can_mount(self) -> bool:
        return bool(self.s.raw.get("harness", {}).get("mount_key"))

    def mount(self):
        """Call the vehicle and get on (F1, /tar spe, /mount from harness settings)."""
        h = self.s.raw.get("harness", {})
        print("\n🏍️  Mounting...")
        self.rec.event("mount", pos=self.player[::2])
        self.keys.stop_movement()
        mark = self.chat_mark()
        self.keys.press(h["mount_key"])
        self._wait_for_vehicle(mark, h)
        for cmd in h.get("mount_commands", []):
            self.keys.chat(cmd)
            time.sleep(float(h.get("mount_command_gap_s", 1.0)))
        self.apply_vehicle(self.cfg.get("default_vehicle", "swoop"))
        self.navigator.after_dismount()      # same reset: fresh stuck detection
        # FIX (7 Nov): the old stuck points stayed in the history, so one bump
        # after remounting counted as "stuck twice" and it got straight off
        # again (mount, 10 s, dismount; three times in 2 min)
        self.navigator._stuck_hist.clear()

    _chat_base = None
    _chat_t = 0.0
    chatlog = None

    # -- chat: the client's own log file when /logChat is on, OCR otherwise --
    def open_chatlog(self):
        """Find this character's chat log and make sure /logChat is on (ensure_chatlog)."""
        from ..perception.chatlog import ChatLog, find_chatlog
        pattern = self.s.raw.get("harness", {}).get("chatlog_glob")
        path = find_chatlog(pattern) if pattern else None
        if path is None:
            print("[chat] no chat log file found: reading the chat window with OCR")
        else:
            self.chatlog = ChatLog(path)
            print(f"[chat] log file {path}")
        self.ensure_chatlog(force=True)
        return self.chatlog

    # /logChat is a toggle with no way to ask its state, so the watcher types
    # it and reads the answer. 8 Nov screenshot: the answer in the chat window
    # was "Chat logging ON / Current chat log file size: 3 KB" and the file
    # had just grown: switching ON writes that header to the file at once.
    # (My 7 Nov reading, "the file only grows when logging goes OFF", was
    # backwards and left logging off for the whole 8 Nov run.) So:
    #   chat window says ON/OFF -> believe it;
    #   unreadable -> file grew = now ON, didn't grow = now OFF.
    def _chatlog_state_path(self):
        from pathlib import Path
        return Path(__file__).resolve().parents[2] / "data" / "live" / "chatlog_state.json"

    def chat_flush(self) -> bool | None:
        """Leave chat logging ON (toggle, read the answer, toggle again if it said OFF)."""
        if self.chatlog is None:
            return None
        for attempt in range(3):
            now = self._toggle_logchat()
            if now == "on":
                self._save_chat_state(True)
                return True
            if now is None:
                self._chat_anomaly("logchat_no_answer")
                return None
            # it was on and that switched it off: again
        self._chat_anomaly("logchat_stays_off")
        self._save_chat_state(False)
        return False

    def _save_chat_state(self, on):
        import json
        sp = self._chatlog_state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps({"on": on, "checked": time.time()}))

    def ensure_chatlog(self, force: bool = False) -> bool | None:
        """
        Make sure /logChat is ON: at start (unless another harness process
        checked in the last chatlog_trust_s) and every chatlog_check_min.
        """
        import json
        h = self.s.raw.get("harness", {})
        if not h.get("chatlog_watch", True) or self.chatlog is None:
            return None
        try:
            st = json.loads(self._chatlog_state_path().read_text())
        except Exception:
            st = {}
        age = time.time() - float(st.get("checked", 0))
        limit = float(h.get("chatlog_trust_s", 120)) if force else float(h.get("chatlog_check_min", 10)) * 60
        if st.get("on") and age < limit:
            return True
        ok = self.chat_flush()
        print("[chat] chat logging is ON" if ok else f"[chat] chat logging state: {ok} (see data/live/anomalies)")
        return ok

    def _toggle_logchat(self):
        """Type /logChat once. Returns the state it left: 'on', 'off' or None."""
        size0 = os.path.getsize(self.chatlog.path) if os.path.exists(self.chatlog.path) else 0
        self.keys.chat("/logChat")
        grew = False
        t0 = time.time()
        while time.time() - t0 < 2.5:
            time.sleep(0.25)
            if os.path.exists(self.chatlog.path) and os.path.getsize(self.chatlog.path) > size0:
                grew = True
                break
        by_file = "on" if grew else "off"
        by_eye = self._logchat_answer_on_screen()
        if by_eye is None:
            return by_file
        if by_eye != by_file:
            print(f"[chat] /logChat: chat window says {by_eye}, file says {by_file}; going with the window")
            self._chat_anomaly("logchat_file_vs_screen")
        return by_eye

    def _logchat_answer_on_screen(self):
        """The newest 'Chat logging ON/OFF' in the chat window (enlarged OCR), or None."""
        import re
        try:
            import cv2
            from ..perception.tiles import region_box
            region = self.s.raw.get("harness", {}).get("screen_regions", {}).get("chat")
            ocr = getattr(getattr(self.hud, "waypoint_reader", None), "ocr", None)
            if region is None or ocr is None:
                return None
            frame = self.screen.grab()
            h, w = frame.shape[:2]
            x0, y0, x1, y1 = region_box(w, h, region)
            y0 = y1 - (y1 - y0) // 3                 # the newest lines are at the bottom
            crop = cv2.resize(frame[y0:y1, x0:x1], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            res = ocr.readtext(crop, detail=1, paragraph=False)
        except Exception:
            return None
        hits = []
        for box, text, _ in res:
            m = re.search(r"logg?ing\s*(on|0n|off|0ff|of)\b", text.lower())
            if m:
                hits.append((max(p[1] for p in box), "on" if m.group(1) in ("on", "0n") else "off"))
        return max(hits)[1] if hits else None

    def _chat_anomaly(self, reason: str):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "data" / "live" / "anomalies"
        stamp = time.strftime("%Y%m%d_%H%M%S")
        d = root / f"{stamp}_chatlog_{reason}"
        d.mkdir(parents=True, exist_ok=True)
        try:
            import cv2
            cv2.imwrite(str(d / "screen.jpg"), self.screen.grab(), [cv2.IMWRITE_JPEG_QUALITY, 85])
        except Exception:
            pass
        rec = {"time": stamp, "skill": "chatlog_watch", "reason": reason, "dir": str(d),
               "chat_ocr": self.chat_lines()}
        (d / "anomaly.json").write_text(json.dumps(rec, indent=1))
        with open(root / "index.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def chat_mark(self):
        """A point in the chat to compare against later (chat_since)."""
        return ("both", self.chatlog.mark() if self.chatlog is not None else None, self.chat_lines())

    def chat_since(self, mark) -> list[str]:
        """
        Chat messages that appeared after `mark`: the log file's (exact, but
        the client may not have flushed it yet) plus the chat window's (OCR,
        but there now).
        """
        from ..perception.chat import new_lines
        _, log_mark, ocr_base = mark
        out = []
        if self.chatlog is not None and log_mark is not None:
            out = self.chatlog.since(log_mark)
        for ln in new_lines(ocr_base, self.chat_lines()):
            if ln not in out:
                out.append(ln)
        return out

    def _chat_watch(self, frame):
        """New chat lines -> 'dead' / 'down' / 'attacked' / None (logged)."""
        from ..perception.chat import classify, new_lines
        # The log file isn't live (written only at /logChat OFF), so combat
        # is spotted by OCR of the chat window every chat_watch_s.
        every = float(self.cfg.get("chat_watch_s", 6.0))
        if every <= 0 or time.time() - self._chat_t < every:
            return None
        self._chat_t = time.time()
        t0 = time.time()
        lines = self.chat_lines(frame)
        took = time.time() - t0
        if took > 0.6:
            print(f"\n[chat] chat OCR took {took:.1f} s")
        if not lines:
            return None
        base, self._chat_base = self._chat_base, lines
        if base is None:
            return None
        fresh = new_lines(base, lines)
        if not fresh:
            return None
        self.rec.event("chat", lines=fresh)
        kind = classify(fresh)
        if kind == "dead" and not self._clone_window_up(frame):
            # 16 Nov: old "slain / clone activation" lines from earlier deaths were
            # still in the chat window, re-read slightly differently, and the walk
            # was stopped as "dead" with the character alive. Dead only counts when
            # the CLONE ACTIVATED window is actually on screen.
            self.rec.event("dead_false_alarm", lines=fresh[-4:], pos=self.player[::2])
            print("\n[chat] 'slain' in chat but no clone window: old lines, carrying on")
            kind = None
        if kind:
            self.rec.event(kind, frame=frame, lines=fresh[-4:], pos=self.player[::2])
            if kind != "dead":
                print(f"\n⚔️  {kind}: {fresh[-1][:70]}")
        return kind

    def _clone_window_up(self, frame) -> bool:
        """Is the CLONE ACTIVATED window on screen? (one full-frame OCR pass; only on a suspected death)"""
        try:
            from ..harness.skills.datapad import ocr_boxes
            from ..harness.skills.recover import clone_window
            ocr = getattr(getattr(self.hud, "waypoint_reader", None), "ocr", None)
            if ocr is None:
                return True                       # can't check: believe the chat, as before
            return clone_window(ocr_boxes(ocr, frame)) is not None
        except Exception:
            return True

    def chat_lines(self, frame=None) -> list[str]:
        """OCR the chat window (harness.screen_regions.chat)."""
        from ..perception.chat import read_chat_lines
        region = self.s.raw.get("harness", {}).get("screen_regions", {}).get("chat")
        ocr = getattr(getattr(self.hud, "waypoint_reader", None), "ocr", None)
        if region is None or ocr is None:
            return []
        return read_chat_lines(self.screen.grab() if frame is None else frame, ocr, region)

    def shutdown(self):
        self.keys.release_all()
        print("✓ All keys released")

    # =========================================================================
    # Vision
    # =========================================================================
    def update_coords(self, frame):
        """Read coords; reject jumps > max_coord_jump. Returns last accepted (x, y, z)."""
        x, y, z = self.hud.read_coords(frame)
        if x is not None and z is not None and x == z and abs(x) > 50:
            # one field read twice (seen 7 Nov: (-5028, -5028)); never a real position here
            self.rec.event("coord_duplicate", raw=(x, z))
            z = None
        self.last_raw_coords = (x, z) if x is not None and z is not None else None
        if x is not None and z is not None:
            if self.coord_gate.check((x, z)) is None:
                self.rec.event("coord_rejected", raw=(x, z), last=self.player[::2])
                return self.player        # implausible jump: misread
            self.player = (x, y, z)
            self.vel_tracker.update(x, z)
            self._check_height(frame, y)
        return self.player

    _last_height = None
    _height_crops = 0
    _last_height_crop_t = 0.0

    def _check_height(self, frame, y):
        """
        Height is read but not used for navigation yet (Marc, 7 Nov: log it for
        review first). Missing reads and sudden jumps are logged with a
        full-resolution crop of the coordinate readout, for checking after the run.
        """
        c = self.cfg
        last, self._last_height = self._last_height, (y if y is not None else self._last_height)
        if y is None:
            why = "height_missing"
        elif last is not None and abs(y - last) > c.get("height_jump_m", 4.0):
            why = "height_jump"
        else:
            return
        self.rec.event(why, raw=y, last=last, pos=self.player[::2])
        now = time.time()
        if (self._height_crops < c.get("height_crops_max", 150)
                and now - self._last_height_crop_t >= c.get("height_crop_every_s", 2.0)):
            region = self.s.raw.get("harness", {}).get("screen_regions", {}).get("minimap")
            if region is not None and self.rec.crop(frame, f"{why}_{y}_was_{last}", region):
                self._height_crops += 1
                self._last_height_crop_t = now

    def read_position(self):
        """Grab a frame and return (x, z) — last accepted — or None."""
        x, _, z = self.update_coords(self.screen.grab())
        return (x, z) if x is not None else None

    def target_status(self, frame):
        """(arrow, distance, screen_pos) of the waypoint matching target_name."""
        candidates = []
        for wp in self.hud.waypoints(frame, self.should_stop):
            if self.should_stop():
                return None, None, None
            if fuzzy_match(self.target_name, wp.name, self.cfg["fuzzy_threshold"]):
                diff = abs(wp.distance - self.last_valid_dist) if wp.distance else 99999
                candidates.append((diff, wp))
        if not candidates:
            return None, None, None
        best = min(candidates, key=lambda c: c[0])[1]
        # FIX: OCR sometimes adds a digit (887 m read as 9887 m or 8989 m). Those
        # readings wrecked the target estimate and the explore sweep's choice.
        self.last_raw_dist = best.distance
        raw = best.distance
        g = self.dist_gate
        pos = self.player[::2] if self.player[0] is not None else None
        if g.last is not None and raw is not None:
            fixed = repair_extra_digit(raw, g.last, g.allowed(pos))
            if fixed != raw:
                self.rec.event("dist_repaired", raw=raw, fixed=fixed)
                raw = fixed
        dist = g.check(raw, pos)
        if dist is None and best.distance is not None:
            self.rejected_dists += 1
            self.rec.event("dist_rejected", raw=best.distance, last=self.dist_gate.last)
        return best.arrow, dist, best.center

    def read_target_distance(self):
        _, d, _ = self.target_status(self.screen.grab())
        return d

    # =========================================================================
    # Alignment / exploration
    # =========================================================================
    def hard_align(self, direction="N", timeout=10.0):
        """Turn right until the waypoint arrow reads `direction`."""
        print(f"   🔄 Realigning to {direction}...")
        self.keys.stop_movement()
        time.sleep(0.1)
        self.keys.key_down("RIGHT")
        start = time.time()
        last_arrow = None
        while True:
            if self.should_stop():
                self.keys.key_up("RIGHT")
                self.shutdown()
                sys.exit(0)
            arrow, _, _ = self.target_status(self.screen.grab())
            if arrow == direction:
                self.keys.key_up("RIGHT")
                print(f"   ✓ Realigned ({arrow})")
                self.navigator.reset_estimator()
                return True
            if time.time() - start > timeout:
                self.keys.key_up("RIGHT")
                print(f"   ⚠️  Align timeout (last arrow: {arrow}), continuing")
                return False
            if arrow != last_arrow:
                print(f"   ... {arrow}", end="\r", flush=True)
                last_arrow = arrow
            time.sleep(0.05)

    def explore_sweep(self):
        """Quick left/right probe when every recal candidate failed."""
        print("\n[EXPLORE] Sweep...")
        k, c = self.keys, self.cfg
        k.stop_movement()

        def measure(turn_key, label):
            k.key_down(turn_key)
            time.sleep(c["explore_turn_secs"])
            k.key_up(turn_key)
            k.key_down("FORWARD")
            time.sleep(c["explore_hold_secs"])
            k.key_up("FORWARD")
            frame = self.screen.grab()
            _, d, _ = self.target_status(frame)
            x, _, z = self.update_coords(frame)
            if x is not None:
                self.navigator.mark(x, z, CLEAR)
            print(f"   {label}: {d}m")
            return d or 9999

        dl = measure("LEFT", "LEFT")
        k.stop_movement()
        k.key_down("RIGHT")
        time.sleep(c["explore_turn_secs"])
        k.key_up("RIGHT")
        dr = measure("RIGHT", "RIGHT")
        k.stop_movement()

        ret = "RIGHT" if dl <= dr else "LEFT"
        k.key_down(ret)
        time.sleep(c["explore_turn_secs"])
        k.key_up(ret)

    def scan_for_waypoints(self):
        """Turn in steps and list every waypoint seen."""
        print("\n--- EXPLORATION PHASE ---")
        seen = []
        for _ in range(self.cfg["scan_steps"]):
            if self.should_stop():
                sys.exit(0)
            for wp in self.hud.waypoints(self.screen.grab(), self.should_stop):
                if not wp.distance:
                    continue
                dup = any(fuzzy_match(s["name"], wp.name, self.cfg["fuzzy_threshold"]) and
                          abs(s["distance"] - wp.distance) < 50 for s in seen)
                if not dup:
                    print(f"  Found: '{wp.name}' ({wp.distance}m)")
                    seen.append({"name": wp.name, "distance": wp.distance})
            self.keys.tap("RIGHT", duration=self.cfg["scan_turn_time"])
            time.sleep(self.cfg["scan_settle"])
        return seen

    # =========================================================================
    # Main entry
    # =========================================================================
    def choose_vehicle(self):
        print("\n--- VEHICLE SELECT ---")
        vehicles = list(self.vehicle_speeds.items())
        for i, (k, v) in enumerate(vehicles):
            print(f"  {i}: {k:<12} max: {v['max']} m/s")
        default = self.cfg["default_vehicle"]
        choice = input(f"Vehicle [default={default}]: ").strip()
        self.apply_vehicle(vehicles[int(choice)][0]
                           if choice.isdigit() and int(choice) < len(vehicles) else default)

    def choose_waypoint(self, name: str | None = None, expect_dist: float | None = None):
        """Pick a waypoint. With `name` (harness use) pick it without asking.
        With `expect_dist` (metres, from our position to the target) match on the
        listed distance: SWGEmu ignores the name given to /waypoint and the names
        OCR badly anyway ('Waupoint'), but the distance column reads reliably."""
        waypoints = self.scan_for_waypoints()
        if not waypoints:
            print("No waypoints found.")
            return None
        if expect_dist is not None:
            tol = 25.0 + 0.03 * expect_dist
            close = [wp for wp in waypoints if abs(wp["distance"] - expect_dist) <= tol]
            if close:
                return min(close, key=lambda wp: abs(wp["distance"] - expect_dist))
            print(f"No waypoint at ~{expect_dist:.0f} m (±{tol:.0f}) in "
                  f"{[(w['name'], w['distance']) for w in waypoints]}")
            return None
        if name is not None:
            for wp in waypoints:
                if wp["name"] == name:
                    return wp
            for wp in waypoints:
                if name.lower() in wp["name"].lower():
                    return wp
            print(f"Waypoint '{name}' not found in {[w['name'] for w in waypoints]}")
            return None
        print("\n--- WAYPOINTS FOUND ---")
        for i, wp in enumerate(waypoints):
            print(f"  [{i}] '{wp['name']}' ({wp['distance']}m)")
        choice = input("\nSelect ID: ").strip()
        if not choice.isdigit() or int(choice) >= len(waypoints):
            print("Invalid selection.")
            return None
        return waypoints[int(choice)]

    def navigate(self, target_name: str | None = None, expect_dist: float | None = None,
                 target_xz: tuple[float, float] | None = None, stop_dist: float | None = None) -> str:
        """
        Drive to a waypoint. Returns the outcome string:
        "arrived", "no_waypoint", "stopped", a stop reason, or "crashed: ...".
        target_name=None asks on the console (scripts/run_navigator.py);
        the harness passes a name and nothing is asked.
        target_xz: the target's real coordinates when known (badge, starport:
        wiki knowledge). D* heads straight there; no estimating, no cross
        exploration. The waypoint is still used for the arrival distance.
        stop_dist: arrive closer than the usual stop distance (a badge's radius).
        """
        c = self.cfg
        if target_name is None:
            self.choose_vehicle()
        target = self.choose_waypoint(target_name, expect_dist)
        if target is None:
            return "no_waypoint"

        self.target_name = target["name"]
        print(f"\n🎯  Target: '{self.target_name}' ({target['distance']}m)")
        print(f"   Vehicle: {self.current_vehicle} (max ~{self.max_speed} m/s)")
        print(f"   Corridor: ±{c['corridor_width']}m | Clear mark: {c['clear_hz']}Hz")

        if self.planet != self.base_planet:         # last run ended inside a problem area
            self.area = None
            self.planet = self.base_planet
            self.planner = self._make_planner(self.base_planet, prior=True)
        self.navigator.reset()
        self.navigator.known_target = tuple(target_xz) if target_xz is not None else None
        self._stop_override = stop_dist
        self._dismount_spot = None
        self._remounts = 0
        self._last_height, self._height_crops = None, 0
        self._chat_base, self._chat_t = None, 0.0
        self.planner_active = False
        # per-run fields: the harness calls navigate() many times on one runner
        self._end_reason = "stopped"
        self._approaching = False
        self._approach_best, self._approach_t, self._approach_off_until = 1e9, 0.0, 0.0
        self.min_dist_seen = self.last_valid_dist = target["distance"]
        self.dist_gate.reset()        # the list's reading may itself be a misread
        self.close_read_count = 0

        run_id = self.db.start_run(self.planet, self.player[0] or 0, self.player[2] or 0, 0, 0)
        record = c.get("record_runs", True) and not os.environ.get("JEDAI_NO_RECORD")
        self.rec = RunRecorder(self.s.path("debug_dir"), self.target_name, enabled=record,
                               shot_every_s=float(c.get("record_shot_every_s", 5.0)),
                               clock=lambda: time.time())
        self.rec.event("start", target=self.target_name, listed_distance=target["distance"],
                       vehicle=self.current_vehicle, pos=self.player[::2])
        result = "stopped"

        print(f"⏳  Resuming in {c['tab_in_pause']}s...")
        time.sleep(c["tab_in_pause"])
        print("🧭  Aligning to target...")
        self.keys.stop_movement()
        time.sleep(0.3)
        self.hard_align("N")

        origin = self.read_position() or (0.0, 0.0)
        if target_xz is None:
            self.navigator.run_cross_exploration(origin)

        self.hard_align("N")
        self.keys.key_down("FORWARD")

        try:
            arrived = self._main_loop(run_id)
            result = "arrived" if arrived else self._end_reason
        except BaseException as e:
            result = f"crashed: {type(e).__name__}: {e}"
            raise
        finally:
            print("\n\n🧹  Shutting down...")
            self.shutdown()
            self.rec.close(result, *self._cells_for_report())
        if arrived:
            print("\n\n🏁  SUCCESS!")
        return result

    _end_reason = "stopped"
    _stop_override = None
    _dismount_spot = None
    _dismount_dist = 0.0
    _remounts = 0
    _approaching = False
    _approach_best = 1e9
    _approach_t = 0.0
    _approach_off_until = 0.0
    turn_rate_deg_s = None

    def _cells_for_report(self):
        """Blocked and clear cells round the run, for the report's map."""
        try:
            xs = [p[0] for p in self._run_track] or [0]
            zs = [p[1] for p in self._run_track] or [0]
            m = 100.0
            cells = self.db.get_cells_in_region(self.planet, min(xs) - m, max(xs) + m,
                                                min(zs) - m, max(zs) + m)
            from .grid_db import BLOCKED
            res = 5
            blocked = [(c["cx"] * res, c["cz"] * res) for c in cells if c["state"] == BLOCKED]
            clear = [(c["cx"] * res, c["cz"] * res) for c in cells if c["state"] == CLEAR]
            return blocked, clear
        except Exception:
            return (), ()

    def _main_loop(self, run_id) -> bool:
        c, k, nav = self.cfg, self.keys, self.navigator
        lost_timer = None
        self._run_track = []
        worse_ticks = 0

        while True:
            if self.should_stop():
                print("\n🛑 Kill key pressed - stopping")
                self._end_reason = "kill key"
                return False

            frame = self.screen.grab()
            px, _, pz = self.update_coords(frame)
            arrow, dist, _ = self.target_status(frame)
            pos = (px, pz) if px is not None else None
            kt = self.navigator.known_target
            if kt is not None and pos is not None:
                # FIX (7 Nov): with the target's real coordinates the distance comes
                # from our position, not the OCR'd waypoint list. On the Theed falls
                # run 3,657 of 3,694 waypoint readings were rejected as misreads,
                # so it never registered arriving and wandered off.
                wp_dist, dist = dist, int(round(math.hypot(kt[0] - pos[0], kt[1] - pos[1])))
                if wp_dist is not None and abs(wp_dist - dist) > 25 + 0.05 * dist:
                    # two readings disagree: believe the one nearer the last good distance
                    last = self.last_valid_dist
                    if last is not None and abs(wp_dist - last) < abs(dist - last):
                        dist = wp_dist
                    self.rec.event("wp_dist_disagrees", wp=wp_dist, pos_dist=dist, pos=pos, used=dist)

            # Lost the waypoint
            if dist is None:
                self.rec.tick(frame, pos=pos, height=self.player[1], layer=self.planet, coords_raw=self.last_raw_coords, dist=None,
                              dist_raw=self.last_raw_dist, arrow=arrow, state=nav.state, lost=True)
                if lost_timer is None:
                    lost_timer = time.time()
                elif time.time() - lost_timer > c["ocr_stale_limit"]:
                    print(f"\n⚠️  Lost for {c['ocr_stale_limit']:.0f}s, searching...")
                    k.key_up("FORWARD")
                    self.hard_align("N")
                    nav.reset_estimator()
                    k.key_down("FORWARD")
                    lost_timer = None
                time.sleep(c["loop_interval"])
                continue
            lost_timer = None

            self.last_valid_dist = dist
            self.min_dist_seen = min(self.min_dist_seen, dist)
            if pos is not None:
                self._run_track.append(pos)

            # Combat / death watch (7 Nov: killed by nightspiders while it kept
            # steering). The chat says it plainly; read it every few seconds.
            danger = self._chat_watch(frame)
            if danger == "dead":
                k.key_up("FORWARD")
                print("\n☠️  Killed (chat: slain / clone activation): stopping")
                self.rec.event("dead", frame=frame, pos=pos)
                self._end_reason = "dead"
                return False

            # Watchdog: give up (and keep the recording) if it has got a long way
            # further from the waypoint than its closest point so far
            limit = c.get("abort_if_further_m", 300.0)
            if limit and dist > self.min_dist_seen + limit:
                worse_ticks += 1
                if worse_ticks >= 3:
                    print(f"\n🛑 Watchdog: {dist}m away, {dist - self.min_dist_seen}m further than its "
                          f"closest ({self.min_dist_seen}m). Stopping.")
                    self.rec.event("watchdog", frame=frame, pos=pos, dist=dist, closest=self.min_dist_seen)
                    self._end_reason = "watchdog: going the wrong way"
                    k.key_up("FORWARD")
                    return False
            else:
                worse_ticks = 0
            spd = self.vel_tracker.speed()
            spd_pct = spd / self.max_speed if self.max_speed > 0 else 0

            # Arrival. On foot the bot can stop where it wants, so "there" is
            # much closer than on a vehicle (Marc, 27 Oct: it called a run
            # successful at 45 m while running).
            stop = c.get("stop_dist_on_foot", 10) if not self.mounted else c["stop_dist"]
            if self._stop_override is not None:
                stop = self._stop_override
            creep = c.get("final_creep_dist_on_foot", 0) if not self.mounted else c["final_creep_dist"]
            # Final approach on foot: stop-and-go by the arrow. At ~2 s per
            # decision a runner overshoots a 10 m circle and orbits it; this
            # turns on the spot until the arrow says N, then steps forward.
            approach = c.get("approach_dist_on_foot", 30)
            now = time.time()
            if not self._approaching or dist < self._approach_best:
                self._approach_best, self._approach_t = dist, now
            if self._approaching and now - self._approach_t > 20:
                # not getting closer (something in the way): hand back to D* for a while
                self._approach_off_until = now + 30
                self._approaching = False
                k.key_down("FORWARD")
            if (not self.mounted and approach and stop < dist <= approach
                    and now >= self._approach_off_until):
                self._approaching = True
                k.key_up("FORWARD")
                if arrow and arrow != "N":
                    deg = {"NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 135, "W": 90, "NW": 45}.get(arrow, 45)
                    rate = self.turn_rate_deg_s or c.get("turn_rate_deg_s", 100.0)
                    k.tap("RIGHT" if arrow in ("NE", "E", "SE", "S") else "LEFT", 0.8 * deg / rate)
                else:
                    k.tap("FORWARD", max(0.3, min(1.5, 0.7 * (dist - stop / 2) / max(self.max_speed, 1))))
                self.rec.tick(frame, pos=pos, height=self.player[1], layer=self.planet, dist=dist, arrow=arrow, state="APPROACH")
                print("\r" + self.status_line(arrow, dist, 0, 0, px, pz) + " [approach]", end="", flush=True)
                time.sleep(c["loop_interval"])
                continue
            if self._approaching and dist > approach:
                self._approaching = False
                k.key_down("FORWARD")
            if dist <= stop:
                self.close_read_count += 1
                if dist <= creep:
                    k.key_up("FORWARD")
                    k.tap("FORWARD", duration=c["creep_pulse_on"])
                    time.sleep(c["creep_pulse_off"])
                if self.close_read_count >= c["verify_readings"]:
                    k.key_up("FORWARD")
                    self.db.complete_run(run_id)
                    self.db.print_stats(self.planet)
                    return True
            else:
                self.close_read_count = 0

            steer = nav.tick(pos, spd_pct, arrow, dist)
            est = (nav.target_wx, nav.target_wz) if nav.target_wx is not None else None
            self.rec.tick(frame, pos=pos, height=self.player[1], layer=self.planet, coords_raw=self.last_raw_coords, dist=dist,
                          dist_raw=self.last_raw_dist, arrow=arrow, speed=spd, speed_pct=spd_pct,
                          state=nav.state, steer=steer, steer_by=nav.steer_by, heading=nav.heading,
                          off_deg=nav.steer_angle_deg,
                          est=est, est_sigma=nav.estimator.sigma_m, est_locked=nav.est_locked,
                          goal=nav._goal_xy, carrot=nav._carrot, waypoint=nav.last_waypoint,
                          mounted=self.mounted, vehicle=self.current_vehicle)
            # Got off because of an obstacle: once past it, get back on
            # (Marc, 7 Nov: it walked the rest of the way to the next badge).
            # "Past it" = moved away from the spot AND closer to the target, so
            # walking along a long wall doesn't cycle mount/stuck/dismount.
            if (self._dismount_spot is not None and not self.mounted and self.can_mount()
                    and pos is not None and dist is not None
                    and self._remounts < c.get("remount_max_per_run", 3)
                    and dist > c.get("remount_min_dist", 150)
                    and dist < self._dismount_dist - c.get("remount_progress_m", 30)
                    and math.hypot(pos[0] - self._dismount_spot[0], pos[1] - self._dismount_spot[1])
                    > c.get("remount_after_m", 40)):
                self._remounts += 1
                k.key_up("FORWARD")
                self.mount()
                self._dismount_spot = None
                k.key_down("FORWARD")
                continue
            if steer == "TRAPPED":
                k.key_up("FORWARD")
                if self.enter_area_layer(pos):
                    k.key_down("FORWARD")
                    continue
                self.db.note_area(self.area["id"], f"trapped on its own layer too at {pos}")
                self._end_reason = "trapped"
                return False
            if self.area is not None and pos is not None and math.hypot(
                    pos[0] - self.area["cx"], pos[1] - self.area["cz"]) > self.area["radius"]:
                self.leave_area_layer(pos, f"got out (now {math.hypot(pos[0] - self.area['cx'], pos[1] - self.area['cz']):.0f} m from the centre)")
            if steer == "STOP":
                k.key_up("FORWARD")
            elif steer == "DISMOUNT":
                k.key_up("FORWARD")
                self.dismount()
                self._dismount_spot = pos
                self._dismount_dist = dist
                k.key_down("FORWARD")
            elif steer == "RECAL":
                result = nav.do_recal(pos)
                if result is True:
                    k.key_down("FORWARD")
                elif result is False:
                    k.key_up("FORWARD")
                    self.explore_sweep()
                    k.key_down("FORWARD")
            elif steer == "PIVOT":
                nav.pivot(nav.pivot_side)
                k.key_down("FORWARD")
            elif steer == -1:
                k.tap("LEFT", nav.turn_tap_secs())
            elif steer == 1:
                k.tap("RIGHT", nav.turn_tap_secs())

            print("\r" + self.status_line(arrow, dist, spd, spd_pct, px, pz), end="", flush=True)
            time.sleep(c["loop_interval"])

    def status_line(self, arrow, dist, spd, spd_pct, px, pz) -> str:
        nav = self.navigator
        h = f"({nav.heading[0]:.1f},{nav.heading[1]:.1f})" if nav.heading else "(?,?)"
        # FIX: the original formatted px/pz with :.0f and crashed when coords were None
        p = f"({px:.0f},{pz:.0f})" if px is not None and pz is not None else "(?,?)"
        return (f"[{nav.state[:5]:>5}] Dir:{arrow or '---':>3} | Dist:{dist or 0:>5}m | "
                f"Min:{self.min_dist_seen:>5}m | Spd:{spd:.1f}m/s ({spd_pct * 100:.0f}%) | "
                f"Pos:{p} | H:{h} | {self._est_text(nav)}")

    @staticmethod
    def _est_text(nav):
        """Target estimate for the status line: where, how sure, and whether D* follows it."""
        e = nav.estimator
        if nav.target_wx is None or e.sigma_m is None:
            return f"Est: ... ({len(e.readings)} readings)"
        who = "D*" if nav.est_locked else "arrow"
        return f"Est:({nav.target_wx:.0f},{nav.target_wz:.0f})±{e.sigma_m:.0f}m [{who}]"
