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
import random
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from ultralytics import YOLO

# =============================================================================
# NAVIGATION BRAIN v0.04
# Changes from v0.03:
#   1. Speed-based obstacle detection replaces Bug2 (no more spin-in-circles)
#   2. Smooth proportional steering — hold key, release when target speed hit
#   3. Wall-hug mode: aim ~45deg into wall until 50%+ max speed, then re-aim
#   4. Loop detection via position grid — if stuck same spot twice, flip side
#   5. Vehicle dismount fallback if full loop completes with no gap found
#   6. _hard_align rewritten: continuous rotation, releases on FIRST arrow match
#   7. Max speed loaded from speedometer JSON (per vehicle class)
#
# Requires master_controller.py running in a separate terminal first.
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

# Speed config — sits in the same folder as this script
SPEED_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed_config.json")

# Dismount key — assign /dismount to a keybind in SWG keymap and set here.
# If None, dismount will be skipped (bot continues on current vehicle).
DISMOUNT_KEY = None   # e.g. 'F10'

MONITOR_INDEX    = 2
NAV_FREQ         = 0.05          # 20fps main loop
AUTORUN_KEY      = 'AUTORUN'
KILL_KEY         = 'f8'
LEFT_KEY         = 'LEFT'
RIGHT_KEY        = 'RIGHT'
FORWARD_KEY      = 'FORWARD'
BACK_KEY         = 'BACK'

STOP_DIST        = 50
VERIFY_READINGS  = 5

FINAL_CREEP_DIST = 20
CREEP_PULSE_ON   = 0.8
CREEP_PULSE_OFF  = 0.15

SCAN_STEPS       = 4
SCAN_TURN_TIME   = 0.9
SCAN_SETTLE      = 0.4

FUZZY_THRESHOLD  = 0.5
OCR_STALE_LIMIT  = 15.0
CLOSE_RANGE_THRESHOLD = 25

ACCEPTABLE_DIRECTIONS = ('N',)

TAB_IN_PAUSE     = 4
DEBUG_DIR        = r"E:\label_work\debug_logs"

# ------------------------------------------------------------------
# Speed / obstacle detection config
# ------------------------------------------------------------------
SPEED_HISTORY_LEN        = 8      # samples to average for smooth velocity
OBSTACLE_SPEED_RATIO     = 0.20   # base threshold — 20% of max speed = obstacle detected
OBSTACLE_SPEED_RATIO_MAX = 0.50   # frustration ceiling — never exceed 50%
OBSTACLE_GRACE_SECS      = 4.0    # seconds after any alignment/stop before obstacle detection arms
OBSTACLE_CONFIRM_COUNT   = 3      # consecutive below-threshold readings before triggering wall-hug

# Stuck detection (positional fallback)
STUCK_DIST_THRESHOLD     = 2.0    # metres — less than this movement = stuck
STUCK_TIME_THRESHOLD     = 5.0    # seconds window to measure movement over

WALL_HUG_STEER_TIME      = 0.06   # seconds per LEFT nudge during wall-hug

# Loop / reverse detection
LOOP_SAME_POS_DIST       = 5.0    # metres — stuck if moved less than this from hug start
LOOP_STUCK_SECS          = 10.0   # seconds cooldown between direction changes
LOOP_MAX_TIMEOUT         = 180.0  # 3 minutes — give up and dismount

# ------------------------------------------------------------------
# Frustration index
# ------------------------------------------------------------------
FRUST_WINDOW_SECS        = 30.0   # if re-stuck within this window, escalate threshold
FRUST_STEP               = 0.05   # each escalation adds 5% to speed threshold
FRUST_RESET_DIST         = 300.0  # metres of clear travel resets threshold to base

NAV_STATE_FILE   = "nav_state.json"

# =============================================================================
# HELPERS
# =============================================================================

def fuzzy_match(target, detected, threshold=FUZZY_THRESHOLD):
    if not target or not detected:
        return False
    t = target.lower().replace(" ", "")
    d = detected.lower().replace(" ", "").replace("0", "o")
    if t in d or d in t:
        return True
    return SequenceMatcher(None, t, d).ratio() >= threshold

def check_kill():
    return keyboard.is_pressed(KILL_KEY)

