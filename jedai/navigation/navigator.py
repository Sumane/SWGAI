"""
Navigator: the D*-first movement state machine (v0.20 logic, reorganised).

States:
  MOVING        — D* steers. Speed below stop_speed_ratio for stop_debounce
                  ticks -> RECALIBRATING.
  RECALIBRATING — Try neighbouring cells in D* score order. Each attempt
                  marks the cell CLEAR or BLOCKED. Success -> MOVING.
                  All fail -> the runner does an explore sweep.
  EXPLORING     — Cross exploration, run once at startup by the runner.

Target estimation:
  The arrow is a RELATIVE bearing. While arrow == N, the target is straight
  ahead along the bot's world-space heading (from position deltas), so
      target = position + heading * distance.

The panel arrow is used only for the initial align, target estimation, and
as a steering fallback before D* has a path. It never fights D*.

Mounted vs on foot:
  On a vehicle the grid map is NOT updated (vehicle inertia makes the logged
  coordinates lag the real position, so marks land in the wrong cells). D*
  still steers with the map it already has. If the bot gets stuck twice in
  the same place while mounted, tick() returns "DISMOUNT"; once on foot every
  mark is written to the map and the planner.

The navigator talks to the game only through `runner`, which must provide:
    runner.keys            KeyClient (key_down, key_up, tap, stop_movement)
    runner.read_position() -> (x, z) or None   (grabs a frame)
    runner.read_target_distance() -> int or None   (grabs a frame)
    runner.db, runner.planner, runner.planner_active (bool attribute)
    runner.planet, runner.should_stop()
"""

from __future__ import annotations

import math
import time
from collections import deque

from .grid_db import BLOCKED, CLEAR


