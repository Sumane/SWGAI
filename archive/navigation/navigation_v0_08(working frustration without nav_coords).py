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

# =============================================================================
# NAVIGATION BRAIN v0.18
# v0.15: locked-side hug, frustration flip (L→R), exploration sweep escalation
#        confirmed-free exit (N + speed >= HIGH for CONFIRM_SECS)
#        speed thresholds raised to 30/40%
# v0.16: gap detection — speed rises through GAP_RATIO (50%) while hugging away
#        from wall signals obstacle ended. Stop, hard-align to N, resume.
# v0.17: position memory + revisit frustration index. Levels 3+: flip side.
#        Level 6+: chaos exploration sweep.
# v0.18: frustration decay — if bot makes FRUSTRATION_DECAY_DIST (75m) progress
#        toward the waypoint since frustration first triggered, reset index to 0.
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

SPEED_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "speed_config.json")
DISMOUNT_KEY = None

MONITOR_INDEX    = 2
NAV_FREQ         = 0.05
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

LOW_RATIO        = 0.30
HIGH_RATIO       = 0.40
GAP_RATIO        = 0.50   # rising through this while hugging away = obstacle ended
GRACE_SECS       = 3.0
SPEED_HISTORY_LEN = 3

# Hug controller
HUG_FRUSTRATION_PATH  = 10.0   # total metres travelled before frustration check
HUG_FRUSTRATION_DISP  = 2.0    # net displacement (m) from hug origin — below = looping
CONFIRM_SECS          = 3.0    # seconds of N + speed >= HIGH_RATIO to exit hug
SOUTH_DEBOUNCE        = 3      # consecutive southern arrow reads before hard-correcting
HUG_ENTRY_DEBOUNCE    = 3      # consecutive low-speed reads before entering HUG
EXPLORE_TURN_SECS     = 1.5    # seconds to turn ~45° during exploration sweep
EXPLORE_HOLD_SECS     = 3.0    # seconds to measure distance each sweep leg

# Position memory / revisit frustration
POS_REVISIT_RADIUS    = 2.0    # metres — "same spot" threshold
POS_REVISIT_MIN_TRAVEL = 10.0  # metres walked since last visit before it counts
POS_MEMORY_LEN        = 20     # how many past REALIGN positions to remember
FRUSTRATION_FLIP_LVL  = 1      # frustration_index >= this → force opposite side
FRUSTRATION_CHAOS_LVL = 6      # frustration_index >= this → random escape move
FRUSTRATION_DECAY_DIST = 75.0  # metres closer to waypoint → reset frustration index

NAV_STATE_FILE   = "nav_state.json"

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
    defaults = {
        "walking":     {"max": 5.20},
        "running":     {"max": 10.50},
        "speederbike": {"max": 14.00},
        "swoop":       {"max": 16.00},
    }
    try:
        with open(SPEED_JSON) as f:
            raw = f.read()
        clean = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))
        data = json.loads(clean)
        defaults.update(data)
        print(f"   Loaded speed config for: {list(data.keys())}")
    except Exception as e:
        print(f"   ⚠️  Speed JSON not found ({e}), using defaults")
    return defaults

# =============================================================================
class VelocityTracker:
    def __init__(self, history_len=SPEED_HISTORY_LEN):
        self.history   = deque(maxlen=history_len)
        self.last_pos  = None
        self.last_time = None

    def update(self, x, y):
        now = time.time()
        if self.last_pos is not None and self.last_time is not None:
            dt = now - self.last_time
            if dt > 0.01:
                dx = x - self.last_pos[0]
                dy = y - self.last_pos[1]
                spd = math.hypot(dx, dy) / dt
                self.history.append(spd)
        self.last_pos  = (x, y)
        self.last_time = now

    def current_speed(self):
        if len(self.history) < 2:
            return None
        return sum(self.history) / len(self.history)

    def reset(self):
        self.history.clear()
        self.last_pos  = None
        self.last_time = None


