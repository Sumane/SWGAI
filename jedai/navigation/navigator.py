"""
Navigator: the D*-first movement state machine (v0.20 logic, reorganised).

States:
  MOVING        — D* steers. Speed below stop_speed_ratio for stop_debounce
                  ticks -> RECALIBRATING.
  RECALIBRATING — Try neighbouring cells in D* score order. Each attempt
                  marks the cell CLEAR or BLOCKED. Success -> MOVING.
                  All fail -> the runner does an explore sweep.
  EXPLORING     — Cross exploration, run once at startup by the runner.

Target estimation (target_estimator.py):
  Every few metres the bot records (position, waypoint distance, arrow). The
  distances are circles round the target; as the bot moves they intersect at
  it. The arrow only says which 45° sector the target is in. The estimate is
  refined on every reading and D* is pointed at it once the geometry pins it
  down (estimator.confident()). Before that the bot follows the arrow.

  FIX: v0.20 projected   target = position + heading * distance   once, when
  the arrow first read N. N means anywhere within ±22.5°, which at 3 km is up
  to ±1100 m sideways, and the estimate was never refreshed because the arrow
  then stopped reading N. D* drove to that wrong point: "navigating away from
  the target".

Mounted vs on foot:
  CLEAR is recorded whether mounted or not: a cell the bot drove through is
  clear. BLOCKED is only recorded on foot: a vehicle takes metres to stop and
  slides along walls, so it can't tell which cell stopped it. If the bot gets
  stuck twice in the same place while mounted, tick() returns "DISMOUNT"; on
  foot, blocked cells are confirmed and D* plans around them.

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
from .target_estimator import ARROW_DEG, TargetEstimator


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
        self.est_locked = False          # True once D* has been pointed at a confident estimate
        self._est_dist_at_last = None
        self.estimator = TargetEstimator(
            min_spacing_m=float(self.cfg.get("estimate_spacing_m", 5.0)),
            dist_sigma_m=float(self.cfg.get("estimate_dist_sigma_m", 3.0)),
            right_turn_sign=int(self.cfg.get("right_turn_sign", -1)))
        # CLEAR interpolation
        self._last_mark_pos = None
        self._last_mark_time = 0.0
        self._min_speed_since_mark = 999.0
        # Stop debounce
        self._stop_ticks = 0
        self._pos_hist = deque()
        # Recalibration
        self._recal_candidates = []
        self._recal_visited = set()
        self._recal_origin = None
        self._recal_cycles = 0
        self._track = deque(maxlen=2000)   # where the bot has been (for backtracking)
        self._escape = None                # {"until", "target", "why"} after an unstick
        self._backtracks = 0
        self._pushes = []                  # (pos, heading) each time it got stuck here
        self._stuck_hist = deque(maxlen=200)   # (time, pos, heading) of every stuck

    @property
    def mounted(self) -> bool:
        """True while on a vehicle (set by the runner from the vehicle profile)."""
        return bool(getattr(self.r, "mounted", False))

    def after_dismount(self):
        """Back on foot: start recalibration afresh, now with mapping."""
        self._escape = None
        self.state = self.MOVING
        self._stop_ticks = 0
        self._pos_hist = deque()
        self._recal_candidates = []
        self._recal_visited = set()
        self._recal_origin = None
        self._recal_cycles = 0
        self._last_mark_pos = None
        self._min_speed_since_mark = 999.0

    def reset_estimator(self):
        """After a re-align: clear the heading (it's stale). The target readings stay valid."""
        self._prev_pos = None
        self._heading_buffer = deque(maxlen=5)
        self.heading = None

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
        if state == CLEAR and self._near_cell_edge(wx, wz) and \
                self.r.db.get_state(self.r.planet, wx, wz) == BLOCKED:
            # FIX (simulator, then explains the in-game flip-flopping): a bot
            # pressed against a wall is often reported just inside the wall's
            # 5 m cell (whole-metre coordinates, walls not on cell lines). Marking
            # that CLEAR because "it's standing there" erased the wall every time.
            # A blocked cell is only cleared from its middle.
            return False
        if state == BLOCKED and not (self.mounted and self.cfg.get("mounted_mapping", "clear_only") != "all"):
            self._ev("blocked", cell=(wx, wz))
        if self.mounted:
            # mounted_mapping: "clear_only" (default) = keep CLEAR, drop BLOCKED;
            # "none" = nothing while mounted; "all" = old behaviour
            mode = self.cfg.get("mounted_mapping", "clear_only")
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
            # FIX: coordinates are whole metres, so a 1 m move only gives the
            # heading to the nearest 45°. Wait for a few metres.
            if d >= float(self.cfg.get("heading_min_move_m", 3.0)) and during_clean_movement:
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

    _goal_xy = None

    _carrot = None

    def _dstar_goal(self, tx, tz, pos):
        """
        The target itself if it's within dstar_horizon_m, otherwise a point that
        far along the straight line to it (a carrot). Keeps D* fast in Python;
        the carrot moves on as the bot gets near it.
        """
        horizon = float(self.cfg.get("dstar_horizon_m", 300.0))
        dx, dz = tx - pos[0], tz - pos[1]
        d = math.hypot(dx, dz)
        if d <= horizon or d == 0:
            return tx, tz, False
        return pos[0] + dx / d * horizon, pos[1] + dz / d * horizon, True

    def _advance_carrot(self, pos):
        """Move the carrot on once the bot is halfway to it."""
        if self._carrot is None or pos is None or self._goal_xy is None:
            return
        horizon = float(self.cfg.get("dstar_horizon_m", 300.0))
        if math.hypot(self._carrot[0] - pos[0], self._carrot[1] - pos[1]) < horizon / 2:
            self._set_target(self._goal_xy[0], self._goal_xy[1], self._est_dist_at_last, pos)

    def _set_target(self, tx, tz, dist, start_pos):
        """Point D* at a target estimate (or at a carrot on the way to it)."""
        self.target_wx, self.target_wz = tx, tz
        self._goal_xy = (tx, tz)
        self._est_dist_at_last = dist
        self.est_locked = True
        gx, gz, is_carrot = self._dstar_goal(tx, tz, start_pos)
        self._carrot = (gx, gz) if is_carrot else None
        tx, tz = gx, gz
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

    def observe_target(self, pos, dist, arrow=None, force_goal=False):
        """
        Feed one reading to the estimator and, when it has moved enough, move
        the D* goal to the new estimate.

        The arrow is only used while the heading is trustworthy (it is relative
        to the way the bot faces). The goal moves when the estimate is confident,
        or always with force_goal (e.g. the bot is stuck and needs some goal).
        Returns True if the D* goal was (re)set.
        """
        if pos is None or dist is None:
            return False
        use_arrow = arrow if (self.heading is not None and self.heading_stable()) else None
        if not self.estimator.add(pos, dist, use_arrow, self.heading):
            return False
        est = self.estimator.solve()
        if est is None:
            return False
        self.target_wx, self.target_wz = est
        # Once D* is running (even if it was started early because the bot got
        # stuck), keep its goal on the latest estimate. FIX: a goal set from an
        # early, poor estimate was never corrected unless the estimate became
        # "confident", so D* kept driving to the wrong place.
        if not (force_goal or self.r.planner_active or
                self.estimator.confident(self._confident_sigma(dist))):
            return False
        goal_ok = self.r.planner_active and self.r.planner.goal is not None
        if goal_ok and self._goal_xy is not None:
            moved = math.hypot(est[0] - self._goal_xy[0], est[1] - self._goal_xy[1])
            if moved < max(float(self.cfg.get("goal_move_m", 20.0)), 0.02 * dist):
                return False
        old = self._goal_xy
        self._set_target(est[0], est[1], dist, pos)
        self._ev("goal", pos=pos, est=est, sigma=self.estimator.sigma_m, dist=dist,
                 readings=len(self.estimator.readings), carrot=self._carrot)
        old_s = f"({old[0]:.0f},{old[1]:.0f})" if old is not None else "(none)"
        print(f"\n[EST] Target {old_s} -> ({est[0]:.0f},{est[1]:.0f}) "
              f"±{self.estimator.sigma_m:.0f}m dist={dist}m readings={len(self.estimator.readings)}")
        return True

    def _confident_sigma(self, dist):
        """How sure we must be before D* follows the estimate: 25 m, or 4% of the range far away."""
        return max(float(self.cfg.get("estimate_confident_m", 25.0)), 0.04 * dist)

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
                        x = self._last_mark_pos[0] + t * (pos[0] - self._last_mark_pos[0])
                        z = self._last_mark_pos[1] + t * (pos[1] - self._last_mark_pos[1])
                        # FIX (in-game, Theed): the straight line between two reads
                        # 2 s apart cuts corners, and it was re-clearing cells the
                        # bot had just found blocked, so D* sent it back into the
                        # same wall 31 times. Only a cell the bot was actually read
                        # standing in (the ends of the line) may clear a block.
                        if 0 < i < steps and self.r.db.get_state(self.r.planet, x, z) == BLOCKED:
                            continue
                        self.mark(x, z, CLEAR)
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

    def turn_tap_secs(self):
        """
        How long to hold LEFT/RIGHT this tick: proportional to how far off we are.
        FIX: every correction was a 0.1 s tap (~10°) once per ~2 s tick, about
        5°/s. At 16 m/s that is a 180 m turning circle, and in the simulator the
        bot orbited the waypoint 100 m out for six minutes.
        """
        rate = float(getattr(self.r, "turn_rate_deg_s", None) or self.cfg.get("turn_rate_deg_s", 100.0))
        gain = float(self.cfg.get("steer_gain", 0.6))   # < 1: don't overshoot with coordinate lag
        return max(0.1, min(float(self.cfg.get("steer_max_tap_secs", 0.8)),
                            gain * self.steer_angle_deg / rate))

    def lookahead_waypoint(self):
        """
        A point a few cells along the D* path, not just the next cell.
        FIX: the next cell is only 5 m away; at vehicle speed the bot is past it
        before the next tick, so steering chased a point that kept appearing
        just behind it.
        """
        n = int(self.cfg.get("steer_lookahead_cells", 3))
        # FIX: at ~2 s per tick a runner covers 20 m between steering decisions,
        # so a point 15 m ahead was behind it by the next tick and it circled.
        # Look at least 1.5 ticks of travel ahead.
        speed = float(getattr(self.r, "max_speed", 0) or 0)
        n = max(n, int(math.ceil(1.5 * speed * self._tick_dt / self.cell)))
        path = self.r.planner.get_planned_path(max_steps=n + 1)
        if len(path) > 1:
            return path[min(n, len(path) - 1)]
        return self.r.planner.next_waypoint()

    _behind_turn = 0
    _tick_dt = 0.5           # seconds per main-loop tick (measured)
    _last_tick_t = None
    steer_angle_deg = 0.0

    def _near_cell_edge(self, wx, wz, margin=1.5):
        fx = wx - math.floor(wx / self.cell) * self.cell
        fz = wz - math.floor(wz / self.cell) * self.cell
        return min(fx, self.cell - fx, fz, self.cell - fz) < margin

    def _cell_of(self, wx, wz):
        return (int(math.floor(wx / self.cell)), int(math.floor(wz / self.cell)))

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
        dot = (self.heading[0] * dx + self.heading[1] * dz) / mag
        self.steer_angle_deg = math.degrees(math.atan2(abs(cross), dot))   # how far off, 0..180
        behind = dot < 0
        if behind:
            # FIX: a target behind has cross ≈ 0 and its sign flips with every
            # wobble, so the bot tapped left, right, left... and drove 700 m the
            # wrong way in the simulator. Pick a side once and keep turning.
            if self._behind_turn == 0:
                self._behind_turn = 1 if cross >= 0 else -1
            return self._behind_turn
        self._behind_turn = 0
        if abs(cross) < self.cfg["steer_deadband"]:
            return 0
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
        Drive to (wx, wz), steering on the way, for up to `duration` seconds.
        Got there, or got clearly closer -> CLEAR (and seeds heading); otherwise
        the cell is marked BLOCKED (on foot). Returns (success, final_pos).

        FIX: this used to turn on the spot first (align_to_direction), but the
        heading only updates when the bot moves, so the turn ran blind until it
        timed out and the bot then drove off in whatever direction it faced.
        Now it holds FORWARD and steers, so the heading keeps updating.
        """
        duration = self.cfg["recal_attempt_secs"] if duration is None else duration
        if pos is None:
            return False, pos
        d = math.hypot(wx - pos[0], wz - pos[1])
        if d < 0.5:
            return True, pos

        keys = self.r.keys
        keys.stop_movement()
        time.sleep(0.2)
        start = self.r.read_position()
        if start is None:
            return False, pos
        d = math.hypot(wx - start[0], wz - start[1])
        if self.heading is not None and \
                (self.heading[0] * (wx - start[0]) + self.heading[1] * (wz - start[1])) < 0:
            duration += 2.0                       # turning round first takes a while
        self._prev_pos = start
        self._behind_turn = 0

        if self.heading is None:
            # FIX: with no heading, steering returns "straight" and the bot drove
            # 50-80 m in whatever direction it faced. A short pulse gives a heading.
            self._prev_pos = start
            keys.tap("FORWARD", 0.4)
            p = self.r.read_position()
            if p is not None:
                self.update_heading(p)
                start = p
                d = math.hypot(wx - start[0], wz - start[1])
        target_cell = self._cell_of(wx, wz)
        travelled, last_p = 0.0, start
        holding = False
        mode = None              # None = forward/turning, "back" = reversing
        deadline = time.time() + duration
        final_pos = start
        while time.time() < deadline:
            if self.r.should_stop():
                break
            p = self.r.read_position()
            if p is None:
                continue
            final_pos = p
            travelled += math.hypot(p[0] - last_p[0], p[1] - last_p[1])
            last_p = p
            if mode != "back":
                self.update_heading(p)
            left = math.hypot(wx - p[0], wz - p[1])
            if travelled > d + 15.0:
                break        # wandering or overshooting: give up on this cell
            # FIX: stop at the cell instead of holding FORWARD for the full
            # duration (a 20 m leg became 100 m on a swoop)
            if left <= max(1.5, 0.2 * d) or (self._cell_of(*p) == target_cell and d - left >= 1.0):
                break
            if self.heading is not None and left > 0.5:
                dot = (self.heading[0] * (wx - p[0]) + self.heading[1] * (wz - p[1])) / left
                if mode == "back" or (mode is None and dot < -0.5):
                    # Cell (mostly) behind: reverse into it rather than turning
                    # round. Turning on foot means a forward pulse for every
                    # 30° to see the new heading, which spiralled 12 m round a 5 m cell.
                    if mode != "back":
                        if holding:
                            keys.key_up("FORWARD"); holding = False
                        keys.key_down("BACK"); mode = "back"
                    if dot > -0.2:            # no longer behind us: stop reversing
                        break
                    continue
                if dot < 0.64:
                    # More than ~50° off: turn with FORWARD released, then a short
                    # forward pulse so the heading (from movement) updates. Holding
                    # FORWARD while turning drove a 10 m circle round a 5 m cell.
                    if holding:
                        keys.key_up("FORWARD"); holding = False
                    turn = 1 if self._turn_cross(wx - p[0], wz - p[1]) >= 0 else -1
                    keys.tap("RIGHT" if turn > 0 else "LEFT", 0.25)
                    keys.tap("FORWARD", 0.15)
                    continue
            if not holding:
                keys.key_down("FORWARD"); holding = True
            turn = self.steer_toward(p, wx, wz)
            if turn:
                keys.tap("RIGHT" if turn > 0 else "LEFT", 0.1)
        if holding:
            keys.key_up("FORWARD")
        if mode == "back":
            keys.key_up("BACK")

        progress = math.hypot(final_pos[0] - start[0], final_pos[1] - start[1])
        # FIX: success used to be "moved 1.5 m in any direction", and the target
        # cell was then stamped CLEAR. Sliding along a wall counted, so cells
        # INSIDE the wall were marked clear again and again, and the bot never
        # learnt the wall. Now it must have got closer, and the cell is only
        # marked CLEAR if the bot actually got there.
        left = math.hypot(wx - final_pos[0], wz - final_pos[1])
        # in the cell AND actually moved towards it (a bot pressed against a wall
        # can already be reported inside the wall's 5 m cell)
        reached = self._cell_of(*final_pos) == target_cell and d - left >= 1.0
        closer = d - left >= max(1.5, 0.5 * d)
        tag = " (mounted: not mapped)" if self.mounted else ""
        if reached or closer:
            self.mark(final_pos[0], final_pos[1], CLEAR)
            if self.r.planner_active and reached:
                self.mark(wx, wz, CLEAR, replan_now=True)
            print(f"[CELL] ({wx:.0f},{wz:.0f}) CLEAR (moved {progress:.1f}m){tag}")
            if self.heading is not None:
                self._seed_heading(self.heading)    # latest facing, from the drive
            return True, final_pos

        if d - left >= 1.0 or progress >= 4.0:
            # Got closer but didn't get there, or moved about without getting
            # closer (turning round took too long). Either way it says nothing
            # about the cell: don't mark it. BLOCKED = pushed and couldn't move.
            print(f"[CELL] ({wx:.0f},{wz:.0f}) not reached (moved {progress:.1f}m), not marked{tag}")
            return False, final_pos
        # FIX: never mark the target BLOCKED from here. With ~2 s position
        # reads, "didn't move" mostly meant "hadn't got going yet", and clear
        # cells the bot had just come through were marked as walls. Real
        # obstacles are marked by the main loop when the bot is pushing into one.
        print(f"[CELL] ({wx:.0f},{wz:.0f}) not reached (moved {progress:.1f}m), not marked{tag}")
        return False, final_pos

    def align_to_direction(self, ndx, ndz, timeout=3.0):
        """
        Rotate until heading matches (ndx, ndz) within 15 degrees.

        NOT USED any more (attempt_cell steers while driving instead). Kept for
        reference: the heading only updates when the bot has moved >= 1 m, but
        this loop only turns on the spot, so it usually ran to the timeout.
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
            leg_dist = self.r.read_target_distance()
            if leg_dist is not None:
                self.estimator.add(final, leg_dist)     # sideways legs fix the target best
            print(f"   Leg {label}: {'CLEAR' if success else 'BLOCKED'} ({travelled:.1f}m) — returning")
            self.attempt_cell(final, origin_pos[0], origin_pos[1], duration=cfg["explore_leg_secs"])
            return travelled

        origin_dist = self.r.read_target_distance()
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
                    # first reading (arrow was N after the align); the legs add more
                    self.estimator.add(origin_pos, origin_dist)
                    self.estimator.add(seed, seed_dist, "N", fwd)
                    est = self.estimator.solve()
                    if est is not None:
                        self.target_wx, self.target_wz = est
                        print(f"   First target guess: ({est[0]:.0f},{est[1]:.0f}) dist={seed_dist}m "
                              f"(±{self.estimator.sigma_m:.0f}m)")
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

        # Only hand D* a goal if the legs pinned the target down; otherwise
        # follow the arrow and keep collecting readings.
        p = self.r.read_position()
        d = self.r.read_target_distance()
        if p is not None and d is not None:
            self.observe_target(p, d, None)

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
        self._last_arrow = arrow
        now = time.time()
        if self._last_tick_t is not None and 0 < now - self._last_tick_t < 10:
            self._tick_dt = 0.8 * self._tick_dt + 0.2 * (now - self._last_tick_t)
        self._last_tick_t = now
        if pos is not None and (not self._track or math.hypot(
                pos[0] - self._track[-1][0], pos[1] - self._track[-1][1]) >= 2.0):
            self._track.append(pos)
        if self.state == self.MOVING:
            self.update_heading(pos, during_clean_movement=True)
            self._check_heading_against_arrow(pos, arrow)

        self.mark_clear_interpolated(pos, speed_pct)

        if self.r.planner_active and pos is not None:
            self.r.planner.update_start(pos[0], pos[1])

        self.observe_target(pos, dist, arrow)
        self._advance_carrot(pos)

        if self.state == self.MOVING:
            if speed_pct < self.cfg["stop_speed_ratio"]:
                self._stop_ticks += 1
                if self._stop_ticks >= self.cfg["stop_debounce"]:
                    self._stop_ticks = 0
                    return self._enter_recal(pos)
            else:
                self._stop_ticks = 0
            if self._grinding(pos):
                print("\n[NAV] Moving but getting nowhere (grinding along a wall?)")
                self._mark_grind_wall(self._grind_track)
                return self._enter_recal(pos)

            # After an unstick, keep going the new way for a while before D*
            # gets the wheel back. FIX (in-game, Theed): D* turned straight back
            # into the same wall after every 2 s unstick, 31 times in a row.
            esc = self._escape
            if esc is not None and pos is not None and esc["why"] == "follow":
                if self._follow_step(pos, esc):
                    self.steer_by, self.last_waypoint = "follow", esc["target"]
                    return self.steer_toward(pos, *esc["target"])
                esc = self._escape = None
            if esc is not None and pos is not None:
                if time.time() < esc["until"] and \
                        math.hypot(esc["target"][0] - pos[0], esc["target"][1] - pos[1]) > 8.0:
                    self.steer_by, self.last_waypoint = esc["why"], esc["target"]
                    return self.steer_toward(pos, *esc["target"])
                self._escape = None
                self._ev("escape_done", pos=pos, why=esc["why"])

            if self.r.planner_active and self.r.planner.has_path():
                wp = self.lookahead_waypoint()
                if wp is not None:
                    self.steer_by, self.last_waypoint = "D*", wp
                    return self.steer_toward(pos, wp[0], wp[1])
            self.steer_by, self.last_waypoint = "arrow", None

            # No confident estimate yet: follow the arrow. It's drawn relative to
            # the screen, so E = target on your right = press RIGHT, whatever the
            # world axes do. FIX: S/SE/SW used to return 0 = drive straight away.
            self.steer_angle_deg = float(ARROW_DEG.get(arrow, 0))
            if self.steer_angle_deg > 180:
                self.steer_angle_deg = 360 - self.steer_angle_deg
            if arrow in ("NE", "E", "SE", "S"):
                return 1
            if arrow in ("NW", "W", "SW"):
                return -1
            return 0

        if self.state == self.RECALIBRATING:
            return "RECAL"
        return 0

    _last_arrow = None
    steer_by = "-"
    last_waypoint = None

    def _ev(self, kind, **fields):
        rec = getattr(self.r, "rec", None)
        if rec is not None:
            rec.event(kind, **fields)
    _heading_warned = 0.0

    def _check_heading_against_arrow(self, pos, arrow):
        """
        The arrow says where the target is relative to the way the bot faces.
        With a good target estimate that gives the facing, to within one sector.
        If the heading from position deltas disagrees by more than ~70°, it's
        wrong (lag, sliding, a reversed move): use the arrow's instead.
        FIX: the heading was sometimes 90-180° out and D* steered off with it.
        """
        if (pos is None or self.heading is None or arrow not in ARROW_DEG
                or not self.estimator.confident(self._confident_sigma(self._est_dist_at_last or 0))
                or self.target_wx is None):
            return
        bearing = math.atan2(self.target_wz - pos[1], self.target_wx - pos[0])
        if math.hypot(self.target_wx - pos[0], self.target_wz - pos[1]) < 30:
            return
        # arrow = clockwise-from-facing when right_turn_sign is -1
        sign = int(self.cfg.get("right_turn_sign", -1))
        facing = bearing - sign * math.radians(ARROW_DEG[arrow])
        cur = math.atan2(self.heading[1], self.heading[0])
        err = (cur - facing + math.pi) % (2 * math.pi) - math.pi
        # The arrow pins the facing to its 45° sector (±22.5°) plus slack
        if abs(err) > math.radians(float(self.cfg.get("arrow_heading_tolerance_deg", 35.0))):
            # (Also normal while sliding along a wall: moving sideways, facing the wall.)
            now = time.time()
            if now - self._heading_warned > 10:
                print(f"\n[NAV] Heading disagrees with the arrow by {abs(math.degrees(err)):.0f}° — using the arrow")
                self._heading_warned = now
            self._ev("heading_override", pos=pos, arrow=arrow, was=self.heading,
                     now=(math.cos(facing), math.sin(facing)), err_deg=abs(math.degrees(err)))
            self._seed_heading((math.cos(facing), math.sin(facing)))

    def _grinding(self, pos, now=None):
        """
        True if the bot has covered less than grind_min_m in the last
        grind_window_s although it isn't 'stopped'. FIX: pressed against a wall
        at an angle the game slides the bot along it, so the speed never drops
        below stop_speed_ratio and the bot ground back and forth for minutes.
        """
        if pos is None:
            return False
        now = time.time() if now is None else now
        win = float(self.cfg.get("grind_window_s", 6.0))
        hist = self._pos_hist
        hist.append((now, pos))
        while hist and now - hist[0][0] > win:
            hist.popleft()
        if now - hist[0][0] < 0.8 * win:
            return False
        span = max(math.hypot(p[0] - hist[0][1][0], p[1] - hist[0][1][1]) for _, p in hist)
        if span < float(self.cfg.get("grind_min_m", 8.0)):
            self._grind_track = [p for _, p in hist]
            hist.clear()
            return True
        return False

    def _mark_grind_wall(self, track):
        """
        While grinding the bot slid along the obstacle, so the cells just beside
        that track, on the side it was trying to go, are the obstacle. Marks
        them all at once (on foot) instead of learning one cell per contact.
        """
        if self.mounted or not track or not self.r.planner_active:
            return
        (x0, z0), (x1, z1) = track[0], track[-1]
        lx, lz = x1 - x0, z1 - z0
        ln = math.hypot(lx, lz)
        wp = self.lookahead_waypoint()
        if ln < 1.0 or wp is None:
            return
        nx, nz = -lz / ln, lx / ln                      # perpendicular to the slide
        if nx * (wp[0] - x1) + nz * (wp[1] - z1) < 0:   # towards where it wanted to go
            nx, nz = -nx, -nz
        seen = set()
        for x, z in track:
            c = self._cell_of(x + nx * 1.0, z + nz * 1.0)
            if c not in seen and c != self._cell_of(x, z):
                seen.add(c)
                self.mark(x + nx * 1.0, z + nz * 1.0, BLOCKED)
        if seen:
            print(f"[NAV] Slid along an obstacle: {len(seen)} cells marked blocked")

    def _enter_recal(self, pos):
        print("\n[NAV] Stopped — entering RECALIBRATING")
        self._ev("stuck", pos=pos, heading=self.heading, arrow=self._last_arrow, mounted=self.mounted)
        self.state = self.RECALIBRATING
        # Stuck before D* had a goal: give it the best estimate we have, so the
        # recalibration and D* can plan round the obstacle towards it.
        if not self.r.planner_active and pos is not None and self.target_wx is not None:
            self._set_target(self.target_wx, self.target_wz, self._est_dist_at_last, pos)

        if pos is not None and self.heading is not None:
            # FIX: the blocked cell is the one just in front of the bot, not one
            # a whole cell (5 m) ahead: pressed against a wall the bot is often
            # already reported inside the wall's cell, and marking the cell
            # beyond it left D* routing through the real obstacle forever.
            # The first point along the heading that's in a different cell: the
            # cell it's standing in is walkable, and marking it blocked just got
            # it re-cleared as the bot moved off (and the wall forgotten).
            # Mark where D* was sending it (that's what was blocked, and marking it
            # forces a new plan). The heading is a worse guess: it's noisy, and it
            # follows the slide along the wall rather than the push into it.
            push = self._push_dir(pos) or self.heading
            self._mark_beside(pos[0], pos[1], push[0], push[1])

        # FIX: guard pos=None (the original crashed here if coords weren't read)
        if pos is not None:
            # FIX (simulator, dead end on a swoop): "same place" used to mean "near
            # the LAST stuck point", so sliding corner to corner (A, B, A, B...)
            # reset the count every time and it never dismounted. Count every
            # stuck point within stuck_radius_m in the last few minutes.
            now = time.time()
            win = float(self.cfg.get("stuck_window_s", 300.0))
            rad = float(self.cfg["stuck_radius_m"])
            self._stuck_hist = deque(((t, p, h) for t, p, h in self._stuck_hist if now - t <= win),
                                     maxlen=200)
            self._stuck_hist.append((now, pos, self.heading))
            near = [(p, h) for _, p, h in self._stuck_hist
                    if math.hypot(p[0] - pos[0], p[1] - pos[1]) <= rad]
            self._recal_cycles = len(near) - 1
            self._recal_origin = pos
            self._pushes = [(p, h) for p, h in near if h is not None]

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
        Get unstuck: back off, turn, drive on. Returns True (resume MOVING).

        FIX (second in-game drive): this used to probe the 8 neighbouring 5 m
        cells one by one. With ~2 s between position reads the bot covers 10-30 m
        per read, so probes overshot by 15-25 m or, when a probe was too short to
        get moving, "moved 0.0m" and marked a clear cell BLOCKED. Free cells the
        bot had just walked through were marked as walls all round it, D* routed
        round the false walls, and the bot went the wrong way. A fixed manoeuvre
        doesn't need fast position reads, and it marks nothing it didn't hit.
        """
        c, keys = self.cfg, self.r.keys
        n = self._recal_cycles                       # times stuck here before
        if n + 1 >= int(c.get("backtrack_after_stuck", 4)) and self._track:
            return self._backtrack(pos)
        side = self._unstick_side(pos, n)
        if c.get("wall_follow", True) and self.heading is not None and pos is not None:
            return self._start_follow(pos, side, n)
        angle = min(45.0 * (1 + n // 2), 135.0)      # widen the turn if it keeps happening
        turn_rate = float(getattr(self.r, "turn_rate_deg_s", None) or c.get("turn_rate_deg_s", 100.0))
        back_s = float(c.get("unstick_back_secs", 1.0))
        go_s = float(c.get("unstick_forward_secs", 2.0))
        print(f"\n[UNSTICK] back {back_s:.1f}s, turn {'RIGHT' if side > 0 else 'LEFT'} "
              f"{angle:.0f}°, forward {go_s:.1f}s (stuck {n + 1}x here)")
        self._ev("unstick", pos=pos, side="RIGHT" if side > 0 else "LEFT", angle=angle,
                 times_here=n + 1, heading=self.heading)

        keys.stop_movement()
        keys.key_up("FORWARD")
        keys.key_down("BACK"); time.sleep(back_s); keys.key_up("BACK")
        self._prev_pos = None
        keys.tap("RIGHT" if side > 0 else "LEFT", angle / turn_rate)
        if self.heading is not None:
            # dead-reckon the new facing: RIGHT turns by right_turn_sign in X/Z
            a = math.radians(angle) * side * int(c.get("right_turn_sign", -1))
            hx, hz = self.heading
            self._seed_heading((hx * math.cos(a) - hz * math.sin(a), hx * math.sin(a) + hz * math.cos(a)))
        start = self.r.read_position()
        keys.key_down("FORWARD"); time.sleep(go_s); keys.key_up("FORWARD")
        end = self.r.read_position()
        if start is not None and end is not None:
            dx, dz = end[0] - start[0], end[1] - start[1]
            d = math.hypot(dx, dz)
            if d >= 3.0:
                self._seed_heading((dx / d, dz / d))     # measured, better than reckoned
                self.mark(end[0], end[1], CLEAR)
            fd = self.r.read_target_distance()
            if fd is not None:
                self.observe_target(end, fd, None)
        if end is not None and self.heading is not None:
            secs = min(2.0 + 2.0 * n, 10.0)
            self._escape = {"until": time.time() + secs, "why": "escape",
                            "target": (end[0] + self.heading[0] * 60, end[1] + self.heading[1] * 60)}
        self.state = self.MOVING
        self._pos_hist = deque()
        self._stop_ticks = 0
        self._prev_pos = end
        if self.r.planner_active and end is not None:
            self.r.planner.update_start(end[0], end[1])
        return True

    @staticmethod
    def _rot(v, deg):
        a = math.radians(deg)
        return (v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a))

    def _start_follow(self, pos, side, n):
        """
        Follow the wall: back off a little, then drive along it angled ~30° into
        it. SWG slides a character along a wall it pushes into at an angle, so
        this runs along the wall until it ends, then D* takes over again.
        side +1 = follow to the right of the way it was facing, -1 = left.
        (A building is passed in one go instead of one 5 m cell per bump.)
        """
        c, keys = self.cfg, self.r.keys
        into = self._push_dir(pos) or self.heading            # the way it was trying to go
        sign = int(c.get("right_turn_sign", -1))
        # "right" in screen terms is a rotation by sign*90 in X/Z
        along = self._rot(into, side * sign * 90.0)
        desired = self._rot(into, side * sign * float(c.get("follow_angle_deg", 60.0)))
        turn_rate = float(getattr(self.r, "turn_rate_deg_s", None) or c.get("turn_rate_deg_s", 100.0))
        print(f"\n[FOLLOW] Following the wall to the {'RIGHT' if side > 0 else 'LEFT'} "
              f"(stuck {n + 1}x here)")
        self._ev("follow", pos=pos, side="RIGHT" if side > 0 else "LEFT", into=into, times_here=n + 1)
        keys.stop_movement()
        keys.key_up("FORWARD")
        keys.key_down("BACK"); time.sleep(float(c.get("unstick_back_secs", 1.0)) / 2); keys.key_up("BACK")
        keys.tap("RIGHT" if side > 0 else "LEFT", float(c.get("follow_angle_deg", 60.0)) / turn_rate)
        self._seed_heading(desired)
        # FIX (in-game): the heading base was the position from BEFORE reversing,
        # so the next heading came out pointing backwards (155-176° wrong).
        here = self.r.read_position() or pos
        self._escape = {"why": "follow", "until": time.time() + float(c.get("follow_secs", 30.0)),
                        "desired": desired, "along": along, "free": 0, "start": here, "into": into,
                        "target": (here[0] + desired[0] * 60, here[1] + desired[1] * 60), "last": here}
        self.state = self.MOVING
        self._pos_hist = deque()
        self._stop_ticks = 0
        self._prev_pos = here
        return True

    def _follow_step(self, pos, esc):
        """Keep following? Updates the steering target. Ends when the wall does, or on timeout."""
        if time.time() > esc["until"]:
            self._ev("follow_done", pos=pos, why="timeout")
            return False
        lx, lz = esc["last"]
        dx, dz = pos[0] - lx, pos[1] - lz
        d = math.hypot(dx, dz)
        if d >= 3.0:
            esc["last"] = pos
            mx, mz = dx / d, dz / d
            # Sliding along the wall = moving across the "into" direction.
            # Moving with a clear component into it = the wall has ended.
            ix, iz = esc["into"]
            dot = mx * ix + mz * iz
            if dot > 0.35:
                esc["free"] += 1
            else:
                esc["free"] = 0
                # Sliding: the wall is on the "into" side of the track. Map it
                # (on foot), so D* doesn't send the bot back along it later.
                wx, wz = ix - dot * mx, iz - dot * mz
                wn = math.hypot(wx, wz)
                if wn > 0.2 and not self.mounted:
                    for px, pz in ((lx, lz), (pos[0], pos[1])):
                        self._mark_beside(px, pz, wx / wn, wz / wn)
            gone = math.hypot(pos[0] - esc["start"][0], pos[1] - esc["start"][1])
            if esc["free"] >= 2 and gone >= 10.0:
                print("\n[FOLLOW] Past the wall — back to D*")
                self._ev("follow_done", pos=pos, why="past the wall")
                return False
        esc["target"] = (pos[0] + esc["desired"][0] * 60, pos[1] + esc["desired"][1] * 60)
        return True

    def _push_dir(self, pos):
        """Unit vector to where D* was steering, or None."""
        if pos is None or self.last_waypoint is None or self.steer_by not in ("D*",):
            return None
        wx, wz = self.last_waypoint[0] - pos[0], self.last_waypoint[1] - pos[1]
        wn = math.hypot(wx, wz)
        return (wx / wn, wz / wn) if wn > 1.0 else None

    def _mark_beside(self, px, pz, nx, nz):
        """
        Mark the cell on the (nx, nz) side of a bot at (px, pz) blocked.
        Normally that's the next cell over; but if the bot is reported right at
        the edge of its own cell with that cell's body on the wall side, it's
        pressed against a wall INSIDE its own cell (whole-metre coordinates,
        walls not on cell lines), so its own cell is the blocked one.
        """
        own = self._cell_of(px, pz)
        for k in (1.0, 2.0, 3.0, 4.0, 5.0):
            bx, bz = px + nx * k, pz + nz * k
            if self._cell_of(bx, bz) != own:
                break
        if k >= 3.0 and self._near_cell_edge(px, pz):
            bx, bz = px + nx * 1.0, pz + nz * 1.0     # most of its own cell is wall-ward
        self.mark(bx, bz, BLOCKED)

    def _backtrack(self, pos):
        """
        Stuck here again and again: it's a pocket. Go back the way it came, far
        enough to be out of it, and mark where it kept pushing (on foot) so D*
        plans another way in.
        """
        c, keys = self.cfg, self.r.keys
        self._backtracks += 1
        origin = self._recal_origin or pos
        need = float(c.get("backtrack_m", 40.0)) * self._backtracks
        target = None
        for p in reversed(self._track):
            if math.hypot(p[0] - origin[0], p[1] - origin[1]) >= need:
                target = p
                break
        if target is None:
            target = self._track[0]
        marked = 0
        if not self.mounted:
            for (px, pz), (hx, hz) in self._pushes:
                for k in (1.0, 3.0, 5.0):
                    if self.mark(px + hx * k, pz + hz * k, BLOCKED):
                        marked += 1
        print(f"\n[BACKTRACK] Stuck {self._recal_cycles + 1}x here: going back to "
              f"({target[0]:.0f},{target[1]:.0f}), {need:.0f} m back along its track"
              + (f"; {marked} pushed-into cells marked blocked" if marked else ""))
        self._ev("backtrack", pos=pos, target=target, times=self._backtracks, marked=marked)
        keys.stop_movement()
        keys.key_up("FORWARD")
        keys.key_down("BACK"); time.sleep(float(c.get("unstick_back_secs", 1.0))); keys.key_up("BACK")
        self._escape = {"until": time.time() + float(c.get("backtrack_secs", 25.0)),
                        "target": target, "why": "backtrack"}
        self._recal_cycles = 0
        self._recal_origin = None
        self._pushes = []
        # forget this pocket's stuck points: the next backtrack should only come
        # after a fresh run of failures (and goes further: self._backtracks)
        self._stuck_hist = deque(((t, p, h) for t, p, h in self._stuck_hist
                                  if math.hypot(p[0] - origin[0], p[1] - origin[1]) > 2 * float(
                                      c["stuck_radius_m"])), maxlen=200)
        self.state = self.MOVING
        self._pos_hist = deque()
        self._stop_ticks = 0
        self._prev_pos = self.r.read_position() or pos   # after reversing, not before
        return True

    def _unstick_side(self, pos, n):
        """+1 = turn right, -1 = left: towards the D* path, else the arrow, else alternate."""
        if self.r.planner_active and self.heading is not None and pos is not None:
            wp = self.lookahead_waypoint()
            if wp is not None and math.hypot(wp[0] - pos[0], wp[1] - pos[1]) > 1.0:
                side = 1 if self._turn_cross(wp[0] - pos[0], wp[1] - pos[1]) >= 0 else -1
                return side if n % 2 == 0 else -side   # tried that side last time: other way
        a = self._last_arrow
        if a in ("NE", "E", "SE", "S"):
            side = 1
        elif a in ("NW", "W", "SW"):
            side = -1
        else:
            side = 1
        return side if n % 2 == 0 else -side