class Navigator:
    MOVING = "MOVING"
    RECALIBRATING = "RECALIBRATING"
    EXPLORING = "EXPLORING"

    def __init__(self, runner, cfg: dict):
        self.r = runner
        self.cfg = cfg
        self.cell = float(cfg["cell_size"])
        self.reset()

    # ------------------------------------------------------------------
    def reset(self):
        self.state = self.MOVING
        # Heading vector, updated from position deltas during clean movement
        self.heading = None
        self._prev_pos = None
        self._heading_buffer = deque(maxlen=5)
        # Target estimate
        self.target_wx = None
        self.target_wz = None
        self.est_locked = False
        self._est_p1 = None
        self._est_travel = 0.0
        self._est_dist_at_last = None
        # CLEAR interpolation
        self._last_mark_pos = None
        self._last_mark_time = 0.0
        self._min_speed_since_mark = 999.0
        # Stop debounce
        self._stop_ticks = 0
        # Recalibration
        self._recal_candidates = []
        self._recal_visited = set()
        self._recal_origin = None
        self._recal_cycles = 0

    @property
    def mounted(self) -> bool:
        """True while on a vehicle (set by the runner from the vehicle profile)."""
        return bool(getattr(self.r, "mounted", False))

    def after_dismount(self):
        """Back on foot: start recalibration afresh, now with mapping."""
        self.state = self.MOVING
        self._stop_ticks = 0
        self._recal_candidates = []
        self._recal_visited = set()
        self._recal_origin = None
        self._recal_cycles = 0
        self._last_mark_pos = None
        self._min_speed_since_mark = 999.0

    def reset_estimator(self):
        """After a confirmed align to N: clear heading/accumulation, keep the estimate."""
        self._prev_pos = None
        self._heading_buffer = deque(maxlen=5)
        self.heading = None
        self._est_p1 = None
        self._est_travel = 0.0

    # ------------------------------------------------------------------
    # Grid marking
    # ------------------------------------------------------------------
    def mark(self, wx, wz, state, replan_now=False):
        """
        Record a cell in the map. Does nothing while mounted.
        replan_now: also replan immediately (planner.update_cell), otherwise the
        planner is told and replans on its next update (FIX: D* cache miss).
        Returns True if the cell was recorded.
        """
        if self.mounted:
            # mounted_mapping: "none" = map is read-only on a vehicle (default);
            # "clear_only" = keep CLEAR marks (lagged positions were still driven
            # through) but drop BLOCKED marks (those land behind the real obstacle)
            mode = self.cfg.get("mounted_mapping", "none")
            if mode == "none" or (mode == "clear_only" and state != CLEAR):
                return False
        if replan_now and self.r.planner_active:
            self.r.planner.update_cell(wx, wz, state)   # also writes the DB
            return True
        if state == CLEAR:
            self.r.db.mark_clear(self.r.planet, wx, wz)
        else:
            self.r.db.mark_blocked(self.r.planet, wx, wz)
        if self.r.planner_active:
            self.r.planner.note_cell(wx, wz, state)
        return True

    # ------------------------------------------------------------------
    # Heading
    # ------------------------------------------------------------------
    def update_heading(self, pos, during_clean_movement=True):
        if pos is None:
            return
        if self._prev_pos is not None:
            dx = pos[0] - self._prev_pos[0]
            dz = pos[1] - self._prev_pos[1]
            d = math.hypot(dx, dz)
            if d >= 1.0 and during_clean_movement:
                h = (dx / d, dz / d)
                self._heading_buffer.append(h)
                self.heading = h
                self._prev_pos = pos
        else:
            self._prev_pos = pos

    def _seed_heading(self, h, times=3):
        for _ in range(times):
            self._heading_buffer.append(h)
        self.heading = h

    def heading_stable(self):
        """True if the last 3+ heading samples agree within 15 degrees."""
        if len(self._heading_buffer) < 3:
            return False
        ref = self._heading_buffer[-1]
        for h in list(self._heading_buffer)[:-1]:
            dot = max(-1.0, min(1.0, ref[0] * h[0] + ref[1] * h[1]))
            if math.degrees(math.acos(dot)) > 15:
                return False
        return True

    # ------------------------------------------------------------------
    # Target estimation
    # ------------------------------------------------------------------
    @staticmethod
    def project_target(pos, heading, dist):
        """target = pos + heading * dist, or None if it would be behind us."""
        tx = pos[0] + heading[0] * dist
        tz = pos[1] + heading[1] * dist
        to_t = (tx - pos[0], tz - pos[1])
        mag = math.hypot(*to_t)
        if mag > 0:
            dot = heading[0] * (to_t[0] / mag) + heading[1] * (to_t[1] / mag)
            if dot <= 0:
                return None
        return tx, tz

    def _set_target(self, tx, tz, dist, start_pos):
        """Lock a new target estimate and point D* at it."""
        self.target_wx, self.target_wz = tx, tz
        self._est_dist_at_last = dist
        self.est_locked = True
        if self.r.planner_active:
            # FIX: D* Lite's g/rhs values are distances to the goal, so moving
            # the goal on a live planner left it routing to the old target.
            # Re-initialise from the current position when the goal cell changes.
            old_goal = self.r.planner.goal
            self.r.planner.set_goal(tx, tz)
            if self.r.planner.goal != old_goal:
                self.r.planner.set_start(start_pos[0], start_pos[1])
                self.r.planner.initialise()
            return
        self.r.planner.set_goal(tx, tz)
        if not self.r.planner_active:
            self.r.planner.set_start(start_pos[0], start_pos[1])
            try:
                self.r.planner.initialise()
                self.r.planner_active = True
                print(f"\n[D*] Active — target ({tx:.0f},{tz:.0f}) dist={dist}m")
            except Exception as e:
                print(f"\n[D*] Init failed: {e}")

    def try_estimate_target(self, pos, dist, arrow, dstar_straight):
        """
        Re-estimate the target only when: arrow == N, D* routes straight,
        heading is stable, and we've travelled 3m (first estimate) or 10m
        (refresh). Once locked, only refresh if the distance drifted > 50m.
        """
        if pos is None or dist is None:
            return
        if arrow != "N" or not dstar_straight or not self.heading_stable():
            self._est_p1 = None
            self._est_travel = 0.0
            return

        if self.est_locked and (self._est_dist_at_last is None or
                                abs(dist - self._est_dist_at_last) < 50):
            return

        if self._est_p1 is None:
            self._est_p1 = pos
            self._est_travel = 0.0
            return
        self._est_travel = math.hypot(pos[0] - self._est_p1[0], pos[1] - self._est_p1[1])
        if self._est_travel < (3.0 if not self.est_locked else 10.0):
            return

        target = self.project_target(pos, self.heading, dist)
        if target is None:
            print("\n[EST] Rejected — target behind heading")
            self._est_p1 = pos
            self._est_travel = 0.0
            return

        old = (self.target_wx, self.target_wz)
        was_active = self.r.planner_active
        self._est_p1 = None
        self._est_travel = 0.0
        self._set_target(target[0], target[1], dist, pos)
        if was_active:
            # FIX: the original f-string here used an invalid format spec and
            # crashed the navigator the first time a refresh happened.
            old_s = (f"({old[0]:.0f},{old[1]:.0f})" if old[0] is not None else "(?,?)")
            print(f"\n[EST] Target: {old_s} → ({target[0]:.0f},{target[1]:.0f}) dist={dist}m")

    # ------------------------------------------------------------------
    # CLEAR interpolation
    # ------------------------------------------------------------------
    def mark_clear_interpolated(self, pos, speed_pct, now=None):
        now = time.time() if now is None else now
        self._min_speed_since_mark = min(self._min_speed_since_mark, speed_pct)
        if now - self._last_mark_time < (1.0 / self.cfg["clear_hz"]):
            return
        if pos is None:
            return
        if self._last_mark_pos is not None:
            d = math.hypot(pos[0] - self._last_mark_pos[0], pos[1] - self._last_mark_pos[1])
            if d >= self.cell:
                if self._min_speed_since_mark >= self.cfg["stop_speed_ratio"]:
                    steps = max(1, int(d / self.cell))
                    for i in range(steps + 1):
                        t = i / steps
                        self.mark(self._last_mark_pos[0] + t * (pos[0] - self._last_mark_pos[0]),
                                   self._last_mark_pos[1] + t * (pos[1] - self._last_mark_pos[1]),
                                   CLEAR)
                else:
                    self.mark(pos[0], pos[1], CLEAR)
                self._last_mark_pos = pos
                self._last_mark_time = now
                self._min_speed_since_mark = 999.0
        else:
            self._last_mark_pos = pos
            self._last_mark_time = now
        if self.r.planner_active:
            self.r.planner.update_start(pos[0], pos[1])

    # ------------------------------------------------------------------
    # Steering
    # ------------------------------------------------------------------
    def dstar_is_straight(self, pos):
        """True if D*'s next cell is within ~20 degrees of the current heading."""
        if not self.r.planner_active or pos is None or self.heading is None:
            return False
        wp = self.r.planner.next_waypoint()
        if wp is None:
            return False
        dx, dz = wp[0] - pos[0], wp[1] - pos[1]
        mag = math.hypot(dx, dz)
        if mag < 0.1:
            return True
        return self.heading[0] * (dx / mag) + self.heading[1] * (dz / mag) > 0.94

    def _turn_cross(self, dx, dz):
        """Sign-corrected cross product: > 0 means 'turn RIGHT' (see right_turn_sign)."""
        return self.cfg.get("right_turn_sign", 1) * (self.heading[0] * dz - self.heading[1] * dx)

    def steer_toward(self, pos, wx, wz):
        """-1 = left, 0 = straight, +1 = right, toward world point (wx, wz)."""
        if pos is None or self.heading is None:
            return 0
        dx, dz = wx - pos[0], wz - pos[1]
        mag = math.hypot(dx, dz)
        if mag < 1.0:
            return 0
        # FIX: normalise so the deadband is an angle (sin θ), not metres.
        cross = self._turn_cross(dx, dz) / mag
        behind = (self.heading[0] * dx + self.heading[1] * dz) < 0
        if abs(cross) < self.cfg["steer_deadband"]:
            # FIX: a target straight BEHIND also has cross ≈ 0; turn, don't drive away
            return (1 if cross >= 0 else -1) if behind else 0
        return 1 if cross > 0 else -1

    # ------------------------------------------------------------------
    # Recalibration
    # ------------------------------------------------------------------
    def recal_candidates(self, pos):
        """Non-blocked neighbour cells, best first (D* cost, else target/heading)."""
        if pos is None:
            return []
        cx = int(math.floor(pos[0] / self.cell))
        cz = int(math.floor(pos[1] / self.cell))
        out = []
        for dcx in (-1, 0, 1):
            for dcz in (-1, 0, 1):
                if dcx == 0 and dcz == 0:
                    continue
                nb = (cx + dcx, cz + dcz)
                wx = nb[0] * self.cell + self.cell / 2
                wz = nb[1] * self.cell + self.cell / 2
                if self.r.db.get_state(self.r.planet, wx, wz) == BLOCKED:
                    continue
                if self.r.planner_active:
                    score = self.r.planner.cost_to_goal(nb)
                elif self.target_wx is not None:
                    score = math.hypot(wx - self.target_wx, wz - self.target_wz)
                elif self.heading is not None:
                    ddx, ddz = wx - pos[0], wz - pos[1]
                    mag = math.hypot(ddx, ddz)
                    score = -((self.heading[0] * ddx + self.heading[1] * ddz) / mag if mag > 0 else 0)
                else:
                    score = math.hypot(wx - pos[0], wz - pos[1])
                out.append((score, wx, wz, nb))
        out.sort(key=lambda c: c[0])
        return out

    def attempt_cell(self, pos, wx, wz, duration=None):
        """
        Align toward (wx, wz), drive forward for `duration` seconds and check
        progress. >= 1.5m moved -> CLEAR (and seeds heading), else BLOCKED.
        Returns (success, final_pos).
        """
        duration = self.cfg["recal_attempt_secs"] if duration is None else duration
        if pos is None:
            return False, pos
        dx, dz = wx - pos[0], wz - pos[1]
        d = math.hypot(dx, dz)
        if d < 0.5:
            return True, pos

        keys = self.r.keys
        keys.stop_movement()
        time.sleep(0.2)
        self.align_to_direction(dx / d, dz / d)

        start = self.r.read_position()
        if start is None:
            return False, pos

        keys.key_down("FORWARD")
        deadline = time.time() + duration
        final_pos = start
        while time.time() < deadline:
            if self.r.should_stop():
                keys.key_up("FORWARD")
                return False, final_pos
            p = self.r.read_position()
            if p is not None:
                final_pos = p
        keys.key_up("FORWARD")

        progress = math.hypot(final_pos[0] - start[0], final_pos[1] - start[1])
        tag = " (mounted: not mapped)" if self.mounted else ""
        if progress >= 1.5:
            self.mark(final_pos[0], final_pos[1], CLEAR)
            if self.r.planner_active:
                self.mark(wx, wz, CLEAR, replan_now=True)
            print(f"[CELL] ({wx:.0f},{wz:.0f}) CLEAR (moved {progress:.1f}m){tag}")
            mv = (final_pos[0] - start[0], final_pos[1] - start[1])
            mv_mag = math.hypot(*mv)
            if mv_mag > 0:
                self._seed_heading((mv[0] / mv_mag, mv[1] / mv_mag))
            return True, final_pos

        self.mark(wx, wz, BLOCKED, replan_now=True)
        print(f"[CELL] ({wx:.0f},{wz:.0f}) BLOCKED (moved {progress:.1f}m){tag}")
        return False, pos

    def align_to_direction(self, ndx, ndz, timeout=3.0):
        """
        Rotate until heading matches (ndx, ndz) within 15 degrees.

        KNOWN ISSUE (code review, not changed): the heading only updates when
        the bot has moved >= 1m, but this loop only turns on the spot, so the
        heading never changes and the loop usually runs to the timeout.
        """
        deadline = time.time() + timeout
        keys = self.r.keys
        while time.time() < deadline:
            if self.r.should_stop():
                return
            if self.heading is not None:
                dot = max(-1.0, min(1.0, self.heading[0] * ndx + self.heading[1] * ndz))
                if math.degrees(math.acos(dot)) < 15:
                    return
                keys.tap("RIGHT" if self._turn_cross(ndx, ndz) > 0 else "LEFT", 0.1)
            else:
                keys.tap("RIGHT", 0.1)
            p = self.r.read_position()
            if p is not None:
                self.update_heading(p)
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Cross exploration (once at startup)
    # ------------------------------------------------------------------
    def run_cross_exploration(self, origin_pos):
        """
        Seed the heading with a 2s forward move (the bot has just aligned to
        arrow = N), take a first target estimate from it, then try 20m legs
        forward and back (and left/right if both were short).
        Returns (target_wx, target_wz), either can be None.
        """
        cfg = self.cfg
        keys = self.r.keys
        print(f"\n🔍  Cross exploration from ({origin_pos[0]:.0f},{origin_pos[1]:.0f})...")

        def try_leg(label, dx, dz):
            wx = origin_pos[0] + dx * cfg["explore_leg_m"]
            wz = origin_pos[1] + dz * cfg["explore_leg_m"]
            print(f"   Leg {label}: attempting ({wx:.0f},{wz:.0f})")
            success, final = self.attempt_cell(origin_pos, wx, wz, duration=cfg["explore_leg_secs"])
            travelled = math.hypot(final[0] - origin_pos[0], final[1] - origin_pos[1])
            print(f"   Leg {label}: {'CLEAR' if success else 'BLOCKED'} ({travelled:.1f}m) — returning")
            self.attempt_cell(final, origin_pos[0], origin_pos[1], duration=cfg["explore_leg_secs"])
            return travelled

        print("   Seeding forward direction...")
        keys.key_down("FORWARD")
        time.sleep(2.0)
        keys.key_up("FORWARD")
        seed = self.r.read_position()
        seed_dist = self.r.read_target_distance()

        if seed is not None and seed[0] != origin_pos[0]:
            fdx, fdz = seed[0] - origin_pos[0], seed[1] - origin_pos[1]
            fmag = math.hypot(fdx, fdz)
            if fmag > 0.5:
                fwd = (fdx / fmag, fdz / fmag)
                self._seed_heading(fwd)
                self.mark(seed[0], seed[1], CLEAR)
                print(f"   Forward direction: ({fwd[0]:.2f},{fwd[1]:.2f})")
                if seed_dist is not None:
                    target = self.project_target(seed, fwd, seed_dist)
                    if target is not None:
                        self.target_wx, self.target_wz = target
                        self._est_dist_at_last = seed_dist
                        self.est_locked = True
                        print(f"   Initial target estimate: ({target[0]:.0f},{target[1]:.0f}) dist={seed_dist}m")
            self.attempt_cell(seed, origin_pos[0], origin_pos[1], duration=cfg["explore_leg_secs"])
        else:
            print("   Seed move failed — heading unknown, using origin")

        if self.heading is None:
            print("   No heading — skipping cross, will estimate during navigation")
            return self.target_wx, self.target_wz

        fwd = self.heading
        rgt = (fwd[1], -fwd[0])
        bwd = (-fwd[0], -fwd[1])
        lft = (-fwd[1], fwd[0])

        fwd_travel = try_leg("FORWARD", *fwd)
        bwd_travel = try_leg("BACKWARD", *bwd)
        if fwd_travel < 10.0 and bwd_travel < 10.0:
            print("   Narrow passage detected — trying LEFT/RIGHT")
            try_leg("LEFT", *lft)
            try_leg("RIGHT", *rgt)

        if self.target_wx is not None and not self.r.planner_active:
            p = self.r.read_position()
            if p is not None:
                self._set_target(self.target_wx, self.target_wz, self._est_dist_at_last, p)

        print("   Cross exploration complete.")
        return self.target_wx, self.target_wz

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------
    def tick(self, pos, speed_pct, arrow, dist):
        """
        One navigation step. Returns:
          -1 / 0 / +1   tap left / nothing / tap right
          "STOP"        stop forward movement (just entered RECALIBRATING)
          "RECAL"       runner should call do_recal()
          "DISMOUNT"    stuck twice in the same place on a vehicle: get off
        """
        if self.state == self.MOVING:
            self.update_heading(pos, during_clean_movement=True)

        self.mark_clear_interpolated(pos, speed_pct)

        if self.r.planner_active and pos is not None:
            self.r.planner.update_start(pos[0], pos[1])

        self.try_estimate_target(pos, dist, arrow, self.dstar_is_straight(pos))

        if self.state == self.MOVING:
            if speed_pct < self.cfg["stop_speed_ratio"]:
                self._stop_ticks += 1
                if self._stop_ticks >= self.cfg["stop_debounce"]:
                    self._stop_ticks = 0
                    return self._enter_recal(pos)
            else:
                self._stop_ticks = 0

            if self.r.planner_active and self.r.planner.has_path():
                wp = self.r.planner.next_waypoint()
                if wp is not None:
                    return self.steer_toward(pos, wp[0], wp[1])

            # No D* path yet — panel arrow fallback
            if arrow in ("NW", "W"):
                return -1
            if arrow in ("NE", "E"):
                return 1
            return 0

        if self.state == self.RECALIBRATING:
            return "RECAL"
        return 0

    def _enter_recal(self, pos):
        print("\n[NAV] Stopped — entering RECALIBRATING")
        self.state = self.RECALIBRATING

        if pos is not None and self.heading is not None:
            bx = pos[0] + self.heading[0] * self.cell
            bz = pos[1] + self.heading[1] * self.cell
            self.mark(bx, bz, BLOCKED, replan_now=True)

        # FIX: guard pos=None (the original crashed here if coords weren't read)
        if pos is not None:
            if (self._recal_origin is None or
                    math.hypot(pos[0] - self._recal_origin[0], pos[1] - self._recal_origin[1])
                    > self.cfg["stuck_radius_m"]):
                self._recal_cycles = 0
                self._recal_visited = set()
                self._recal_origin = pos
            else:
                self._recal_cycles += 1

            stuck_count = self._recal_cycles + 1
            if self.mounted and stuck_count >= self.cfg["dismount_after_stuck"]:
                print(f"\n[NAV] Stuck {stuck_count}x within {self.cfg['stuck_radius_m']:.0f}m "
                      f"on a vehicle — dismounting")
                return "DISMOUNT"

        self._recal_candidates = [c for c in self.recal_candidates(pos)
                                  if c[3] not in self._recal_visited]
        return "STOP"

    def do_recal(self, pos):
        """
        Try the next candidate cell.
        Returns True (moved, resume), False (all failed, explore), None (keep going).
        """
        if not self._recal_candidates:
            self.state = self.MOVING
            return False

        score, wx, wz, node = self._recal_candidates.pop(0)
        self._recal_visited.add(node)
        print(f"\n[RECAL] Attempting ({wx:.0f},{wz:.0f}) score={score:.1f}")
        success, final_pos = self.attempt_cell(pos, wx, wz)

        if success:
            self.state = self.MOVING
            if self.r.planner_active:
                self.r.planner.update_start(wx, wz)
            if not self.est_locked and final_pos is not None and self.heading is not None:
                fd = self.r.read_target_distance()
                if fd is not None:
                    target = self.project_target(final_pos, self.heading, fd)
                    if target is not None:
                        self._set_target(target[0], target[1], fd, final_pos)
                        print(f"[RECAL] Target seeded: ({target[0]:.0f},{target[1]:.0f}) dist={fd}m")
            return True

        if not self._recal_candidates:
            if self._recal_cycles >= 4:
                print(f"\n[RECAL] {self._recal_cycles} cycles — forcing explore")
                self._recal_cycles = 0
                self._recal_visited.clear()
            self.state = self.MOVING
            return False
        return None