def load_vehicle_speeds():
    """
    Load speed config from speedometer JSON.
    Returns dict of {vehicle: {"max", "p95", "threshold"}} entries.
    Falls back to defaults if file missing or malformed.
    Strip any trailing comments from the JSON first (the file has a # comment).
    """
    defaults = {
        "walking":     {"max": 5.20,  "p95": 5.00,  "threshold": 2.50},
        "running":     {"max": 10.50, "p95": 9.50,  "threshold": 4.75},
        "speederbike": {"max": 14.00, "p95": 13.50, "threshold": 6.75},
        "swoop":       {"max": 16.00, "p95": 15.00, "threshold": 7.50},
    }
    try:
        with open(SPEED_JSON) as f:
            raw = f.read()
        # Strip any lines starting with # (invalid JSON but present in file)
        clean = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))
        data = json.loads(clean)
        defaults.update(data)
        print(f"   Loaded speed config for: {list(data.keys())}")
    except Exception as e:
        print(f"   ⚠️  Speed JSON not found ({e}), using defaults")
    return defaults

# =============================================================================
class VelocityTracker:
    """
    Tracks real-world speed (m/s) from coordinate deltas.
    Keeps a rolling average to smooth out noisy reads.
    """
    def __init__(self, history_len=SPEED_HISTORY_LEN):
        self.history   = deque(maxlen=history_len)
        self.last_pos  = None
        self.last_time = None

    def update(self, x, y):
        now = time.time()
        if self.last_pos is not None and self.last_time is not None:
            dt = now - self.last_time
            if dt > 0.01:  # ignore sub-10ms noise
                dx = x - self.last_pos[0]
                dy = y - self.last_pos[1]
                spd = math.hypot(dx, dy) / dt
                self.history.append(spd)
        self.last_pos  = (x, y)
        self.last_time = now

    def current_speed(self):
        """Rolling average speed in m/s. Returns None if not enough data."""
        if len(self.history) < 3:
            return None
        return sum(self.history) / len(self.history)

    def reset(self):
        self.history.clear()
        self.last_pos  = None
        self.last_time = None


class LoopDetector:
    """
    Detects if the bot is stuck in a loop during wall-hug by checking
    whether it has returned to near its starting position after LOOP_STUCK_SECS.
    Allows one direction reversal per obstacle encounter, within LOOP_MAX_TIMEOUT.
    """
    def __init__(self):
        self.hug_start_pos   = None   # (x, y) when wall-hug began
        self.hug_start_time  = None
        self.reversed        = False  # have we already tried the other direction?

    def start(self, x, y):
        self.hug_start_pos  = (x, y)
        self.hug_start_time = time.time()
        self.reversed       = False
        self.max_dist_seen  = 0.0

    def check(self, x, y):
        """
        Returns 'reverse' if bot is back near start after LOOP_STUCK_SECS,
        'timeout' if LOOP_MAX_TIMEOUT exceeded, or None if all clear.
        Only triggers reverse if bot has actually moved >20m at some point
        (eliminates false triggers during initial acceleration phase).
        """
        if self.hug_start_pos is None:
            return None
        elapsed = time.time() - self.hug_start_time
        if elapsed > LOOP_MAX_TIMEOUT:
            return 'timeout'
        # Must have been hugging for LOOP_STUCK_SECS AND must have moved >20m
        # at some point (tracked via max_dist_seen) before we consider reversing
        current_dist = math.hypot(x - self.hug_start_pos[0], y - self.hug_start_pos[1])
        if not hasattr(self, 'max_dist_seen'):
            self.max_dist_seen = 0.0
        if current_dist > self.max_dist_seen:
            self.max_dist_seen = current_dist
        if (elapsed > LOOP_STUCK_SECS
                and not self.reversed
                and self.max_dist_seen > 20.0   # must have actually moved before we check
                and current_dist < LOOP_SAME_POS_DIST):
            return 'reverse'
        return None

    def mark_reversed(self, x, y):
        self.reversed       = True
        self.hug_start_pos  = (x, y)
        self.hug_start_time = time.time()
        self.max_dist_seen  = 0.0

    def reset(self):
        self.hug_start_pos  = None
        self.hug_start_time = None
        self.reversed       = False
        self.max_dist_seen  = 0.0