class SimpleHugController:
    """
    States: CLEAR → HUG → CONFIRMING → CLEAR

    CLEAR:  steer toward N using arrow direction only.
    HUG:    locked-side nudge-and-move obstacle following.
            Side defaults to LEFT; flips to RIGHT on frustration trigger.
            Frustration: path_dist > HUG_FRUSTRATION_PATH AND
                         displacement_from_origin < HUG_FRUSTRATION_DISP
            Second frustration (already on RIGHT): trigger exploration sweep.
    CONFIRMING: arrow==N AND speed>=HIGH for CONFIRM_SECS continuously → CLEAR.
                Any deviation resets the confirm timer back to HUG.

    Speed thresholds (fraction of max_speed):
        < LOW_RATIO  → obstacle detected, nudge away from wall
        > HIGH_RATIO → too far from wall, nudge toward wall
        between     → dead band, no steer
    """

    # Internal state constants
    _CLEAR      = 0
    _HUG        = 1
    _CONFIRMING = 2

    def __init__(self, nav_parent):
        self.nav        = nav_parent
        self.state      = self._CLEAR
        self.grace_until = 0.0

        # Hug side: -1 = LEFT (default), +1 = RIGHT
        self.hug_side   = -1
        self.flipped     = False          # True once we've already flipped to RIGHT

        # Frustration tracking
        self.hug_origin  = None           # (x, z) when hug was entered / reset
        self.path_dist   = 0.0            # total metres walked since hug_origin set
        self.last_pos    = None           # last known position for delta calc

        # Confirmed-free timer
        self.confirm_start = None
        self.south_count   = 0
        self.low_count     = 0
        self.prev_speed    = None   # for gap detection rising-edge check

        # Position memory — revisit frustration
        # Each entry: {'pos': (x,z), 'travel_at_visit': float}
        self.pos_memory        = deque(maxlen=POS_MEMORY_LEN)
        self.total_travel      = 0.0   # odometer — never resets (survives hug resets)
        self.frustration_index = 0
        self._force_right      = False  # set by _frustration_realign at flip level
        self.frustration_dist_trigger = None  # dist-to-waypoint when index first went > 0

    # ------------------------------------------------------------------
    def arm(self):
        """Call after _start_autorun — gives GRACE_SECS before hug logic runs."""
        self.grace_until = time.time() + GRACE_SECS
        self.state = self._CLEAR

    def reset(self):
        self.state       = self._CLEAR
        self.grace_until = 0.0
        self.hug_side    = -1
        self.flipped     = False
        self.hug_origin  = None
        self.path_dist   = 0.0
        self.last_pos    = None
        self.confirm_start = None
        self.south_count   = 0
        self.low_count     = 0
        self.prev_speed    = None
        self._force_right  = False
        # NOTE: total_travel, pos_memory, frustration_index intentionally
        # NOT reset here — they survive individual hug cycles.

    def full_reset(self):
        """Hard reset including odometer and frustration — call on new target."""
        self.reset()
        self.total_travel             = 0.0
        self.pos_memory               = deque(maxlen=POS_MEMORY_LEN)
        self.frustration_index        = 0
        self._force_right             = False
        self.frustration_dist_trigger = None

    def notify_dist(self, dist):
        """
        Called every nav loop tick with the current waypoint distance.
        If frustration is active and we've closed the gap by FRUSTRATION_DECAY_DIST
        since it first triggered, reset the frustration index completely.
        """
        if self.frustration_index == 0 or dist is None:
            return

        if self.frustration_dist_trigger is None:
            # Lazily record the dist at which frustration first became active
            self.frustration_dist_trigger = dist
            return

        progress = self.frustration_dist_trigger - dist
        if progress >= FRUSTRATION_DECAY_DIST:
            print(f"\n[FRUSTRATION] Cleared — made {progress:.0f}m progress "
                  f"(index was {self.frustration_index}, trigger was "
                  f"{self.frustration_dist_trigger:.0f}m, now {dist:.0f}m)")
            self.frustration_index        = 0
            self._force_right             = False
            self.frustration_dist_trigger = None
            self.pos_memory.clear()   # wipe the old position history too

    def _flip_side(self, current_pos):
        self.hug_side   = 1           # switch to RIGHT
        self.flipped    = True
        self.hug_origin = current_pos
        self.path_dist  = 0.0
        self.last_pos   = current_pos
        print(f"\n[HUG] Frustration flip → side=RIGHT, new origin={current_pos}")

    def _update_path(self, current_pos):
        """Accumulate total path distance. Returns current net displacement."""
        if current_pos is None:
            return None
        if self.last_pos is not None:
            delta = math.hypot(current_pos[0] - self.last_pos[0],
                               current_pos[1] - self.last_pos[1])
            self.path_dist   += delta
            self.total_travel += delta   # global odometer
        self.last_pos = current_pos
        if self.hug_origin is None:
            return None
        return math.hypot(current_pos[0] - self.hug_origin[0],
                          current_pos[1] - self.hug_origin[1])

    def _check_revisit(self, current_pos):
        """
        Called on each REALIGN. Checks if current_pos is within POS_REVISIT_RADIUS
        of any remembered position that was recorded at least POS_REVISIT_MIN_TRAVEL
        metres ago. If so, increments frustration_index and returns True.
        Always records current_pos into memory afterward.
        """
        if current_pos is None:
            return False

        revisit = False
        for entry in self.pos_memory:
            dist_to_old = math.hypot(current_pos[0] - entry['pos'][0],
                                     current_pos[1] - entry['pos'][1])
            travel_since = self.total_travel - entry['travel_at_visit']
            if dist_to_old <= POS_REVISIT_RADIUS and travel_since >= POS_REVISIT_MIN_TRAVEL:
                was_zero = (self.frustration_index == 0)
                self.frustration_index += 1
                if was_zero:
                    # notify_dist() will capture the actual waypoint dist on next tick
                    self.frustration_dist_trigger = None
                print(f"\n[FRUSTRATION] Revisited ({current_pos[0]:.0f},{current_pos[1]:.0f}) "
                      f"— index now {self.frustration_index} "
                      f"(was here {dist_to_old:.1f}m away, {travel_since:.0f}m ago)")
                revisit = True
                break  # one increment per REALIGN event

        self.pos_memory.append({'pos': current_pos, 'travel_at_visit': self.total_travel})
        return revisit

    # ------------------------------------------------------------------
    def _frustration_realign(self, current_pos):
        """
        Called instead of bare reset()+REALIGN whenever we're about to snap back to N.
        Applies escalating behaviour based on frustration_index, then returns
        the appropriate sentinel for the nav loop.

        Level 0–2  (normal):  reset + REALIGN as before
        Level 3–5  (flip):    force hug_side to RIGHT on next entry, then REALIGN
        Level 6+   (chaos):   return 'EXPLORE' to trigger random sweep, then REALIGN
        """
        idx = self.frustration_index
        self.reset()  # clears hug state but preserves index/odometer/memory

        if idx >= FRUSTRATION_CHAOS_LVL:
            print(f"\n[FRUSTRATION] Chaos level ({idx}) — triggering exploration sweep")
            return 'EXPLORE'

        if idx >= FRUSTRATION_FLIP_LVL:
            print(f"\n[FRUSTRATION] Flip level ({idx}) — forcing RIGHT side on next hug")
            self._force_right = True
            return 'REALIGN'

        return 'REALIGN'

    def _enter_hug(self, current_pos):
        self.state      = self._HUG
        # Respect forced-right from frustration if set, otherwise default LEFT
        if getattr(self, '_force_right', False):
            self.hug_side = 1
            self._force_right = False
            print(f"\n[HUG] Entered — side=RIGHT (frustration forced) origin={current_pos}")
        else:
            self.hug_side = -1
            print(f"\n[HUG] Entered — side=LEFT origin={current_pos}")
        self.flipped    = False
        self.hug_origin = current_pos
        self.path_dist  = 0.0
        self.last_pos   = current_pos
        self.confirm_start = None

    # ------------------------------------------------------------------
    def tick(self, speed, arrow, current_pos=None):
        """
        Returns steering command: -1=tap LEFT, 0=no steer, +1=tap RIGHT.
        current_pos: (x, z) tuple from player coordinates, or None.
        Call nav._do_exploration_sweep() externally when return value is
        the sentinel 'EXPLORE' (string).
        """

        # Grace period after arm() — ignore ALL steering, run straight
        if time.time() < self.grace_until:
            self.state = self._CLEAR
            self.south_count = 0
            return 0

        # Global odometer — tick regardless of state so CLEAR travel counts too
        if current_pos is not None and self.last_pos is not None:
            delta = math.hypot(current_pos[0] - self.last_pos[0],
                               current_pos[1] - self.last_pos[1])
            # Only count movement outside of HUG (HUG ticks via _update_path)
            if self.state != self._HUG:
                self.total_travel += delta
        if current_pos is not None and self.state != self._HUG:
            self.last_pos = current_pos  # keep last_pos fresh for when HUG starts

        # If speed unavailable, still correct heading but never enter HUG
        if speed is None:
            if self.state == self._CLEAR:
                if arrow in ('NW', 'W'):
                    return -1
                elif arrow in ('NE', 'E'):
                    return 1
            return 0

        low  = self.nav.max_speed * LOW_RATIO
        high = self.nav.max_speed * HIGH_RATIO

        # ==============================================================
        # STATE: CLEAR — normal approach nav with continuous heading correction
        # ==============================================================
        if self.state == self._CLEAR:
            if arrow == 'N':
                self.south_count = 0
                # Heading correct — only enter hug if low speed persists (debounced)
                if speed < low:
                    self.low_count += 1
                    if self.low_count >= HUG_ENTRY_DEBOUNCE:
                        self.low_count = 0
                        self._enter_hug(current_pos)
                        # Fall through to HUG logic below
                    else:
                        return 0
                else:
                    self.low_count = 0
                    return 0
            elif arrow in ('NW', 'W'):
                self.south_count = 0
                self.low_count = 0
                return -1       # waypoint is left of us — turn left
            elif arrow in ('NE', 'E'):
                self.south_count = 0
                self.low_count = 0
                return 1        # waypoint is right of us — turn right
            elif arrow in ('SW', 'S', 'SE'):
                # Debounce: require SOUTH_DEBOUNCE consecutive southern reads
                self.south_count = getattr(self, 'south_count', 0) + 1
                if self.south_count >= SOUTH_DEBOUNCE:
                    return -1 if arrow in ('SW', 'S') else 1
                # Not enough consecutive reads yet — hold course
                return 0

        # ==============================================================
        # STATE: HUG — locked-side nudge-and-move
        # ==============================================================
        if self.state == self._HUG:

            # Update path & check frustration
            if current_pos is not None:
                disp = self._update_path(current_pos)
                if disp is not None and self.path_dist > HUG_FRUSTRATION_PATH:
                    if disp < HUG_FRUSTRATION_DISP:
                        if not self.flipped:
                            self._flip_side(current_pos)
                        else:
                            # Already flipped once — escalate to exploration sweep
                            print(f"\n[HUG] Second frustration trigger → EXPLORE SWEEP")
                            return 'EXPLORE'

            # Check if we've earned a transition to CONFIRMING
            if arrow == 'N' and speed >= high:
                if self.confirm_start is None:
                    self.confirm_start = time.time()
                    self.state = self._CONFIRMING
                    print(f"\n[HUG] Arrow=N + speed high — starting confirm timer")
                    return 0
            else:
                self.confirm_start = None

            # Nudge-and-move: locked to hug_side
            # hug_side == -1 (LEFT): we hug the left wall
            #   speed < low  → too close / blocked → tap RIGHT (step away)
            #   speed > high → too far from wall   → tap LEFT  (step toward)
            # hug_side == +1 (RIGHT): mirror
            if speed < low:
                self.prev_speed = speed
                return -self.hug_side   # step away from wall
            elif speed > high:
                # GAP DETECTION: if we were previously stepping away from the wall
                # (speed was between low and high, rising) and we've now crossed
                # GAP_RATIO, the obstacle has likely ended — snap to N and retry.
                gap = self.nav.max_speed * GAP_RATIO
                if (self.prev_speed is not None
                        and self.prev_speed < gap
                        and speed >= gap):
                    print(f"\n[HUG] Gap detected! Speed rose through {int(GAP_RATIO*100)}% "
                          f"({self.prev_speed:.1f}→{speed:.1f}) — realigning to N")
                    self.prev_speed = speed
                    self._check_revisit(current_pos)
                    return self._frustration_realign(current_pos)
                self.prev_speed = speed
                return self.hug_side    # step toward wall
            self.prev_speed = speed
            return 0

        # ==============================================================
        # STATE: CONFIRMING — verify we're genuinely free
        # ==============================================================
        if self.state == self._CONFIRMING:
            if speed < low:
                # Hit a wall again — back to HUG immediately
                print(f"\n[HUG] Confirm broken (wall) → back to HUG")
                self.confirm_start = None
                self.state = self._HUG
                return self.hug_side
            if arrow in ('S', 'SW', 'SE'):
                # Severe drift — break confirm, back to HUG
                print(f"\n[HUG] Confirm broken (severe drift {arrow}) → back to HUG")
                self.confirm_start = None
                self.state = self._HUG
                return self.hug_side
            # Gentle drift — nudge back toward N but keep confirm timer running
            if arrow in ('NW', 'W'):
                steer = -1  # waypoint is left — turn left
            elif arrow in ('NE', 'E'):
                steer = 1   # waypoint is right — turn right
            else:
                steer = 0   # arrow == N, running clean
            elapsed = time.time() - self.confirm_start
            if elapsed >= CONFIRM_SECS:
                print(f"\n[HUG] Confirmed free — hard aligning to N")
                self._check_revisit(current_pos)
                return self._frustration_realign(current_pos)
            return steer

        return 0

    def get_status(self):
        fi = f" F{self.frustration_index}" if self.frustration_index > 0 else ""
        if self.state == self._CLEAR:
            return f"CLEAR{fi}"
        if self.state == self._HUG:
            side = "L" if self.hug_side == -1 else "R"
            return f"HUG-{side}{fi}"
        if self.state == self._CONFIRMING:
            elapsed = time.time() - self.confirm_start if self.confirm_start else 0
            return f"CONF {elapsed:.1f}s{fi}"
        return "???"


