import cv2
import easyocr
import numpy as np
import re
import time
import mss
import keyboard
import sys
import os
import json
import math
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from ultralytics import YOLO
from nav_database import NavDatabase, CLEAR as NAV_CLEAR, BLOCKED as NAV_BLOCKED
from dstar_lite import DStarLite

# =============================================================================
# NAVIGATION BRAIN v0.20 — D* First Architecture
#
# Replaces the hug/frustration system with D* Lite as primary navigator.
#
# Three states:
#   MOVING       — Within ±5m corridor of predicted path, full speed.
#                  CLEAR cells interpolated along travelled path at 2Hz.
#                  Heading vector updated from consecutive positions.
#   RECALIBRATING— Corridor breached or stopped. Bot attempts adjacent cells
#                  in D* score order. 3s per attempt after alignment.
#                  Failed attempt → mark BLOCKED, try next.
#                  All fail → EXPLORING.
#   EXPLORING    — No D* path. Short sweep left/right to discover new cells.
#                  Feed back to D*, resume MOVING.
#
# Target estimation:
#   Two position readings 10m apart while arrow=N → heading vector →
#   project target = P2 + normalise(P2-P1) * D2.
#   Refreshes when dist drifts >20m from last estimate.
#
# Panel arrow = ground truth for "heading toward target" (arrow=N).
# Heading vector = actual world-space travel direction (from position delta).
# =============================================================================

MODELS = {
    "group":    r"E:\SWGEMU_bot\jedai\models\wp_group_v0101\weights\best.pt",
    "panel":    r"E:\SWGEMU_bot\jedai\models\wp_panel_v2_production3\weights\best.pt",
    "comp":     r"E:\SWGEMU_bot\jedai\models\wp_components_v3\weights\best.pt",
    "arrow":    r"E:\SWGEMU_bot\jedai\models\wp_direction_v2_final\weights\best.pt",
    "wp_digit": r"E:\SWGEMU_bot\jedai\models\wp_distance\weights\best.pt",
    "minimap":  r"E:\SWGEMU_bot\jedai\models\mini_map_v01.00\weights\best.pt",
    "coords":   r"E:\SWGEMU_bot\jedai\models\player_coord_v1.01\weights\best.pt",
    "co_digit": r"E:\SWGEMU_bot\jedai\models\white_on_teal_digits_v01.00\weights\best.pt",
}

SPEED_JSON    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed_config.json")
NAV_DB_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jedai_nav.db")
PLANET        = "naboo"

MONITOR_INDEX = 2
NAV_FREQ      = 0.05
AUTORUN_KEY   = 'AUTORUN'
KILL_KEY      = 'f8'
LEFT_KEY      = 'LEFT'
RIGHT_KEY     = 'RIGHT'
FORWARD_KEY   = 'FORWARD'
BACK_KEY      = 'BACK'
NAV_STATE_FILE = "nav_state.json"

# Arrival
STOP_DIST           = 50
VERIFY_READINGS     = 5
FINAL_CREEP_DIST    = 20
CREEP_PULSE_ON      = 0.8
CREEP_PULSE_OFF     = 0.15

# Vision
FUZZY_THRESHOLD     = 0.5
OCR_STALE_LIMIT     = 15.0
CLOSE_RANGE_THRESHOLD = 25
SCAN_STEPS          = 4
SCAN_TURN_TIME      = 0.9
SCAN_SETTLE         = 0.4
TAB_IN_PAUSE        = 4
DEBUG_DIR           = r"E:\label_work\debug_logs"

# Speed
LOW_RATIO           = 0.30   # below this = effectively stopped
SOUTH_DEBOUNCE      = 3      # consecutive S reads before hard-correct

# Navigation
CORRIDOR_WIDTH      = 5.0    # metres lateral deviation before recalibration
CLEAR_HZ            = 2.0    # how often to stamp CLEAR cells (seconds)
TARGET_EST_TRAVEL   = 10.0   # metres of arrow=N travel before estimating target
TARGET_EST_FIRST    = 3.0    # metres needed for FIRST estimate (lower threshold)
TARGET_DRIFT_LIMIT  = 20.0   # metres drift in dist before refreshing target estimate
RECAL_ATTEMPT_SECS  = 3.0    # seconds of movement before declaring cell blocked
EXPLORE_TURN_SECS   = 1.5    # seconds per sweep direction
EXPLORE_HOLD_SECS   = 2.0    # seconds to measure distance per sweep leg
EXPLORE_LEG_M       = 20.0   # metres per cross exploration leg
EXPLORE_LEG_SECS    = 4.0    # seconds allotted per leg attempt
CELL_SIZE           = 5.0    # metres per D* cell (matches NavDatabase RES_NORMAL)

# =============================================================================
def fuzzy_match(target, detected, threshold=FUZZY_THRESHOLD):
    if not target or not detected:
        return False
    t, d = target.lower().strip(), detected.lower().strip()
    if t in d or d in t:
        return True
    return SequenceMatcher(None, t, d).ratio() >= threshold

def check_kill():
    return keyboard.is_pressed(KILL_KEY)

# =============================================================================
class VelocityTracker:
    def __init__(self, history=5):
        self._history = deque(maxlen=history)
        self._last = None
        self._last_t = None

    def reset(self):
        self._history.clear()
        self._last = None
        self._last_t = None

    def update(self, x, z):
        now = time.time()
        if self._last is not None and self._last_t is not None:
            dt = now - self._last_t
            if dt > 0:
                dx = x - self._last[0]
                dz = z - self._last[1]
                speed = math.hypot(dx, dz) / dt
                self._history.append(speed)
        self._last = (x, z)
        self._last_t = now

    def speed(self):
        if not self._history:
            return 0.0
        return sum(self._history) / len(self._history)


