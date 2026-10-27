"""
NavigationRunner: wires vision, keys, grid map and the Navigator together and
runs the interactive "drive to a waypoint" loop. (Was SWGMasterNavigator.)
"""

from __future__ import annotations

import math
import sys
import time

from ..control.channel import CommandChannel
from ..control.keys import KeyClient, kill_pressed
from ..perception.screen import Screen
from ..perception.text import fuzzy_match
from .dstar_lite import DStarLite
from .grid_db import CLEAR, NavDatabase
from .navigator import Navigator
from .reading_filter import MotionGate
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
        self.db = db or NavDatabase(str(db_path))
        p = settings.planner
        self.planner = DStarLite(self.db, self.planet,
                                 cost_clear=p["cost_clear"],
                                 cost_unexplored=p["cost_unexplored"],
                                 search_radius_m=p["search_radius_m"],
                                 goal_reach_m=p["goal_reach_m"],
                                 cost_near_blocked=p.get("cost_near_blocked", 10.0))
        self.planner_active = False
        self.navigator = Navigator(self, self.cfg)

        self.target_name = ""
        self.last_valid_dist = 99999
        self.min_dist_seen = 99999
        self.close_read_count = 0
        self.player = (None, None, None)   # last accepted (x, y, z)
        # Drop readings that would need the bot to move faster than its vehicle
        self.coord_gate = MotionGate(self.cfg["max_coord_jump"] / 5.0, lambda: self.max_speed,
                                     clock=lambda: time.time())
        self.dist_gate = MotionGate(10.0, lambda: self.max_speed, clock=lambda: time.time())
        self.rejected_dists = 0

    # =========================================================================
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
        self.keys.stop_movement()
        self.keys.dismount(c["dismount_method"], c.get("dismount_command", "/dismount"),
                           c.get("dismount_key", ""))
        time.sleep(c["dismount_settle_secs"])
        self.apply_vehicle(c["foot_vehicle"])
        self.navigator.after_dismount()

    def shutdown(self):
        self.keys.release_all()
        print("✓ All keys released")

    # =========================================================================
    # Vision
    # =========================================================================
    def update_coords(self, frame):
        """Read coords; reject jumps > max_coord_jump. Returns last accepted (x, y, z)."""
        x, y, z = self.hud.read_coords(frame)
        if x is not None and z is not None:
            if self.coord_gate.check((x, z)) is None:
                return self.player        # implausible jump: misread
            self.player = (x, y, z)
            self.vel_tracker.update(x, z)
        return self.player

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
        dist = self.dist_gate.check(best.distance)
        if dist is None and best.distance is not None:
            self.rejected_dists += 1
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

    def choose_waypoint(self):
        waypoints = self.scan_for_waypoints()
        if not waypoints:
            print("No waypoints found.")
            return None
        print("\n--- WAYPOINTS FOUND ---")
        for i, wp in enumerate(waypoints):
            print(f"  [{i}] '{wp['name']}' ({wp['distance']}m)")
        choice = input("\nSelect ID: ").strip()
        if not choice.isdigit() or int(choice) >= len(waypoints):
            print("Invalid selection.")
            return None
        return waypoints[int(choice)]

    def navigate(self):
        c = self.cfg
        self.choose_vehicle()
        target = self.choose_waypoint()
        if target is None:
            return

        self.target_name = target["name"]
        print(f"\n🎯  Target: '{self.target_name}' ({target['distance']}m)")
        print(f"   Vehicle: {self.current_vehicle} (max ~{self.max_speed} m/s)")
        print(f"   Corridor: ±{c['corridor_width']}m | Clear mark: {c['clear_hz']}Hz")

        self.navigator.reset()
        self.planner_active = False
        self.min_dist_seen = self.last_valid_dist = target["distance"]
        self.dist_gate.reset(target["distance"])
        self.close_read_count = 0

        run_id = self.db.start_run(self.planet, self.player[0] or 0, self.player[2] or 0, 0, 0)

        print(f"⏳  Resuming in {c['tab_in_pause']}s...")
        time.sleep(c["tab_in_pause"])
        print("🧭  Aligning to target...")
        self.keys.stop_movement()
        time.sleep(0.3)
        self.hard_align("N")

        origin = self.read_position() or (0.0, 0.0)
        self.navigator.run_cross_exploration(origin)

        self.hard_align("N")
        self.keys.key_down("FORWARD")

        try:
            arrived = self._main_loop(run_id)
        finally:
            print("\n\n🧹  Shutting down...")
            self.shutdown()
        if arrived:
            print("\n\n🏁  SUCCESS!")

    def _main_loop(self, run_id) -> bool:
        c, k, nav = self.cfg, self.keys, self.navigator
        lost_timer = None

        while True:
            if self.should_stop():
                print("\n🛑 Kill key pressed - stopping")
                return False

            frame = self.screen.grab()
            px, _, pz = self.update_coords(frame)
            arrow, dist, _ = self.target_status(frame)
            pos = (px, pz) if px is not None else None

            # Lost the waypoint
            if dist is None:
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
            spd = self.vel_tracker.speed()
            spd_pct = spd / self.max_speed if self.max_speed > 0 else 0

            # Arrival
            if dist <= c["stop_dist"]:
                self.close_read_count += 1
                if dist <= c["final_creep_dist"]:
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
            if steer == "STOP":
                k.key_up("FORWARD")
            elif steer == "DISMOUNT":
                k.key_up("FORWARD")
                self.dismount()
                k.key_down("FORWARD")
            elif steer == "RECAL":
                result = nav.do_recal(pos)
                if result is True:
                    k.key_down("FORWARD")
                elif result is False:
                    k.key_up("FORWARD")
                    self.explore_sweep()
                    k.key_down("FORWARD")
            elif steer == -1:
                k.tap("LEFT", 0.1)
            elif steer == 1:
                k.tap("RIGHT", 0.1)

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