# =============================================================================
class SWGMasterNavigator:

    def __init__(self):
        print("--- Initialising Vision Systems ---")
        os.makedirs(DEBUG_DIR, exist_ok=True)
        self.reader = easyocr.Reader(['en'], gpu=True)

        self.m_group    = YOLO(MODELS["group"])
        self.m_panel    = YOLO(MODELS["panel"])
        self.m_comp     = YOLO(MODELS["comp"])
        self.m_arrow    = YOLO(MODELS["arrow"])
        self.m_wp_digit = YOLO(MODELS["wp_digit"])
        self.m_minimap  = YOLO(MODELS["minimap"])
        self.m_coords   = YOLO(MODELS["coords"])
        self.m_co_digit = YOLO(MODELS["co_digit"])

        self.vel_tracker = VelocityTracker()
        self.hug = SimpleHugController(self)

        self.vehicle_speeds  = load_vehicle_speeds()
        self.current_vehicle = "swoop"
        self._apply_vehicle(self.current_vehicle)

        self.registry           = []
        self.autorun_active     = False
        self.creep_mode         = False
        self.last_valid_dist    = 99999
        self.last_dist_time     = time.time()
        self.min_dist_seen      = 99999
        self.target_last_center = None
        self.target_lock_active = False
        self.close_read_count   = 0

        self.player_x    = None
        self.player_y    = None
        self.player_z    = None
        self.coords_time = 0.0

    def _apply_vehicle(self, vehicle_key):
        cfg = self.vehicle_speeds.get(vehicle_key)
        if cfg is None:
            cfg = self.vehicle_speeds.get("swoop", {"max": 16.0})
        self.current_vehicle = vehicle_key
        self.max_speed = cfg["max"]
        self.vel_tracker.reset()
        self.hug.reset()
        print(f"   🏍  Vehicle: {vehicle_key} | max: {self.max_speed} m/s")

    def _read_digits_yolo(self, crop, model, conf=0.3):
        if crop is None or crop.size == 0:
            return None
        results = model.predict(crop, conf=conf, verbose=False)
        if not results or not results[0].boxes:
            return None
        detections = []
        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            cx = float(box.xyxy[0][0])
            label = model.names[cls_id]
            detections.append((cx, label))
        detections.sort(key=lambda x: x[0])
        return "".join(d[1] for d in detections) if detections else None

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
        coord_names = self.m_coords.names
        for box in res_co[0].boxes:
            cls_id = int(box.cls[0])
            field = coord_names[cls_id]
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
            return "unknown"
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        padded = cv2.copyMakeBorder(thresh, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
        results = self.reader.readtext(padded, detail=0, paragraph=False)
        text = "".join(results).strip().lower()
        return text or "unknown"

    def _get_arrow_direction(self, p_crop):
        if p_crop is None or p_crop.size == 0:
            return None
        gray = cv2.cvtColor(p_crop, cv2.COLOR_BGR2GRAY)
        gray_3ch = cv2.merge([gray, gray, gray])
        upscaled = cv2.resize(gray_3ch, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
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

    def _hard_align(self, target_name, required_arrow='N'):
        print(f"   🔄 Realigning to {required_arrow}...")
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
            arrow, _, _ = self.get_target_status(self.get_screen(), target_name)
            if arrow == required_arrow:
                self._key_up(RIGHT_KEY)
                print(f"   ✓ Realigned ({arrow})")
                return True
            if time.time() - start_time > 10:
                self._key_up(RIGHT_KEY)
                print(f"   ⚠️  Align timeout (last arrow: {arrow}), continuing")
                return False
            if arrow != last_arrow:
                print(f"   ... {arrow}", end="\r", flush=True)
                last_arrow = arrow
            time.sleep(0.05)

    def _initial_align(self, target_name):
        print("🧭  Aligning to target...")
        return self._hard_align(target_name, 'N')

    def _do_exploration_sweep(self, target_name):
        """
        Stop, sweep 45° left and 45° right for EXPLORE_HOLD_SECS each,
        measure distance travelled, commit to the better heading, then
        re-align to target.
        """
        print("\n🔍  Exploration sweep starting...")
        self._stop_movement()
        time.sleep(0.3)

        results = {}

        for label, turn_key, return_key in [
            ('LEFT',  LEFT_KEY,  RIGHT_KEY),
            ('RIGHT', RIGHT_KEY, LEFT_KEY),
        ]:
            if check_kill():
                self.shutdown(); sys.exit(0)

            # Turn ~45°
            self._tap(turn_key, duration=EXPLORE_TURN_SECS)
            time.sleep(0.2)

            # Record start position
            frame = self.get_screen()
            sx, sy, sz = self._update_player_coords(frame)
            start_pos = (sx, sz) if sx is not None and sz is not None else None

            # Run forward for EXPLORE_HOLD_SECS
            self._start_autorun()
            t_end = time.time() + EXPLORE_HOLD_SECS
            while time.time() < t_end:
                if check_kill():
                    self.shutdown(); sys.exit(0)
                frame = self.get_screen()
                self._update_player_coords(frame)
                time.sleep(0.1)

            # Record end position
            frame = self.get_screen()
            ex, ey, ez = self._update_player_coords(frame)
            end_pos = (ex, ez) if ex is not None and ez is not None else None

            self._stop_movement()

            if start_pos and end_pos:
                dist = math.hypot(end_pos[0] - start_pos[0], end_pos[1] - start_pos[1])
            else:
                dist = 0.0
            results[label] = dist
            print(f"   Sweep {label}: {dist:.1f}m")

            # Return to roughly original heading
            self._tap(return_key, duration=EXPLORE_TURN_SECS)
            time.sleep(0.2)

        best = max(results, key=results.get)
        print(f"   Best heading: {best} ({results[best]:.1f}m) — realigning to target")

        # Bias initial turn toward the winning side before hard-aligning
        bias_key = LEFT_KEY if best == 'LEFT' else RIGHT_KEY
        self._tap(bias_key, duration=EXPLORE_TURN_SECS)
        time.sleep(0.2)

        self._hard_align(target_name, 'N')
        self._start_autorun()
        self.hug.arm()

    def _dismount(self):
        print("   🏇 Dismounting vehicle...")
        if DISMOUNT_KEY:
            self._tap(DISMOUNT_KEY, duration=0.1)
            time.sleep(1.0)
            self._apply_vehicle("running")
            print("   ✓ Dismounted — switched to running profile")
        else:
            print("   ⚠️  DISMOUNT_KEY not set")
            time.sleep(1.0)

    def shutdown(self):
        print("\n🧹 Shutting down...")
        self._stop_movement()
        self._release_all()
        print("✓ All keys released")

    # =========================================================================
    # MAIN NAV LOOP
    # =========================================================================
    def navigate(self, selection_index):
        target = self.registry[selection_index]
        target_name = target['name']
        print(f"\n🎯  Target: '{target_name}' ({target['distance']}m)")
        print(f"   Vehicle: {self.current_vehicle} (max ~{self.max_speed:.1f} m/s)")
        print(f"   Hysteresis: {int(LOW_RATIO*100)}% escape / {int(HIGH_RATIO*100)}% approach / {int(GAP_RATIO*100)}% gap-detect")
        print(f"⏳  Resuming in {TAB_IN_PAUSE}s...")
        time.sleep(TAB_IN_PAUSE)

        self._initial_align(target_name)

        self.last_valid_dist = target['distance']
        self.min_dist_seen = target['distance']
        self.last_dist_time = time.time()
        self.close_read_count = 0
        self.hug.full_reset()  # clear position memory and frustration for new target

        self._start_autorun()
        self.hug.arm()

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
                self.last_dist_time = time.time()
                if dist < self.min_dist_seen:
                    self.min_dist_seen = dist

                # Frustration decay check — clears index if 75m progress made
                self.hug.notify_dist(dist)

                is_close = dist <= CLOSE_RANGE_THRESHOLD

                if dist <= STOP_DIST:
                    self.close_read_count += 1
                    status_marker = f" ✓{self.close_read_count}/{VERIFY_READINGS}"
                else:
                    self.close_read_count = 0
                    status_marker = ""

                coord_str = f"({px:.0f},{pz:.0f})" if px is not None and pz is not None else "(?,?)"
                spd = self.vel_tracker.current_speed()
                spd_str = f"{spd:.1f}" if spd is not None else "?"
                ratio = (spd / self.max_speed * 100) if spd is not None else 0
                print(f"\r[{self.hug.get_status():>10}] Dir:{arrow or '---':>3} | Dist:{dist:>5}m | "
                      f"Min:{self.min_dist_seen:>4}m | Spd:{spd_str}m/s ({ratio:.0f}%) | "
                      f"Pos:{coord_str}{status_marker}",
                      end="", flush=True)

                # === ARRIVAL ===
                if self.close_read_count >= VERIFY_READINGS:
                    self._stop_movement()
                    print(f"\n\n🏁  SUCCESS!")
                    self.shutdown()
                    sys.exit(0)

                # Hard align when close and misaligned
                if is_close and arrow and arrow not in ACCEPTABLE_DIRECTIONS:
                    print(f"\n⚠️  Misaligned ({arrow}) near waypoint...")
                    self._hard_align(target_name, 'N')
                    self._start_autorun()
                    self.hug.arm()
                    continue

                # Creep mode
                if dist <= FINAL_CREEP_DIST:
                    if not self.creep_mode:
                        print(f"\n🐢  Final approach ({dist}m)...")
                        self._stop_movement()
                        self.creep_mode = True
                    if arrow and arrow not in ACCEPTABLE_DIRECTIONS:
                        self._hard_align(target_name, 'N')
                    self._do_creep_pulse()
                    continue
                else:
                    if self.creep_mode:
                        self._stop_movement()
                        self._start_autorun()
                        self.hug.arm()

                # =========================================================
                # DISCRETE STEERING TAPS
                # =========================================================
                cur_pos = (px, pz) if px is not None and pz is not None else None
                steer = self.hug.tick(spd, arrow, current_pos=cur_pos)

                if steer == 'EXPLORE':
                    self._do_exploration_sweep(target_name)
                    continue
                elif steer == 'REALIGN':
                    self._stop_movement()
                    self._hard_align(target_name, 'N')
                    self._start_autorun()
                    self.hug.arm()
                    continue
                elif steer == -1:
                    self._tap(LEFT_KEY, 0.05)
                elif steer == 1:
                    self._tap(RIGHT_KEY, 0.05)

                if not self.autorun_active:
                    self._start_autorun()

            else:
                # Lost waypoint
                lost = time.time() - self.last_dist_time
                if lost > OCR_STALE_LIMIT:
                    print(f"\n⚠️  Lost for {lost:.0f}s, searching...")
                    self._stop_movement()
                    self.hug.reset()
                    self._key_down(RIGHT_KEY)
                    found = False
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
                        self.hug.arm()
                    else:
                        print(f"   ✗ Not found, full reset...")
                        self.target_lock_active = False
                        self.close_read_count = 0
                        self._initial_align(target_name)
                        self._start_autorun()
                        self.hug.arm()

            time.sleep(NAV_FREQ)


# =============================================================================
if __name__ == "__main__":
    nav = SWGMasterNavigator()

    print("\n--- VEHICLE SELECT ---")
    vehicles = list(nav.vehicle_speeds.keys())
    for i, v in enumerate(vehicles):
        cfg = nav.vehicle_speeds[v]
        print(f"  {i}: {v:<12} max: {cfg['max']} m/s")
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