# =============================================================================
class Navigator:
    """
    D*-first navigator.

    States:
      MOVING        — D* steers. No panel interference. Speed < 10% → RECALIBRATING.
      RECALIBRATING — Attempt adjacent cells in D* score order. Mark clear/blocked.
                      Success → resume MOVING (no realign). All fail → EXPLORING.
      EXPLORING     — Not yet used mid-journey; cross-map exploration runs at startup.

    Panel arrow used ONLY for:
      - Initial align to N
      - Target estimation (arrow=N + D* routing straight + 3m travel)
      - Never for steering or realigning during MOVING

    D* steers everything. If D* routes backward temporarily, that's correct.
    """

    _MOVING        = 'MOVING'
    _RECALIBRATING = 'RECALIBRATING'
    _EXPLORING     = 'EXPLORING'

    # How many consecutive <10% speed ticks before entering RECALIBRATING
    _STOP_DEBOUNCE = 3

    def __init__(self, nav):
        self.nav   = nav
        self.state = self._MOVING

        # Heading vector — updated from position deltas during clean movement only
        self.heading         = None
        self._prev_pos       = None
        self._heading_buffer = deque(maxlen=5)

        # Target world coordinates (estimated from travel, then locked)
        self.target_wx          = None
        self.target_wz          = None
        self._est_locked        = False   # True once first valid estimate confirmed
        self._est_p1            = None    # position when accumulation started
        self._est_travel        = 0.0    # metres since _est_p1
        self._est_dist_at_last  = None

        # CLEAR interpolation
        self._last_mark_pos        = None
        self._last_mark_time       = 0.0
        self._min_speed_since_mark = 999.0

        # Stop debounce
        self._stop_ticks    = 0

        # RECALIBRATING
        self._recal_candidates = []
        self._recal_visited    = set()
        self._recal_origin     = None
        self._recal_cycles     = 0

    # ------------------------------------------------------------------
    # Heading
    # ------------------------------------------------------------------

    def _update_heading(self, pos, during_clean_movement=True):
        """
        Update heading vector from consecutive positions (>1m apart).
        Only updates during clean forward movement — not during recal/explore.
        """
        if pos is None:
            return
        if self._prev_pos is not None:
            dx = pos[0] - self._prev_pos[0]
            dz = pos[1] - self._prev_pos[1]
            d  = math.hypot(dx, dz)
            if d >= 1.0 and during_clean_movement:
                h = (dx/d, dz/d)
                self._heading_buffer.append(h)
                self.heading = h
                self._prev_pos = pos
        else:
            self._prev_pos = pos

    def _heading_stable(self):
        """True if last 3+ heading samples agree within 15°."""
        if len(self._heading_buffer) < 3:
            return False
        ref = list(self._heading_buffer)[-1]
        for h in list(self._heading_buffer)[:-1]:
            dot = max(-1.0, min(1.0, ref[0]*h[0] + ref[1]*h[1]))
            if math.degrees(math.acos(dot)) > 15:
                return False
        return True

    def reset_estimator(self):
        """Call after confirmed align to N — clears heading + estimation state."""
        self._prev_pos       = None
        self._heading_buffer = deque(maxlen=5)
        self.heading         = None
        self._est_p1         = None
        self._est_travel     = 0.0
        # Do NOT clear target_wx/wz or _est_locked — keep the estimate we have

    # ------------------------------------------------------------------
    # Target estimation
    # ------------------------------------------------------------------

    def _try_estimate_target(self, pos, dist, arrow, dstar_straight):
        """
        Update target estimate ONLY when:
          1. arrow == 'N' (target is ahead)
          2. D* is routing straight toward target (dstar_straight=True)
          3. Heading is stable
          4. Bot has travelled >= 3m since last estimate point
          5. Estimate not yet locked OR dist has drifted >50m

        Once locked, only re-estimates on large drift.
        """
        if pos is None or dist is None:
            return
        if arrow != 'N' or not dstar_straight or not self._heading_stable():
            # Conditions not met — reset accumulator but keep existing estimate
            self._est_p1     = None
            self._est_travel = 0.0
            return

        # Check if refresh needed
        if self._est_locked:
            if (self._est_dist_at_last is None or
                    abs(dist - self._est_dist_at_last) < 50):
                return  # estimate is good, don't touch it

        # Accumulate travel
        if self._est_p1 is None:
            self._est_p1     = pos
            self._est_travel = 0.0
            return

        self._est_travel = math.hypot(pos[0]-self._est_p1[0],
                                       pos[1]-self._est_p1[1])

        threshold = 3.0 if not self._est_locked else 10.0
        if self._est_travel < threshold:
            return

        # Project target
        tx = pos[0] + self.heading[0] * dist
        tz = pos[1] + self.heading[1] * dist

        # Sanity: target must be in forward hemisphere
        to_t   = (tx - pos[0], tz - pos[1])
        to_mag = math.hypot(*to_t)
        if to_mag > 0:
            dot = self.heading[0]*(to_t[0]/to_mag) + self.heading[1]*(to_t[1]/to_mag)
            if dot <= 0:
                print(f"\n[EST] Rejected — target behind heading")
                self._est_p1     = pos
                self._est_travel = 0.0
                return

        old = (self.target_wx, self.target_wz)
        self.target_wx         = tx
        self.target_wz         = tz
        self._est_dist_at_last = dist
        self._est_locked       = True
        self._est_p1           = None
        self._est_travel       = 0.0

        # Update D* goal
        if self.nav._planner_active:
            self.nav.planner.set_goal(tx, tz)
            print(f"\n[EST] Target: ({old[0]:.0f if old[0] else '?'},"
                  f"{old[1]:.0f if old[1] else '?'}) → ({tx:.0f},{tz:.0f}) "
                  f"dist={dist}m")
        else:
            self.nav.planner.set_goal(tx, tz)
            self.nav.planner.set_start(pos[0], pos[1])
            try:
                self.nav.planner.initialise()
                self.nav._planner_active = True
                print(f"\n[D*] Active — target ({tx:.0f},{tz:.0f}) dist={dist}m")
            except Exception as e:
                print(f"\n[D*] Init failed: {e}")

    # ------------------------------------------------------------------
    # CLEAR interpolation
    # ------------------------------------------------------------------

    def _mark_clear_interpolated(self, pos, speed_pct):
        now = time.time()
        self._min_speed_since_mark = min(self._min_speed_since_mark, speed_pct)
        if now - self._last_mark_time < (1.0 / CLEAR_HZ):
            return
        if pos is None:
            return
        if self._last_mark_pos is not None:
            d = math.hypot(pos[0]-self._last_mark_pos[0],
                           pos[1]-self._last_mark_pos[1])
            if d >= CELL_SIZE:
                if self._min_speed_since_mark >= 0.10:
                    steps = max(1, int(d / CELL_SIZE))
                    for i in range(steps + 1):
                        t  = i / steps
                        ix = self._last_mark_pos[0] + t*(pos[0]-self._last_mark_pos[0])
                        iz = self._last_mark_pos[1] + t*(pos[1]-self._last_mark_pos[1])
                        self.nav.nav_db.mark_clear(PLANET, ix, iz)
                else:
                    self.nav.nav_db.mark_clear(PLANET, pos[0], pos[1])
                self._last_mark_pos        = pos
                self._last_mark_time       = now
                self._min_speed_since_mark = 999.0
        else:
            self._last_mark_pos  = pos
            self._last_mark_time = now
        if self.nav._planner_active:
            self.nav.planner.update_start(pos[0], pos[1])

    # ------------------------------------------------------------------
    # D* direction check
    # ------------------------------------------------------------------

    def _dstar_is_straight(self, pos):
        """
        Returns True if D*'s next waypoint is roughly in the same direction
        as the current heading (within 20°). Indicates D* is routing direct,
        not around an obstacle.
        """
        if not self.nav._planner_active or pos is None or self.heading is None:
            return False
        wp = self.nav.planner.next_waypoint()
        if wp is None:
            return False
        dx  = wp[0] - pos[0]
        dz  = wp[1] - pos[1]
        mag = math.hypot(dx, dz)
        if mag < 0.1:
            return True
        dot = self.heading[0]*(dx/mag) + self.heading[1]*(dz/mag)
        return dot > 0.94  # within ~20°

    # ------------------------------------------------------------------
    # Steering
    # ------------------------------------------------------------------

    def _steer_toward_waypoint(self, pos, wp_wx, wp_wz):
        """
        Left/right/straight relative to current heading toward D* waypoint.
        Returns -1, 0, +1.
        """
        if pos is None or self.heading is None:
            return 0
        dx  = wp_wx - pos[0]
        dz  = wp_wz - pos[1]
        mag = math.hypot(dx, dz)
        if mag < 1.0:
            return 0
        cross = self.heading[0]*dz - self.heading[1]*dx
        if abs(cross) < 0.15:
            return 0
        return 1 if cross > 0 else -1

    # ------------------------------------------------------------------
    # Recalibration helpers
    # ------------------------------------------------------------------

    def _get_recal_candidates(self, pos):
        """Adjacent cells sorted by D* score. Falls back to target-distance if
        planner not active."""
        if pos is None:
            return []
        cx = int(math.floor(pos[0] / CELL_SIZE))
        cz = int(math.floor(pos[1] / CELL_SIZE))

        candidates = []
        for dcx in (-1, 0, 1):
            for dcz in (-1, 0, 1):
                if dcx == 0 and dcz == 0:
                    continue
                nb   = (cx+dcx, cz+dcz)
                wx   = nb[0]*CELL_SIZE + CELL_SIZE/2
                wz   = nb[1]*CELL_SIZE + CELL_SIZE/2
                cell_key = nb

                # Check DB state
                from nav_database import BLOCKED as NAV_BLK
                state = self.nav.nav_db.get_state(PLANET, wx, wz)
                if state == NAV_BLK:
                    continue

                # Score
                if self.nav._planner_active:
                    score = self.nav.planner._g_val(nb)
                elif self.target_wx is not None:
                    score = math.hypot(wx-self.target_wx, wz-self.target_wz)
                elif self.heading is not None:
                    ddx = wx - pos[0]
                    ddz = wz - pos[1]
                    mag = math.hypot(ddx, ddz)
                    dot = (self.heading[0]*ddx + self.heading[1]*ddz)/mag if mag>0 else 0
                    score = -dot
                else:
                    score = math.hypot(wx-pos[0], wz-pos[1])

                candidates.append((score, wx, wz, nb))

        candidates.sort(key=lambda x: x[0])
        return candidates

    def _attempt_cell(self, pos, wx, wz, duration=RECAL_ATTEMPT_SECS):
        """
        Attempt to move to (wx,wz). Aligns, creeps for `duration` seconds,
        checks progress. Returns (success:bool, final_pos).
        Marks cell CLEAR or BLOCKED in DB.
        """
        if pos is None:
            return False, pos

        dx = wx - pos[0]
        dz = wz - pos[1]
        d  = math.hypot(dx, dz)
        if d < 0.5:
            return True, pos

        ndx, ndz = dx/d, dz/d

        # Stop fully
        self.nav._stop_movement()
        time.sleep(0.2)

        # Align to cell direction using heading vector
        self._align_to_direction(ndx, ndz)

        # Record start
        frame = self.nav.get_screen()
        sx, _, sz = self.nav._update_player_coords(frame)
        if sx is None:
            return False, pos
        start = (sx, sz)

        # Creep
        self.nav._key_down(FORWARD_KEY)
        deadline   = time.time() + duration
        final_pos  = start
        while time.time() < deadline:
            if check_kill():
                self.nav._key_up(FORWARD_KEY)
                return False, final_pos
            frame = self.nav.get_screen()
            cx, _, cz = self.nav._update_player_coords(frame)
            if cx is not None:
                final_pos = (cx, cz)
        self.nav._key_up(FORWARD_KEY)

        progress = math.hypot(final_pos[0]-start[0], final_pos[1]-start[1])

        if progress >= 1.5:
            self.nav.nav_db.mark_clear(PLANET, final_pos[0], final_pos[1])
            if self.nav._planner_active:
                self.nav.planner.update_cell(wx, wz, NAV_CLEAR)
            print(f"[CELL] ({wx:.0f},{wz:.0f}) CLEAR (moved {progress:.1f}m)")

            # Seed heading from this movement
            mv = (final_pos[0]-start[0], final_pos[1]-start[1])
            mv_mag = math.hypot(*mv)
            if mv_mag > 0:
                h = (mv[0]/mv_mag, mv[1]/mv_mag)
                for _ in range(3):
                    self._heading_buffer.append(h)
                self.heading = h
            return True, final_pos
        else:
            self.nav.nav_db.mark_blocked(PLANET, wx, wz)
            if self.nav._planner_active:
                self.nav.planner.update_cell(wx, wz, NAV_BLOCKED)
            print(f"[CELL] ({wx:.0f},{wz:.0f}) BLOCKED (moved {progress:.1f}m)")
            return False, pos

    def _align_to_direction(self, ndx, ndz, timeout=3.0):
        """Rotate until heading matches target direction within 15°."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check_kill():
                return
            if self.heading is not None:
                dot   = max(-1.0, min(1.0,
                    self.heading[0]*ndx + self.heading[1]*ndz))
                angle = math.degrees(math.acos(dot))
                if angle < 15:
                    return
                cross = self.heading[0]*ndz - self.heading[1]*ndx
                self.nav._tap(RIGHT_KEY if cross > 0 else LEFT_KEY, 0.1)
            else:
                # No heading yet — tap right and let it build
                self.nav._tap(RIGHT_KEY, 0.1)
            # Update heading from latest position
            frame = self.nav.get_screen()
            cx, _, cz = self.nav._update_player_coords(frame)
            if cx is not None:
                self._update_heading((cx, cz))
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Cross exploration (called once at startup)
    # ------------------------------------------------------------------

    def run_cross_exploration(self, target_name, origin_pos, origin_dist):
        """
        From origin, attempt 20m in each of 4 directions using _attempt_cell.
        Forward (toward target) and backward first. Left/right only if both
        forward+backward got < 10m.

        Uses heading vector seeded from initial align (first move = toward target).
        Records clear/blocked cells. Fires target estimate from forward leg if valid.

        Returns: estimated target coords or None.
        """
        print(f"\n🔍  Cross exploration from ({origin_pos[0]:.0f},{origin_pos[1]:.0f})...")

        # We need a heading to know which way is "forward" (toward target).
        # At this point we've just aligned to N (arrow=N), so the bot is
        # physically pointed toward the target. We'll derive forward direction
        # from the first successful move.

        # Helper: attempt 20m in a given world direction vector
        def try_leg(label, dx, dz, record_heading=False):
            wx = origin_pos[0] + dx * EXPLORE_LEG_M
            wz = origin_pos[1] + dz * EXPLORE_LEG_M
            print(f"   Leg {label}: attempting ({wx:.0f},{wz:.0f})")
            success, final = self._attempt_cell(origin_pos, wx, wz,
                                                 duration=EXPLORE_LEG_SECS)
            travelled = math.hypot(final[0]-origin_pos[0],
                                   final[1]-origin_pos[1])
            # Return to origin
            print(f"   Leg {label}: {'CLEAR' if success else 'BLOCKED'} "
                  f"({travelled:.1f}m) — returning")
            self._attempt_cell(final, origin_pos[0], origin_pos[1],
                                duration=EXPLORE_LEG_SECS)
            return travelled

        # --- Determine forward direction ---
        # We know arrow=N so target is ahead. We need to physically move
        # a tiny bit to seed the heading vector, then use that for the legs.
        print(f"   Seeding forward direction...")
        seed_wx = origin_pos[0]  # will be filled after first tap
        seed_wz = origin_pos[1]

        # Tap forward ~2s to get initial heading
        self.nav._key_down(FORWARD_KEY)
        time.sleep(2.0)
        self.nav._key_up(FORWARD_KEY)
        frame = self.nav.get_screen()
        cx, _, cz = self.nav._update_player_coords(frame)
        _, seed_dist, _ = self.nav.get_target_status(frame, target_name)

        if cx is not None and cx != origin_pos[0]:
            fdx = cx - origin_pos[0]
            fdz = cz - origin_pos[1]
            fmag = math.hypot(fdx, fdz)
            if fmag > 0.5:
                fwd = (fdx/fmag, fdz/fmag)
                for _ in range(3):
                    self._heading_buffer.append(fwd)
                self.heading = fwd
                self.nav.nav_db.mark_clear(PLANET, cx, cz)
                print(f"   Forward direction: ({fwd[0]:.2f},{fwd[1]:.2f})")

                # Seed target estimate from this first move
                if seed_dist is not None:
                    tx = cx + fwd[0] * seed_dist
                    tz = cz + fwd[1] * seed_dist
                    # Sanity check
                    to_t = (tx-cx, tz-cz)
                    mag  = math.hypot(*to_t)
                    if mag > 0:
                        dot = fwd[0]*(to_t[0]/mag) + fwd[1]*(to_t[1]/mag)
                        if dot > 0:
                            self.target_wx         = tx
                            self.target_wz         = tz
                            self._est_dist_at_last = seed_dist
                            self._est_locked       = True
                            print(f"   Initial target estimate: "
                                  f"({tx:.0f},{tz:.0f}) dist={seed_dist}m")
            # Return to origin from seed pos
            self._attempt_cell((cx, cz), origin_pos[0], origin_pos[1],
                                duration=EXPLORE_LEG_SECS)
        else:
            print(f"   Seed move failed — heading unknown, using origin")

        if self.heading is None:
            print(f"   No heading — skipping cross, will estimate during navigation")
            return self.target_wx, self.target_wz if self.target_wz else None

        fwd = self.heading
        # Perpendicular (right) = rotate fwd 90° CW: (fwd_z, -fwd_x) ... wait:
        # For 2D: right of (dx,dz) = (dz, -dx)
        rgt = (fwd[1], -fwd[0])
        bwd = (-fwd[0], -fwd[1])
        lft = (-fwd[1], fwd[0])

        # Forward leg
        fwd_travel = try_leg('FORWARD', fwd[0], fwd[1])

        # Backward leg
        bwd_travel = try_leg('BACKWARD', bwd[0], bwd[1])

        # Left/right only if both fwd+bwd got <10m (narrow passage)
        if fwd_travel < 10.0 and bwd_travel < 10.0:
            print(f"   Narrow passage detected — trying LEFT/RIGHT")
            try_leg('LEFT',  lft[0], lft[1])
            try_leg('RIGHT', rgt[0], rgt[1])

        # Initialise D* if we have a target estimate
        if self.target_wx is not None and not self.nav._planner_active:
            frame = self.nav.get_screen()
            cx, _, cz = self.nav._update_player_coords(frame)
            if cx is not None:
                self.nav.planner.set_goal(self.target_wx, self.target_wz)
                self.nav.planner.set_start(cx, cz)
                try:
                    self.nav.planner.initialise()
                    self.nav._planner_active = True
                    print(f"[D*] Active post-exploration — "
                          f"target ({self.target_wx:.0f},{self.target_wz:.0f})")
                except Exception as e:
                    print(f"[D*] Init failed: {e}")

        print(f"   Cross exploration complete.")
        return self.target_wx, self.target_wz

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    def tick(self, pos, speed_pct, arrow, dist):
        """
        Called each nav loop iteration.
        Returns: -1 (tap left), 0 (nothing), +1 (tap right),
                 'STOP', 'RECAL' (handle externally)
        """
        # Always update heading during clean movement
        if self.state == self._MOVING:
            self._update_heading(pos, during_clean_movement=True)

        # Always mark clear
        self._mark_clear_interpolated(pos, speed_pct)

        # Update D* start
        if self.nav._planner_active and pos is not None:
            self.nav.planner.update_start(pos[0], pos[1])

        # Check if D* is routing straight (for estimation gating)
        dstar_straight = self._dstar_is_straight(pos)

        # Try to estimate/update target
        self._try_estimate_target(pos, dist, arrow, dstar_straight)

        # ============================================================
        # STATE: MOVING
        # ============================================================
        if self.state == self._MOVING:
            # Stop debounce — only enter RECALIBRATING when genuinely stopped
            if speed_pct < 0.10:
                self._stop_ticks += 1
                if self._stop_ticks >= self._STOP_DEBOUNCE:
                    self._stop_ticks = 0
                    return self._enter_recal(pos)
            else:
                self._stop_ticks = 0

            # D* steering — no panel arrow interference
            if self.nav._planner_active and self.nav.planner.has_path():
                wp = self.nav.planner.next_waypoint()
                if wp is not None:
                    steer = self._steer_toward_waypoint(pos, wp[0], wp[1])
                    return steer

            # No D* path yet — use panel arrow as basic fallback
            if arrow in ('NW', 'W'):
                return -1
            if arrow in ('NE', 'E'):
                return 1
            return 0

        # ============================================================
        # STATE: RECALIBRATING — handled externally via 'RECAL'
        # ============================================================
        if self.state == self._RECALIBRATING:
            return 'RECAL'

        return 0

    def _enter_recal(self, pos):
        """Transition to RECALIBRATING. Mark forward cell blocked. Return 'STOP'."""
        print(f"\n[NAV] Stopped — entering RECALIBRATING")
        self.state = self._RECALIBRATING

        if pos is not None and self.heading is not None:
            bx = pos[0] + self.heading[0] * CELL_SIZE
            bz = pos[1] + self.heading[1] * CELL_SIZE
            self.nav.nav_db.mark_blocked(PLANET, bx, bz)
            if self.nav._planner_active:
                self.nav.planner.update_cell(bx, bz, NAV_BLOCKED)

        # Track cycles
        if (self._recal_origin is None or
                math.hypot(pos[0]-self._recal_origin[0],
                           pos[1]-self._recal_origin[1]) > 15):
            self._recal_cycles  = 0
            self._recal_visited = set()
            self._recal_origin  = pos
        else:
            self._recal_cycles += 1

        self._recal_candidates = [
            c for c in self._get_recal_candidates(pos)
            if c[3] not in self._recal_visited
        ]
        return 'STOP'

    def do_recal(self, pos, target_name):
        """
        Called from nav loop when state==RECALIBRATING.
        Attempts candidates in order. Returns True when one succeeds
        (caller should resume autorun). Returns False if all fail (caller
        should do explore sweep).
        """
        if not self._recal_candidates:
            # Force explore
            self.state = self._MOVING
            return False

        score, wx, wz, node = self._recal_candidates.pop(0)
        self._recal_visited.add(node)
        print(f"\n[RECAL] Attempting ({wx:.0f},{wz:.0f}) score={score:.1f}")

        success, final_pos = self._attempt_cell(pos, wx, wz)

        if success:
            self.state = self._MOVING
            # Update D* start to new pos
            if self.nav._planner_active:
                self.nav.planner.update_start(wx, wz)
            # Seed target estimate if not yet done
            if not self._est_locked and final_pos is not None:
                frame = self.nav.get_screen()
                _, fd, _ = self.nav.get_target_status(
                    frame, getattr(self.nav, '_current_target_name', ''))
                if fd is not None and self.heading is not None:
                    tx = final_pos[0] + self.heading[0] * fd
                    tz = final_pos[1] + self.heading[1] * fd
                    to_t = (tx-final_pos[0], tz-final_pos[1])
                    mag  = math.hypot(*to_t)
                    if mag > 0:
                        dot = self.heading[0]*(to_t[0]/mag) + \
                              self.heading[1]*(to_t[1]/mag)
                        if dot > 0:
                            self.target_wx         = tx
                            self.target_wz         = tz
                            self._est_dist_at_last = fd
                            self._est_locked       = True
                            if not self.nav._planner_active:
                                self.nav.planner.set_goal(tx, tz)
                                self.nav.planner.set_start(
                                    final_pos[0], final_pos[1])
                                try:
                                    self.nav.planner.initialise()
                                    self.nav._planner_active = True
                                except Exception:
                                    pass
                            else:
                                self.nav.planner.set_goal(tx, tz)
                            print(f"[RECAL] Target seeded: "
                                  f"({tx:.0f},{tz:.0f}) dist={fd}m")
            return True

        # Failed — try next on next call
        if not self._recal_candidates:
            # All exhausted
            if self._recal_cycles >= 4:
                print(f"\n[RECAL] {self._recal_cycles} cycles — forcing explore")
                self._recal_cycles = 0
                self._recal_visited.clear()
            self.state = self._MOVING
            return False
        return None  # still going, call again

    def reset(self):
        self.state              = self._MOVING
        self.heading            = None
        self._prev_pos          = None
        self._heading_buffer    = deque(maxlen=5)
        self.target_wx          = None
        self.target_wz          = None
        self._est_locked        = False
        self._est_p1            = None
        self._est_travel        = 0.0
        self._est_dist_at_last  = None
        self._last_mark_pos     = None
        self._last_mark_time    = 0.0
        self._min_speed_since_mark = 999.0
        self._stop_ticks        = 0
        self._recal_candidates  = []
        self._recal_visited     = set()
        self._recal_origin      = None
        self._recal_cycles      = 0

# =============================================================================
class SWGMasterNavigator:

    def __init__(self):
        print("--- Initialising Vision Systems ---")
        self.m_group   = YOLO(MODELS["group"])
        self.m_panel   = YOLO(MODELS["panel"])
        self.m_comp    = YOLO(MODELS["comp"])
        self.m_arrow   = YOLO(MODELS["arrow"])
        self.m_wp_digit= YOLO(MODELS["wp_digit"])
        self.m_minimap = YOLO(MODELS["minimap"])
        self.m_coords  = YOLO(MODELS["coords"])
        self.m_co_digit= YOLO(MODELS["co_digit"])

        try:
            self.ocr = easyocr.Reader(['en'], gpu=True, verbose=False)
        except Exception:
            self.ocr = easyocr.Reader(['en'], gpu=False, verbose=False)

        # Vehicle speeds
        self.vehicle_speeds = {
            "walking":    {"max": 5.2},
            "running":    {"max": 10.5},
            "speederbike":{"max": 14.0},
            "swoop":      {"max": 16.0},
        }
        try:
            with open(SPEED_JSON) as f:
                custom = json.load(f)
            self.vehicle_speeds.update(custom)
        except Exception:
            pass
        print(f"   Loaded speed config for: {list(self.vehicle_speeds.keys())}")

        self.current_vehicle = "swoop"
        self.max_speed       = 16.0
        self.vel_tracker     = VelocityTracker()
        self._apply_vehicle(self.current_vehicle)

        # Spatial DB + D* planner
        self.nav_db         = NavDatabase(NAV_DB_FILE)
        self.planner        = DStarLite(self.nav_db, PLANET)
        self._planner_active = False

        # Navigator (new D*-first controller)
        self.navigator = Navigator(self)

        self.registry           = []
        self.autorun_active     = False
        self.creep_mode         = False
        self.last_valid_dist    = 99999
        self.min_dist_seen      = 99999
        self.target_last_center = None
        self.target_lock_active = False
        self.close_read_count   = 0

        self.player_x = None
        self.player_y = None
        self.player_z = None
        self.coords_time = 0.0

    # =========================================================================
    # VEHICLE
    # =========================================================================
    def _apply_vehicle(self, vehicle_key):
        cfg = self.vehicle_speeds.get(vehicle_key)
        if cfg is None:
            cfg = self.vehicle_speeds.get("swoop", {"max": 16.0})
        self.current_vehicle = vehicle_key
        self.max_speed = cfg["max"]
        self.vel_tracker.reset()
        print(f"   🏍  Vehicle: {vehicle_key} | max: {self.max_speed} m/s")

    # =========================================================================
    # COMMANDS
    # =========================================================================
    def _send(self, payload, wait_for_idle=True):
        try:
            with open(NAV_STATE_FILE, "w") as f:
                json.dump(payload, f)
        except Exception as e:
            print(f"[NAV] Send error: {e}")
            return
        if wait_for_idle:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                try:
                    with open(NAV_STATE_FILE, "r") as f:
                        state = json.load(f)
                    if state.get("cmd") == "IDLE":
                        return
                except:
                    pass
                time.sleep(0.005)

    def _press(self, key, state=None):
        payload = {"cmd": "PRESS", "key": key}
        if state is not None:
            payload["state"] = state
        self._send(payload)

    def _tap(self, key, duration=0.0):
        self._send({"cmd": "TAP", "key": key, "duration": duration})
        time.sleep(duration + 0.05)

    def _key_down(self, key):
        self._send({"cmd": "KEY_DOWN", "key": key}, wait_for_idle=True)

    def _key_up(self, key):
        self._send({"cmd": "KEY_UP", "key": key}, wait_for_idle=True)

    def _release_all(self):
        self._send({"cmd": "RELEASE_ALL"}, wait_for_idle=True)

    def _stop_movement(self):
        if self.autorun_active:
            self._press(AUTORUN_KEY)
            self.autorun_active = False
        self._key_up(FORWARD_KEY)
        self.creep_mode = False

    def _start_autorun(self):
        if not self.autorun_active:
            self._press(AUTORUN_KEY)
            self.autorun_active = True
        self.creep_mode = False

    def _do_creep_pulse(self):
        self._tap(FORWARD_KEY, duration=CREEP_PULSE_ON)
        time.sleep(CREEP_PULSE_OFF)

    def shutdown(self):
        self._release_all()
        print("✓ All keys released")

    # =========================================================================
    # VISION
    # =========================================================================
    def get_screen(self):
        with mss.mss() as sct:
            monitor = sct.monitors[MONITOR_INDEX]
            sct_img = sct.grab(monitor)
            return cv2.cvtColor(np.array(sct_img), cv2.COLOR_BGRA2BGR)

    def _safe_crop(self, img, x1, y1, x2, y2):
        h, w = img.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = img[y1:y2, x1:x2]
        return crop if crop.size > 0 else None

    def _ocr_name(self, crop):
        if crop is None or crop.size == 0:
            return None
        try:
            results = self.ocr.readtext(crop, detail=0)
            return ' '.join(results).strip() if results else None
        except Exception:
            return None

    def _read_digits_yolo(self, crop, model, conf=0.3):
        if crop is None or crop.size == 0:
            return None
        results = model.predict(crop, conf=conf, verbose=False)
        if not results or not results[0].boxes:
            return None
        boxes = results[0].boxes
        names = model.names
        digit_boxes = []
        for box in boxes:
            cls_id = int(box.cls[0])
            label  = names[cls_id]
            x1_b   = float(box.xyxy[0][0])
            digit_boxes.append((x1_b, label))
        digit_boxes.sort(key=lambda x: x[0])
        raw = ''.join(label for _, label in digit_boxes)
        raw = re.sub(r'[^0-9\-]', '', raw)
        return raw if raw else None

    def _read_player_coords(self, frame):
        res_mm = self.m_minimap.predict(frame, conf=0.4, verbose=False)
        if not res_mm or not res_mm[0].boxes:
            return None, None, None
        mm_box = res_mm[0].boxes[0]
        mx1, my1, mx2, my2 = map(int, mm_box.xyxy[0])
        mm_crop = self._safe_crop(frame, mx1, my1, mx2, my2)
        if mm_crop is None:
            return None, None, None
        res_co = self.m_coords.predict(mm_crop, conf=0.4, verbose=False)
        if not res_co or not res_co[0].boxes:
            return None, None, None
        coord_values = {}
        coord_names  = self.m_coords.names
        for box in res_co[0].boxes:
            cls_id = int(box.cls[0])
            field  = coord_names[cls_id]
            bx1, by1, bx2, by2 = map(int, box.xyxy[0])
            field_crop = self._safe_crop(mm_crop, bx1, by1, bx2, by2)
            if field_crop is None:
                continue
            raw = self._read_digits_yolo(field_crop, self.m_co_digit)
            if raw is not None:
                try:
                    coord_values[field] = float(raw)
                except ValueError:
                    pass
        return coord_values.get('x_pos'), coord_values.get('y_pos'), coord_values.get('z_pos')

    _MAX_COORD_JUMP = 50.0

    def _update_player_coords(self, frame):
        x, y, z = self._read_player_coords(frame)
        if x is not None and z is not None:
            if self.player_x is not None and self.player_z is not None:
                jump = math.hypot(x - self.player_x, z - self.player_z)
                if jump > self._MAX_COORD_JUMP:
                    return self.player_x, self.player_y, self.player_z
            self.player_x = x
            self.player_y = y
            self.player_z = z
            self.coords_time = time.time()
            self.vel_tracker.update(x, z)
        return self.player_x, self.player_y, self.player_z

    def _read_wp_distance_yolo(self, dist_crop):
        raw = self._read_digits_yolo(dist_crop, self.m_wp_digit)
        if not raw:
            return None
        numeric = re.sub(r'[^0-9]', '', raw)
        if numeric:
            val = int(numeric)
            if 0 < val < 9999:
                return val
        return None

    def _get_arrow_direction(self, p_crop):
        if p_crop is None or p_crop.size == 0:
            return None
        gray = cv2.cvtColor(p_crop, cv2.COLOR_BGR2GRAY)
        gray_3ch = cv2.merge([gray, gray, gray])
        upscaled = cv2.resize(gray_3ch, None, fx=3, fy=3,
                              interpolation=cv2.INTER_CUBIC)
        results = self.m_arrow.predict(upscaled, conf=0.3, verbose=False)
        if not results or len(results[0].boxes) == 0:
            return None
        return self.m_arrow.names[int(results[0].boxes[0].cls[0])]

    def _extract_waypoint_data(self, frame):
        res_g = self.m_group.predict(frame, conf=0.4, verbose=False)
        if not res_g or not res_g[0].boxes:
            return
        for g_box in res_g[0].boxes:
            if check_kill():
                return
            gx1, gy1, gx2, gy2 = map(int, g_box.xyxy[0])
            group_crop = self._safe_crop(frame, gx1, gy1, gx2, gy2)
            if group_crop is None:
                continue
            res_p = self.m_panel.predict(group_crop, conf=0.4, verbose=False)
            if not res_p or not res_p[0].boxes:
                continue
            for p_box in res_p[0].boxes:
                if check_kill():
                    return
                px1, py1, px2, py2 = map(int, p_box.xyxy[0])
                p_crop = self._safe_crop(group_crop, px1, py1, px2, py2)
                if p_crop is None:
                    continue
                pcx = gx1 + (px1 + px2) // 2
                pcy = gy1 + (py1 + py2) // 2
                res_c = self.m_comp.predict(p_crop, conf=0.3, verbose=False)
                name, dist = "unknown", None
                if res_c and res_c[0].boxes:
                    for c_box in res_c[0].boxes:
                        cx1, cy1, cx2, cy2 = map(int, c_box.xyxy[0])
                        c_crop = self._safe_crop(p_crop, cx1, cy1, cx2, cy2)
                        if c_crop is None:
                            continue
                        cls_id = int(c_box.cls[0])
                        if cls_id == 0:
                            name = self._ocr_name(c_crop)
                        else:
                            dist = self._read_wp_distance_yolo(c_crop)
                direction = self._get_arrow_direction(p_crop)
                yield name, dist, (pcx, pcy), direction

    def get_target_status(self, frame, target_name):
        candidates = []
        for name, dist, (cx, cy), direction in self._extract_waypoint_data(frame):
            if check_kill():
                return None, None, None
            if fuzzy_match(target_name, name):
                candidates.append({
                    'dist':      dist,
                    'pos':       (cx, cy),
                    'direction': direction,
                    'dist_diff': abs(dist - self.last_valid_dist) if dist else 99999,
                })
        if not candidates:
            return None, None, None
        candidates.sort(key=lambda x: x['dist_diff'])
        best = candidates[0]
        self.target_last_center = best['pos']
        self.target_lock_active = True
        return best['direction'], best['dist'], best['pos']

    # =========================================================================
    # ALIGNMENT
    # =========================================================================
    def _do_explore_sweep(self, target_name):
        """Quick left/right sweep when all recal candidates exhausted."""
        print(f"\n[EXPLORE] Sweep...")
        self._stop_movement()

        def measure(turn_key, label):
            self._key_down(turn_key)
            time.sleep(EXPLORE_TURN_SECS)
            self._key_up(turn_key)
            self._key_down(FORWARD_KEY)
            time.sleep(EXPLORE_HOLD_SECS)
            self._key_up(FORWARD_KEY)
            frame = self.get_screen()
            _, d, _ = self.get_target_status(frame, target_name)
            cx, _, cz = self._update_player_coords(frame)
            if cx is not None:
                self.nav_db.mark_clear(PLANET, cx, cz)
            print(f"   {label}: {d}m")
            return d or 9999

        dl = measure(LEFT_KEY,  'LEFT')
        self._stop_movement()
        self._key_down(RIGHT_KEY)
        time.sleep(EXPLORE_TURN_SECS)
        self._key_up(RIGHT_KEY)
        dr = measure(RIGHT_KEY, 'RIGHT')
        self._stop_movement()

        # Return to better heading
        ret = RIGHT_KEY if dl <= dr else LEFT_KEY
        self._key_down(ret)
        time.sleep(EXPLORE_TURN_SECS)
        self._key_up(ret)

    def _hard_align(self, target_name, direction='N', timeout=10.0):
        print(f"   🔄 Realigning to {direction}...")
        self._stop_movement()
        time.sleep(0.1)
        self._key_down(RIGHT_KEY)
        start_time = time.time()
        last_arrow = None
        while True:
            if check_kill():
                self._key_up(RIGHT_KEY)
                self.shutdown()
                sys.exit(0)
            frame = self.get_screen()
            arrow, _, _ = self.get_target_status(frame, target_name)
            if arrow == direction:
                self._key_up(RIGHT_KEY)
                print(f"   ✓ Realigned ({arrow})")
                self.navigator.reset_estimator()
                return True
            if time.time() - start_time > timeout:
                self._key_up(RIGHT_KEY)
                print(f"   ⚠️  Align timeout (last arrow: {arrow}), continuing")
                return False
            if arrow != last_arrow:
                print(f"   ... {arrow}", end="\r", flush=True)
                last_arrow = arrow
            time.sleep(0.05)

    def _initial_align(self, target_name):
        self._stop_movement()
        time.sleep(0.3)
        self._hard_align(target_name, 'N')
        time.sleep(0.5)
        self._start_autorun()

    # =========================================================================
    # WAYPOINT SCAN
    # =========================================================================
    def _scan_for_waypoints(self):
        print("\n--- EXPLORATION PHASE ---")
        seen = []
        registry = []
        for _ in range(SCAN_STEPS):
            if check_kill():
                sys.exit(0)
            frame = self.get_screen()
            for name, dist, (cx, cy), direction in self._extract_waypoint_data(frame):
                is_new = True
                for existing in seen:
                    if fuzzy_match(existing['name'], name) and \
                            dist and abs(existing['dist'] - dist) < 50:
                        is_new = False
                        break
                if is_new and dist:
                    print(f"  Found: '{name}' ({dist}m)")
                    seen.append({'name': name, 'dist': dist})
                    registry.append({'name': name, 'distance': dist})
            self._tap(RIGHT_KEY, duration=SCAN_TURN_TIME)
            time.sleep(SCAN_SETTLE)
        return registry

    # =========================================================================
    # NAVIGATE
    # =========================================================================
    def navigate(self, vehicle_idx=3):
        # Vehicle select
        print("\n--- VEHICLE SELECT ---")
        vehicles = list(self.vehicle_speeds.items())
        for i, (k, v) in enumerate(vehicles):
            print(f"  {i}: {k:<12} max: {v['max']} m/s")
        choice = input(f"Vehicle [default=swoop]: ").strip()
        if choice.isdigit() and int(choice) < len(vehicles):
            vkey = vehicles[int(choice)][0]
        else:
            vkey = "swoop"
        self._apply_vehicle(vkey)

        # Scan for waypoints
        waypoints = self._scan_for_waypoints()
        if not waypoints:
            print("No waypoints found.")
            return

        print("\n--- WAYPOINTS FOUND ---")
        for i, wp in enumerate(waypoints):
            print(f"  [{i}] '{wp['name']}' ({wp['distance']}m)")

        choice = input("\nSelect ID: ").strip()
        if not choice.isdigit() or int(choice) >= len(waypoints):
            print("Invalid selection.")
            return
        target = waypoints[int(choice)]
        target_name = target['name']

        print(f"\n🎯  Target: '{target_name}' ({target['distance']}m)")
        print(f"   Vehicle: {self.current_vehicle} (max ~{self.max_speed} m/s)")
        print(f"   Corridor: ±{CORRIDOR_WIDTH}m | Clear mark: {CLEAR_HZ}Hz")

        # Reset state for new target
        self.navigator.reset()
        self._planner_active      = False
        self.min_dist_seen        = target['distance']
        self.last_valid_dist      = target['distance']
        self.close_read_count     = 0
        self._current_target_name = target_name

        # Start a run in the DB
        run_id = self.nav_db.start_run(PLANET, self.player_x or 0,
                                        self.player_z or 0, 0, 0)

        # Initial align
        print(f"⏳  Resuming in {TAB_IN_PAUSE}s...")
        time.sleep(TAB_IN_PAUSE)
        print("🧭  Aligning to target...")
        self._stop_movement()
        time.sleep(0.3)
        self._hard_align(target_name, 'N')

        # Get initial position and dist for exploration
        frame = self.get_screen()
        ox, _, oz = self._update_player_coords(frame)
        _, od, _  = self.get_target_status(frame, target_name)
        origin_pos  = (ox, oz) if ox is not None else (0.0, 0.0)
        origin_dist = od or 999

        # Cross exploration — maps local area, seeds target estimate and D*
        self.navigator.run_cross_exploration(
            target_name, origin_pos, origin_dist)

        # Align back to N before starting main loop
        self._hard_align(target_name, 'N')
        self._key_down(FORWARD_KEY)

        lost_timer = None

        # ─── MAIN LOOP ────────────────────────────────────────────────
        while True:
            if check_kill():
                print("\n🛑 Kill key pressed - stopping")
                break

            frame = self.get_screen()
            px, py, pz = self._update_player_coords(frame)
            arrow, dist, _ = self.get_target_status(frame, target_name)
            pos = (px, pz) if px is not None else None

            # Lost detection
            if dist is None:
                if lost_timer is None:
                    lost_timer = time.time()
                elif time.time() - lost_timer > OCR_STALE_LIMIT:
                    print(f"\n⚠️  Lost for {OCR_STALE_LIMIT:.0f}s, searching...")
                    self._key_up(FORWARD_KEY)
                    self._hard_align(target_name, 'N')
                    self.navigator.reset_estimator()
                    self._key_down(FORWARD_KEY)
                    lost_timer = None
                time.sleep(NAV_FREQ)
                continue
            lost_timer = None

            if dist is not None:
                self.last_valid_dist = dist
                if dist < self.min_dist_seen:
                    self.min_dist_seen = dist

            spd     = self.vel_tracker.speed()
            spd_pct = spd / self.max_speed if self.max_speed > 0 else 0

            # === ARRIVAL ===
            if dist <= STOP_DIST:
                self.close_read_count += 1
                if dist <= FINAL_CREEP_DIST:
                    self._key_up(FORWARD_KEY)
                    self._do_creep_pulse()
                if self.close_read_count >= VERIFY_READINGS:
                    self._key_up(FORWARD_KEY)
                    self.nav_db.complete_run(run_id)
                    self.nav_db.print_stats(PLANET)
                    print(f"\n\n🏁  SUCCESS!")
                    self.shutdown()
                    sys.exit(0)
            else:
                self.close_read_count = 0

            # === NAVIGATOR TICK ===
            steer = self.navigator.tick(pos, spd_pct, arrow, dist)

            if steer == 'STOP':
                self._key_up(FORWARD_KEY)

            elif steer == 'RECAL':
                result = self.navigator.do_recal(pos, target_name)
                if result is True:
                    self._key_down(FORWARD_KEY)
                elif result is False:
                    self._key_up(FORWARD_KEY)
                    self._do_explore_sweep(target_name)
                    self._key_down(FORWARD_KEY)
                # None = still attempting

            elif steer == -1:
                self._tap(LEFT_KEY, 0.1)
            elif steer == 1:
                self._tap(RIGHT_KEY, 0.1)

            # Log
            hx = f"{self.navigator.heading[0]:.1f}" \
                 if self.navigator.heading else "?"
            hz = f"{self.navigator.heading[1]:.1f}" \
                 if self.navigator.heading else "?"
            locked = "L" if self.navigator._est_locked else "."
            print(f"\r[{self.navigator.state[:5]:>5}] "
                  f"Dir:{arrow or '---':>3} | "
                  f"Dist:{dist or 0:>5}m | "
                  f"Min:{self.min_dist_seen:>5}m | "
                  f"Spd:{spd:.1f}m/s ({spd_pct*100:.0f}%) | "
                  f"Pos:({px:.0f},{pz:.0f}) | "
                  f"H:({hx},{hz}) Est:{locked}",
                  end='', flush=True)

            time.sleep(NAV_FREQ)

        # ─── END ──────────────────────────────────────────────────────
        print("\n\n🧹  Shutting down...")
        self.shutdown()


# =============================================================================
if __name__ == "__main__":
    nav = SWGMasterNavigator()
    nav.navigate()