# =============================================================================
class SWGMasterNavigator:

    def __init__(self):
        print("--- Initialising Vision Systems ---")
        os.makedirs(DEBUG_DIR, exist_ok=True)

        # EasyOCR kept for waypoint name reading only
        self.reader = easyocr.Reader(['en'], gpu=True)

        # YOLO models
        self.m_group    = YOLO(MODELS["group"])
        self.m_panel    = YOLO(MODELS["panel"])
        self.m_comp     = YOLO(MODELS["comp"])
        self.m_arrow    = YOLO(MODELS["arrow"])
        self.m_wp_digit = YOLO(MODELS["wp_digit"])
        self.m_minimap  = YOLO(MODELS["minimap"])
        self.m_coords   = YOLO(MODELS["coords"])
        self.m_co_digit = YOLO(MODELS["co_digit"])

        # Velocity tracking — must be before _apply_vehicle
        self.vel_tracker = VelocityTracker()

        # Loop detection
        self.loop_detector = LoopDetector()

        # Vehicle speeds — full config per vehicle
        self.vehicle_speeds  = load_vehicle_speeds()
        self.current_vehicle = "swoop"
        self._apply_vehicle(self.current_vehicle)

        # Waypoint state
        self.registry           = []
        self.autorun_active     = False
        self.creep_mode         = False
        self.last_valid_dist    = 99999
        self.last_dist_time     = time.time()
        self.min_dist_seen      = 99999
        self.target_last_center = None
        self.target_lock_active = False
        self.close_read_count   = 0

        # Player coordinate state
        # SWGEmu minimap: x_pos = east/west, z_pos = north/south, y_pos = height
        # Navigation uses x_pos + z_pos for 2D movement. y_pos stored but not used for nav.
        self.player_x    = None   # east/west
        self.player_y    = None   # height (stored, not used for nav)
        self.player_z    = None   # north/south — primary nav axis
        self.coords_time = 0.0

        # Obstacle / wall-hug state
        self.wall_hug_active      = False
        self.wall_hug_side        = 'LEFT'
        self.wall_hug_start_dist  = None
        self.wall_hug_start_time  = None
        self.obstacle_grace_until = 0.0
        self.obstacle_low_count   = 0

        # Frustration index — escalates speed threshold when repeatedly stuck
        self.frust_ratio          = OBSTACLE_SPEED_RATIO   # current active threshold ratio
        self.frust_last_stuck_time = 0.0                   # when we last got stuck
        self.frust_last_stuck_pos  = None                  # (x,y) of last stuck event
        self.frust_clear_origin    = None                  # (x,y) where clear travel began

        # Stuck detection
        self.stuck_pos_history  = deque(maxlen=40)
        self.unstick_cooldown   = 0

    def _apply_vehicle(self, vehicle_key):
        """
        Apply speed profile for the given vehicle key.
        Sets max_speed, obstacle_threshold, and resets calibration.
        """
        cfg = self.vehicle_speeds.get(vehicle_key)
        if cfg is None:
            print(f"   ⚠️  Unknown vehicle '{vehicle_key}', defaulting to swoop")
            cfg = self.vehicle_speeds.get("swoop", {"max": 16.0, "p95": 15.0, "threshold": 7.5})
        self.current_vehicle      = vehicle_key
        self.max_speed            = cfg["max"]
        self.frust_ratio          = OBSTACLE_SPEED_RATIO   # reset to base on vehicle change
        self.obstacle_threshold   = self.max_speed * self.frust_ratio
        self.vel_tracker.reset()
        print(f"   🏍  Vehicle: {vehicle_key} | max: {self.max_speed} m/s | "
              f"obstacle threshold: {self.obstacle_threshold:.2f} m/s ({self.frust_ratio*100:.0f}% of max)")


    def _read_digits_yolo(self, crop, model, conf=0.3):
        if crop is None or crop.size == 0:
            return None
        results = model.predict(crop, conf=conf, verbose=False)
        if not results or not results[0].boxes:
            return None
        detections = []
        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            cx     = float(box.xyxy[0][0])
            label  = model.names[cls_id]
            detections.append((cx, label))
        detections.sort(key=lambda x: x[0])
        return "".join(d[1] for d in detections) if detections else None

    # =========================================================================
    # WAYPOINT DISTANCE
    # =========================================================================
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

    # =========================================================================
    # PLAYER COORDINATES
    # =========================================================================
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
            cls_id     = int(box.cls[0])
            field      = coord_names[cls_id]
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

    # Maximum plausible distance a player can move between two coord reads.
    # At 16 m/s swoop with up to 0.5s between reads = 8m. 3x headroom = 24m.
    _MAX_COORD_JUMP = 50.0

    def _update_player_coords(self, frame):
        x, y, z = self._read_player_coords(frame)
        # Need both x (east/west) and z (north/south) for 2D navigation
        if x is not None and z is not None:
            # Sanity check against implausible jumps (bad YOLO read)
            if self.player_x is not None and self.player_z is not None:
                jump = math.hypot(x - self.player_x, z - self.player_z)
                if jump > self._MAX_COORD_JUMP:
                    return self.player_x, self.player_y, self.player_z
            self.player_x    = x
            self.player_y    = y    # height — stored but not used for nav
            self.player_z    = z
            self.coords_time = time.time()
            self.vel_tracker.update(x, z)   # velocity from x+z (the nav plane)
        return self.player_x, self.player_y, self.player_z

    def _coords_fresh(self, max_age=2.0):
        return (self.player_x is not None and
                time.time() - self.coords_time < max_age)

    # =========================================================================
    # SPEED / OBSTACLE DETECTION
    # =========================================================================
    def _arm_obstacle_detection(self):
        """
        Call after any stop/align/hug-exit to suppress obstacle detection
        during the acceleration phase. Also resets the consecutive counter.
        """
        self.obstacle_grace_until = time.time() + OBSTACLE_GRACE_SECS
        self.obstacle_low_count   = 0
        self.vel_tracker.reset()

    def _obstacle_detected(self):
        """
        Returns True only if speed has been below 20% of max for
        OBSTACLE_CONFIRM_COUNT consecutive ticks, and grace period has expired.
        """
        if time.time() < self.obstacle_grace_until:
            self.obstacle_low_count = 0
            return False
        spd = self.vel_tracker.current_speed()
        if spd is None:
            return False
        trigger = self.max_speed * OBSTACLE_SPEED_RATIO   # 20% of max
        if spd < trigger:
            self.obstacle_low_count += 1
        else:
            self.obstacle_low_count = 0
        trigger = self.max_speed * self.frust_ratio   # escalates with frustration
        if spd < trigger:
            self.obstacle_low_count += 1
        else:
            self.obstacle_low_count = 0
        return self.obstacle_low_count >= OBSTACLE_CONFIRM_COUNT

    def _frust_on_stuck(self):
        """
        Called whenever wall-hug triggers.
        Rule 1: direction flip handled by LoopDetector (same-pos check).
        Rule 2: if re-stuck within FRUST_WINDOW_SECS, escalate speed threshold by 5%.
        """
        now = time.time()
        px  = self.player_x
        pz  = self.player_z   # north/south nav axis

        # Rule 2: escalate if re-stuck within frustration window
        if now - self.frust_last_stuck_time < FRUST_WINDOW_SECS:
            new_ratio = min(self.frust_ratio + FRUST_STEP, OBSTACLE_SPEED_RATIO_MAX)
            if new_ratio != self.frust_ratio:
                self.frust_ratio        = new_ratio
                self.obstacle_threshold = self.max_speed * self.frust_ratio
                print(f"\n😤  Frustration escalated — threshold now {self.frust_ratio*100:.0f}% "
                      f"({self.obstacle_threshold:.2f} m/s)")

        self.frust_last_stuck_time = now
        self.frust_last_stuck_pos  = (px, pz) if px is not None else None
        self.frust_clear_origin    = None   # reset clear travel on new stuck event

    def _frust_update_clear_travel(self):
        """
        Called each tick during normal (non-hug) nav.
        Rule 3: if bot has travelled >300m since last stuck, reset threshold to base.
        """
        if self.frust_ratio == OBSTACLE_SPEED_RATIO:
            return   # already at base
        if not self._coords_fresh():
            return
        if self.frust_clear_origin is None:
            self.frust_clear_origin = (self.player_x, self.player_z)
            return
        dist_clear = math.hypot(
            self.player_x - self.frust_clear_origin[0],
            self.player_z - self.frust_clear_origin[1]
        )
        if dist_clear >= FRUST_RESET_DIST:
            self.frust_ratio        = OBSTACLE_SPEED_RATIO
            self.obstacle_threshold = self.max_speed * self.frust_ratio
            self.frust_clear_origin = None
            print(f"\n😌  Frustration reset — {dist_clear:.0f}m clear travel, "
                  f"threshold back to {self.frust_ratio*100:.0f}%")

    # =========================================================================
    # SMOOTH ALIGNMENT
    # =========================================================================
    def _hard_align(self, target_name, required_arrow='N'):
        """
        Smoothly rotate until the waypoint arrow shows the required direction.
        Holds RIGHT key, checks at 50ms intervals, releases IMMEDIATELY on match.
        No overshooting, no tap-pause-tap pattern.
        """
        print(f"   🔄 Realigning to {required_arrow}...")
        self._stop_movement()
        self.stuck_pos_history.clear()
        time.sleep(0.1)

        self._key_down(RIGHT_KEY)
        start_time = time.time()
        last_arrow = None

        while True:
            if check_kill():
                self._key_up(RIGHT_KEY)
                self.shutdown()
                sys.exit(0)

            arrow, _, _ = self.get_target_status(self.get_screen(), target_name)

            if arrow == required_arrow:
                # Release immediately — don't let it continue past target
                self._key_up(RIGHT_KEY)
                print(f"   ✓ Realigned ({arrow})")
                return True

            # Timeout guard — if stuck spinning for >10s, break out
            if time.time() - start_time > 10:
                self._key_up(RIGHT_KEY)
                print(f"   ⚠️  Align timeout (last arrow: {arrow}), continuing anyway")
                return False

            # Print only on change to reduce spam
            if arrow != last_arrow:
                print(f"   ... {arrow}", end="\r", flush=True)
                last_arrow = arrow

            time.sleep(0.05)

    def _initial_align(self, target_name):
        print("🧭  Aligning to target...")
        return self._hard_align(target_name, 'N')

    # =========================================================================
    # WALL-HUG OBSTACLE AVOIDANCE
    # =========================================================================
    def _wall_hug_start(self, dist):
        self.wall_hug_side = 'LEFT'   # always start LEFT; loop detector flips to RIGHT if needed
        print(f"\n🧱  WALL-HUG — obstacle at {dist}m | steering {self.wall_hug_side} "
              f"| threshold: {self.obstacle_threshold:.2f} m/s ({self.frust_ratio*100:.0f}%)")
        self.wall_hug_active     = True
        self.wall_hug_start_dist = dist
        self.wall_hug_start_time = time.time()
        self._frust_on_stuck()
        if self._coords_fresh():
            self.loop_detector.start(self.player_x, self.player_z)
        else:
            self.loop_detector.reset()

    def _wall_hug_reset(self):
        self.wall_hug_active     = False
        self.wall_hug_start_dist = None
        self.wall_hug_start_time = None
        self.loop_detector.reset()

    def _wall_hug_step(self, target_name, arrow, dist):
        """
        One tick of wall-hug logic.

        Rules:
          1. If arrow == N and speed >= 50%: obstacle is clear, exit.
          2. If speed < 50%: nudge LEFT to find the gap.
          3. If speed >= 50% but arrow != N: hold heading (autorun carries us along wall).
          4. Loop check: if back at start pos after 10s, try RIGHT instead (once only).
          5. Timeout after 3 minutes: give up, dismount.

        Returns True if obstacle cleared.
        """
        spd = self.vel_tracker.current_speed()
        threshold = self.obstacle_threshold
        ratio = (spd / self.max_speed) if (spd is not None and self.max_speed > 0) else None

        # --- Loop / timeout check ---
        if self._coords_fresh():
            loop_status = self.loop_detector.check(self.player_x, self.player_z)
            if loop_status == 'timeout':
                print(f"\n🚨  Wall-hug timeout (3 min) — dismounting")
                self._dismount()
                self._wall_hug_reset()
                return False
            elif loop_status == 'reverse':
                print(f"\n🔁  Back at start after {LOOP_STUCK_SECS}s — realigning N then trying RIGHT")
                self.wall_hug_side = 'RIGHT'
                self.loop_detector.mark_reversed(self.player_x, self.player_z)
                self._hard_align(target_name, 'N')
                self._start_autorun()
                self._arm_obstacle_detection()

        steer_key = LEFT_KEY if self.wall_hug_side == 'LEFT' else RIGHT_KEY

        # --- Exit condition: speed recovered above threshold ---
        # Don't wait for arrow — exit immediately and realign to N
        if spd is not None and spd >= self.obstacle_threshold:
            print(f"\n✅  Speed recovered ({spd:.1f} m/s, {ratio*100:.0f}%) — exiting wall-hug")
            return True

        # --- Speed below threshold: nudge LEFT to find gap ---
        if ratio is None or spd < self.obstacle_threshold:
            self._tap(steer_key, duration=WALL_HUG_STEER_TIME)
            spd_str = f"{spd:.1f}" if spd is not None else "?"
            print(f"\r   ↩  Nudging {self.wall_hug_side} | spd:{spd_str} m/s ({ratio*100:.0f}% of max)   "
                  if ratio is not None else
                  f"\r   ↩  Nudging {self.wall_hug_side} | spd:{spd_str} m/s   ",
                  end="", flush=True)
        else:
            # Speed is good, arrow isn't N yet — just ride it, no steering input
            print(f"\r   🧱 Hugging | spd:{spd:.1f} m/s ({ratio*100:.0f}%) | arrow:{arrow or '?'}   ",
                  end="", flush=True)

        return False

    def _dismount(self):
        """
        Dismount the current vehicle.
        Requires DISMOUNT_KEY to be set to your SWG keybind for /dismount.
        After dismounting, switches speed profile to 'running'.
        """
        print("   🏇 Dismounting vehicle...")
        if DISMOUNT_KEY:
            self._tap(DISMOUNT_KEY, duration=0.1)
            time.sleep(1.0)
            self._apply_vehicle("running")
            print("   ✓ Dismounted — switched to running profile")
        else:
            print("   ⚠️  DISMOUNT_KEY not set in config — set it at the top of the script")
            print("        Bind /dismount to a key in SWG keymap, then set DISMOUNT_KEY = 'yourkey'")
            # Can't dismount, but switch threshold to running to avoid false obstacle triggers
            # while we're clearly slowed/stuck on the vehicle
            time.sleep(1.0)

    # =========================================================================
    # STUCK DETECTION (coordinate-based)
    # =========================================================================
    def _record_pos(self):
        if self._coords_fresh():
            self.stuck_pos_history.append(((self.player_x, self.player_z), time.time()))

    def _check_stuck(self):
        if not self._coords_fresh() or not self.autorun_active:
            return False
        if self.wall_hug_active:
            return False

        self._record_pos()

        if len(self.stuck_pos_history) < 5:
            return False

        now = time.time()
        oldest_pos, oldest_time = self.stuck_pos_history[0]
        if now - oldest_time < STUCK_TIME_THRESHOLD:
            return False

        dist_moved = math.hypot(
            self.player_x - oldest_pos[0],
            self.player_z - oldest_pos[1]
        )
        if dist_moved < STUCK_DIST_THRESHOLD and time.time() > self.unstick_cooldown:
            print(f"\n🚨  POSITIONAL STUCK — moved only {dist_moved:.1f}m in {now - oldest_time:.0f}s")
            return True
        return False

    # =========================================================================
    # COMMAND SENDER (unchanged from v0.03)
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

    def _tap_multi(self, keys, duration=0.0):
        self._send({"cmd": "TAP_MULTI", "keys": keys, "duration": duration})
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

    # =========================================================================
    # SCREEN / OCR
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
            return "unknown"
        gray      = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        padded    = cv2.copyMakeBorder(thresh, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
        results   = self.reader.readtext(padded, detail=0, paragraph=False)
        text      = "".join(results).strip().lower()
        return text or "unknown"

    # =========================================================================
    # ARROW DETECTION
    # =========================================================================
    def _get_arrow_direction(self, p_crop):
        if p_crop is None or p_crop.size == 0:
            return None
        gray     = cv2.cvtColor(p_crop, cv2.COLOR_BGR2GRAY)
        gray_3ch = cv2.merge([gray, gray, gray])
        upscaled = cv2.resize(gray_3ch, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        results  = self.m_arrow.predict(upscaled, conf=0.3, verbose=False)
        if not results or len(results[0].boxes) == 0:
            return None
        return self.m_arrow.names[int(results[0].boxes[0].cls[0])]

    # =========================================================================
    # WAYPOINT EXTRACTION
    # =========================================================================
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

    # =========================================================================
    # SCAN
    # =========================================================================
    def scan_for_waypoints(self):
        print("\n--- EXPLORATION PHASE ---")
        self.registry = []
        seen = []
        for _ in range(SCAN_STEPS):
            if check_kill():
                print("\n🛑 Kill key pressed - exiting")
                sys.exit(0)
            frame = self.get_screen()
            for name, dist, (cx, cy), direction in self._extract_waypoint_data(frame):
                is_new = True
                for existing in seen:
                    if fuzzy_match(existing['name'], name) and abs(existing['dist'] - dist) < 50:
                        is_new = False
                        break
                if is_new and dist:
                    print(f"  Found: '{name}' ({dist}m)")
                    seen.append({'name': name, 'dist': dist})
                    self.registry.append({
                        'name':       name,
                        'distance':   dist,
                        'screen_pos': (cx, cy),
                        'direction':  direction,
                    })
            self._tap(RIGHT_KEY, duration=SCAN_TURN_TIME)
            time.sleep(SCAN_SETTLE)

    # =========================================================================
    # TARGET STATUS
    # =========================================================================
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
    # SHUTDOWN
    # =========================================================================
    def shutdown(self):
        print("\n🧹 Shutting down...")
        self._stop_movement()
        self._release_all()
        print("✓ All keys released")

    # =========================================================================
    # MAIN NAV LOOP
    # =========================================================================
    def navigate(self, selection_index):
        target      = self.registry[selection_index]
        target_name = target['name']
        print(f"\n🎯  Target: '{target_name}' ({target['distance']}m)")
        print(f"   Vehicle: {self.current_vehicle} (max ~{self.max_speed:.1f} m/s)")
        print(f"   Verify: {VERIFY_READINGS} consecutive readings ≤ {STOP_DIST}m")
        print(f"⏳  Resuming in {TAB_IN_PAUSE}s...")
        time.sleep(TAB_IN_PAUSE)

        self._initial_align(target_name)

        self.last_valid_dist   = target['distance']
        self.min_dist_seen     = target['distance']
        self.last_dist_time    = time.time()
        self.close_read_count  = 0
        self.stuck_pos_history.clear()
        self.loop_detector.reset()
        self._wall_hug_reset()
        self.unstick_cooldown  = 0

        self._start_autorun()
        self._arm_obstacle_detection()   # suppress until vehicle reaches speed

        while True:
            if check_kill():
                print("\n🛑 Kill key pressed - stopping")
                self.shutdown()
                sys.exit(0)

            frame = self.get_screen()
            px, py, pz = self._update_player_coords(frame)
            arrow, dist, pos = self.get_target_status(frame, target_name)

            if dist is not None:
                self.last_valid_dist = dist
                self.last_dist_time  = time.time()
                if dist < self.min_dist_seen:
                    self.min_dist_seen = dist

                is_close = dist <= CLOSE_RANGE_THRESHOLD

                if dist <= STOP_DIST:
                    self.close_read_count += 1
                    status_marker = f" ✓{self.close_read_count}/{VERIFY_READINGS}"
                else:
                    self.close_read_count = 0
                    status_marker = ""

                # Status line — show x,z (nav plane) not x,y
                pz         = self.player_z
                coord_str  = f"({px:.0f},{pz:.0f})" if px is not None and pz is not None else "(?,?)"
                spd        = self.vel_tracker.current_speed()
                spd_str    = f"{spd:.1f}m/s" if spd is not None else "?m/s"
                ratio      = (spd / self.max_speed) if spd is not None else None
                ratio_str  = f"{ratio*100:.0f}%" if ratio is not None else "?%"
                mode_str   = "HUG  " if self.wall_hug_active else "NAV  "
                run_str    = "CREEP" if self.creep_mode else "RUN  "
                frust_str  = f" 😤{self.frust_ratio*100:.0f}%" if self.frust_ratio > OBSTACLE_SPEED_RATIO else ""
                print(f"\r[{mode_str}|{run_str}] Dir:{arrow or '---':>3} | Dist:{dist:>5}m | "
                      f"Min:{self.min_dist_seen:>4}m | Spd:{spd_str} ({ratio_str}){frust_str} | Pos:{coord_str}{status_marker}",
                      end="", flush=True)

                # === ARRIVAL ===
                if self.close_read_count >= VERIFY_READINGS:
                    self._stop_movement()
                    print(f"\n\n🏁  SUCCESS! Verified {VERIFY_READINGS} consecutive readings ≤ {STOP_DIST}m")
                    self.shutdown()
                    sys.exit(0)

                # Hard align when close and misaligned
                if is_close and arrow and arrow not in ACCEPTABLE_DIRECTIONS:
                    print(f"\n⚠️  Misaligned ({arrow}) near waypoint...")
                    self._wall_hug_reset()
                    self._hard_align(target_name, 'N')
                    self._start_autorun()
                    continue

                # Creep mode
                if dist <= FINAL_CREEP_DIST:
                    if not self.creep_mode:
                        print(f"\n🐢  Final approach ({dist}m)...")
                        self._stop_movement()
                        self.creep_mode = True
                        self._wall_hug_reset()
                    if arrow and arrow not in ACCEPTABLE_DIRECTIONS:
                        self._hard_align(target_name, 'N')
                    self._do_creep_pulse()
                    continue
                else:
                    if self.creep_mode:
                        self._stop_movement()
                        self._start_autorun()

                # =========================================================
                # WALL-HUG ACTIVE
                # =========================================================
                if self.wall_hug_active:
                    cleared = self._wall_hug_step(target_name, arrow, dist)
                    if cleared:
                        self._wall_hug_reset()
                        self._hard_align(target_name, 'N')
                        self._start_autorun()
                        self._arm_obstacle_detection()   # rearm after stop+realign
                    continue

                # =========================================================
                # OBSTACLE DETECTION (speed-based)
                # =========================================================
                if self._obstacle_detected():
                    self._wall_hug_start(dist)
                    continue

                # =========================================================
                # POSITIONAL STUCK (no movement at all — emergency fallback)
                # =========================================================
                if self._check_stuck():
                    print(f"\n🚨  Hard stuck — triggering wall-hug")
                    self.stuck_pos_history.clear()
                    self.unstick_cooldown = time.time() + 3.0
                    self._wall_hug_start(dist)
                    continue

                # =========================================================
                # NORMAL NAV STEERING
                # =========================================================
                self._frust_update_clear_travel()   # reset frustration after 300m clear run
                if self.autorun_active and arrow:
                    if arrow in ('W', 'SW'):
                        self._tap(LEFT_KEY,  duration=0.2)
                    elif arrow in ('E', 'SE'):
                        self._tap(RIGHT_KEY, duration=0.2)
                    elif arrow == 'NW':
                        self._tap(LEFT_KEY,  duration=0.1)
                    elif arrow == 'NE':
                        self._tap(RIGHT_KEY, duration=0.1)

                if not self.autorun_active:
                    self._start_autorun()

            else:
                # Lost waypoint
                lost = time.time() - self.last_dist_time
                if lost > OCR_STALE_LIMIT:
                    print(f"\n⚠️  Lost for {lost:.0f}s, searching...")
                    self._stop_movement()
                    self._wall_hug_reset()
                    self._key_down(RIGHT_KEY)
                    found        = False
                    search_start = time.time()
                    while time.time() - search_start < 6.0:
                        if check_kill():
                            self._key_up(RIGHT_KEY)
                            self.shutdown()
                            sys.exit(0)
                        time.sleep(0.1)
                        a, d, _ = self.get_target_status(self.get_screen(), target_name)
                        if d:
                            found = True
                            break
                    self._key_up(RIGHT_KEY)
                    if found:
                        print(f"   ✓ Reacquired at {d}m")
                        self._start_autorun()
                    else:
                        print(f"   ✗ Not found, full reset...")
                        self.target_lock_active = False
                        self.close_read_count   = 0
                        self._initial_align(target_name)
                        self._start_autorun()

            time.sleep(NAV_FREQ)


# =============================================================================
if __name__ == "__main__":
    nav = SWGMasterNavigator()

    # Vehicle selection at startup
    print("\n--- VEHICLE SELECT ---")
    vehicles = list(nav.vehicle_speeds.keys())
    for i, v in enumerate(vehicles):
        cfg = nav.vehicle_speeds[v]
        trigger = cfg['max'] * OBSTACLE_SPEED_RATIO
        print(f"  {i}: {v:<12} max: {cfg['max']} m/s  |  obstacle trigger: <{trigger:.2f} m/s ({OBSTACLE_SPEED_RATIO*100:.0f}%)")
    vc = input(f"Vehicle [default=swoop]: ").strip()
    selected = "swoop"
    if vc.isdigit() and int(vc) < len(vehicles):
        selected = vehicles[int(vc)]
    elif vc in vehicles:
        selected = vc
    nav._apply_vehicle(selected)

    nav.scan_for_waypoints()

    if not nav.registry:
        print("No waypoints found.")
        sys.exit()

    print("\n--- WAYPOINTS FOUND ---")
    for idx, item in enumerate(nav.registry):
        print(f"  [{idx}] '{item['name']}' ({item['distance']}m)")

    choice = input("\nSelect ID: ").strip()
    if choice.isdigit():
        nav.navigate(int(choice))
    else:
        print("Invalid selection.")

# run: python "E:\SWGEMU_bot\jedai\Scripts\movement\navigation_v0_04.py"
