import cv2
import easyocr
import numpy as np
import re
import time
import pydirectinput
import mss
import keyboard
import sys
import os
import random
from collections import deque
from datetime import datetime
from difflib import SequenceMatcher
from ultralytics import YOLO

# =============================================================================
# CONFIG
# =============================================================================
MODELS = {
    "group": r"E:\SWGEMU_bot\jedai\models\wp_group_v0101\weights\best.pt",
    "panel": r"E:\SWGEMU_bot\jedai\models\wp_panel_v2_production3\weights\best.pt",
    "comp":  r"E:\SWGEMU_bot\jedai\models\wp_components_v3\weights\best.pt",
    "arrow": r"E:\SWGEMU_bot\jedai\models\wp_direction_v2_final\weights\best.pt",
}

MONITOR_INDEX   = 2
NAV_FREQ        = 0.1       
AUTORUN_KEY     = 'numlock'
KILL_KEY        = 'f8'
LEFT_KEY        = 'left'
RIGHT_KEY       = 'right'
FORWARD_KEY     = 'up'
BACK_KEY        = 'down'

STOP_DIST       = 50        # Success threshold: 50m
VERIFY_READINGS = 5         # Need 5 consecutive readings <= 50m
VERIFY_TIMEOUT  = 10        # Max 10 seconds to get those 3 readings

CREEP_DIST      = 60        
FINAL_CREEP_DIST = 20       # Extra careful in final 20m
CREEP_PULSE_ON  = 0.8       
CREEP_PULSE_OFF = 0.15      

SCAN_STEPS      = 4
SCAN_TURN_TIME  = 0.9
SCAN_SETTLE     = 0.4

FUZZY_THRESHOLD = 0.5
OCR_STALE_LIMIT = 15.0      
CLOSE_RANGE_THRESHOLD = 25   # Wider close range for alignment checks

ACCEPTABLE_DIRECTIONS = ('N', 'NW', 'NE')
AGGRESSIVE_TURN_TIME = 0.3

TAB_IN_PAUSE    = 4
DEBUG_DIR       = r"E:\label_work\debug_logs"

# UNSTICK CONFIG
STUCK_THRESHOLD = 10.0      # Increased slightly for lag tolerance (was 8.0)
UNSTICK_COOLDOWN = 3.0      
PROGRESS_BUFFER = 8         # Must change by 8m to count as progress (lag tolerance)

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
    """Check if kill key is pressed."""
    return keyboard.is_pressed(KILL_KEY)

# =============================================================================
class SWGMasterNavigator:

    def __init__(self):
        print("--- Initialising Vision Systems ---")
        os.makedirs(DEBUG_DIR, exist_ok=True)

        self.reader  = easyocr.Reader(['en'], gpu=True)
        self.m_group = YOLO(MODELS["group"])
        self.m_panel = YOLO(MODELS["panel"])
        self.m_comp  = YOLO(MODELS["comp"])
        self.m_arrow = YOLO(MODELS["arrow"])

        self.registry = []
        self.autorun_active = False
        self.creep_mode = False
        self.last_valid_dist = 99999
        self.last_dist_time = time.time()
        self.min_dist_seen = 99999
        
        self.target_last_center = None
        self.target_lock_active = False
        self.close_read_count = 0

        # Unstick tracking
        self.last_dist_reading = 99999    # Last distance value seen
        self.last_progress_time = time.time()
        self.unstick_cooldown = 0

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

    def run_ocr(self, crop, is_dist=False):
        if crop is None or crop.size == 0:
            return None
        
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        padded = cv2.copyMakeBorder(thresh, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)
        
        results = self.reader.readtext(padded, detail=0, paragraph=False)
        text = "".join(results)
        
        if is_dist:
            text = text.replace('m', '').replace('M', '').replace(' ', '')
            text = text.replace('O', '0').replace('o', '0').replace('l', '1').replace('I', '1')
            text = text.replace('S', '5').replace('s', '5').replace('B', '8')
            
            match = re.search(r'(\d+)', text)
            if match:
                try:
                    val = int(match.group(1))
                    if 0 < val < 5000:
                        return val
                except:
                    pass
            return None
        
        return text.strip().lower() if text else "unknown"

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
                            name = self.run_ocr(c_crop, is_dist=False) or "unknown"
                        else:
                            dist = self.run_ocr(c_crop, is_dist=True)

                direction = self._get_arrow_direction(p_crop)
                yield name, dist, (pcx, pcy), direction

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
                        'name': name,
                        'distance': dist,
                        'screen_pos': (cx, cy),
                        'direction': direction
                    })
            
            pydirectinput.keyDown(RIGHT_KEY)
            time.sleep(SCAN_TURN_TIME)
            pydirectinput.keyUp(RIGHT_KEY)
            time.sleep(SCAN_SETTLE)

    def get_target_status(self, frame, target_name):
        candidates = []
        
        for name, dist, (cx, cy), direction in self._extract_waypoint_data(frame):
            if check_kill():
                return None, None, None
            
            if fuzzy_match(target_name, name):
                candidates.append({
                    'dist': dist,
                    'pos': (cx, cy),
                    'direction': direction,
                    'dist_diff': abs(dist - self.last_valid_dist) if dist else 99999
                })
        
        if not candidates:
            return None, None, None
        
        candidates.sort(key=lambda x: x['dist_diff'])
        best = candidates[0]
        
        self.target_last_center = best['pos']
        self.target_lock_active = True
        
        return best['direction'], best['dist'], best['pos']

    def _stop_movement(self):
        if self.autorun_active:
            pydirectinput.press(AUTORUN_KEY)
            self.autorun_active = False
        pydirectinput.keyUp(FORWARD_KEY)
        self.creep_mode = False

    def _start_autorun(self):
        if not self.autorun_active:
            pydirectinput.press(AUTORUN_KEY)
            self.autorun_active = True
        self.creep_mode = False

    def _do_creep_pulse(self):
        pydirectinput.keyDown(FORWARD_KEY)
        time.sleep(CREEP_PULSE_ON)
        pydirectinput.keyUp(FORWARD_KEY)
        time.sleep(CREEP_PULSE_OFF)

    def _hard_align(self, target_name, required_arrow='N'):
        """Aggressive alignment with kill key check."""
        print(f"   🔄 Realigning to {required_arrow}...")
        self._stop_movement()
        time.sleep(0.2)
        
        pydirectinput.keyDown(RIGHT_KEY)
        arrow = None
        start_time = time.time()
        
        while arrow != required_arrow:
            if check_kill():
                print("\n🛑 Kill key pressed during alignment")
                pydirectinput.keyUp(RIGHT_KEY)
                self.shutdown()
                sys.exit(0)
            
            time.sleep(0.05)
            arrow, _, _ = self.get_target_status(self.get_screen(), target_name)
            
            if time.time() - start_time > 10:
                print("   ⚠️ Align timeout, retrying...")
                start_time = time.time()
        
        pydirectinput.keyUp(RIGHT_KEY)
        print(f"   ✓ Realigned ({arrow})")
        return True

    def _check_stuck(self, dist):
        """Returns True if we haven't made progress recently."""
        if dist is None or not self.autorun_active:
            return False
        
        # If distance changed significantly from LAST reading (up or down), 
        # we're making progress. This handles lag spikes where distance 
        # temporarily increases due to rubber-banding.
        if abs(dist - self.last_dist_reading) > PROGRESS_BUFFER:
            self.last_dist_reading = dist
            if dist < self.min_dist_seen:
                self.min_dist_seen = dist
            self.last_progress_time = time.time()
            return False
        
        # Check if we've been stagnant too long
        stagnant_time = time.time() - self.last_progress_time
        
        if stagnant_time > STUCK_THRESHOLD and time.time() > self.unstick_cooldown:
            print(f"\n🚨  STUCK DETECTED! No progress for {stagnant_time:.1f}s (last: {self.last_dist_reading}m, curr: {dist}m)")
            return True
        
        return False

    def _unstick_maneuver(self):
        """Execute unstuck routine."""
        print("   🔄 Executing unstick maneuver...")
        
        # Stop first
        self._stop_movement()
        time.sleep(0.3)
        
        # Randomize direction to break collision
        directions = [
            (LEFT_KEY, 0.6),
            (RIGHT_KEY, 0.6),
            (BACK_KEY, 0.8),
            ((BACK_KEY, LEFT_KEY), 0.4),
            ((BACK_KEY, RIGHT_KEY), 0.4),
        ]
        
        move, duration = random.choice(directions)
        
        # Execute move
        if isinstance(move, tuple):
            pydirectinput.keyDown(move[0])
            pydirectinput.keyDown(move[1])
            time.sleep(duration)
            pydirectinput.keyUp(move[1])
            pydirectinput.keyUp(move[0])
        else:
            pydirectinput.keyDown(move)
            time.sleep(duration)
            pydirectinput.keyUp(move)
        
        print("   ✓ Unstick complete, resuming...")
        self.unstick_cooldown = time.time() + UNSTICK_COOLDOWN
        
        # CRITICAL: Reset baseline so we get fresh 10s to make progress
        self.last_progress_time = time.time()
        self.last_dist_reading = 99999  # Force reset on next valid reading

    def _initial_align(self, target_name):
        print("🧭  Aligning to target...")
        return self._hard_align(target_name, 'N')

    def shutdown(self):
        print("\n🧹 Shutting down...")
        self._stop_movement()
        for k in (LEFT_KEY, RIGHT_KEY, FORWARD_KEY, BACK_KEY):
            pydirectinput.keyUp(k)
        print("✓ All keys released")

    def navigate(self, selection_index):
        target = self.registry[selection_index]
        target_name = target['name']
        print(f"\n🎯  Target: '{target_name}' ({target['distance']}m)")
        print(f"   Verify: {VERIFY_READINGS} consecutive readings ≤ {STOP_DIST}m")

        print(f"⏳  Resuming in {TAB_IN_PAUSE}s...")
        time.sleep(TAB_IN_PAUSE)

        self._initial_align(target_name)
        
        self._start_autorun()
        self.last_valid_dist = target['distance']
        self.min_dist_seen = target['distance']
        self.last_dist_time = time.time()
        self.close_read_count = 0
        
        # Initialize unstick tracking
        self.last_dist_reading = target['distance']
        self.last_progress_time = time.time()
        self.unstick_cooldown = 0

        while True:
            if check_kill():
                print("\n🛑 Kill key pressed - stopping")
                self.shutdown()
                sys.exit(0)

            frame = self.get_screen()
            arrow, dist, pos = self.get_target_status(frame, target_name)
            
            if dist is not None:
                self.last_valid_dist = dist
                self.last_dist_time = time.time()
                
                # === UNSTICK LOGIC ===
                if self._check_stuck(dist):
                    self._unstick_maneuver()
                    self._start_autorun()
                    continue
                # === END UNSTICK ===
                
                if dist < self.min_dist_seen:
                    self.min_dist_seen = dist
                
                is_close = dist <= CLOSE_RANGE_THRESHOLD
                
                # Consecutive close reading logic
                if dist <= STOP_DIST:
                    self.close_read_count += 1
                    status_marker = f" ✓{self.close_read_count}/{VERIFY_READINGS}"
                else:
                    self.close_read_count = 0
                    status_marker = ""
                
                mode = "CREEP" if self.creep_mode else "RUN "
                status = "CLOSE" if is_close else "    "
                print(f"\r[{mode}] {status} Dir: {arrow or '---':>3} | Dist: {dist:>5}m | Min:{self.min_dist_seen:>4}m{status_marker}", end="", flush=True)
                
                # === VERIFIED SUCCESS ===
                if self.close_read_count >= VERIFY_READINGS:
                    self._stop_movement()
                    print(f"\n\n🏁  SUCCESS! Verified {VERIFY_READINGS} consecutive readings ≤ {STOP_DIST}m")
                    print(f"   Final distance: {dist}m (minimum seen: {self.min_dist_seen}m)")
                    self.shutdown()
                    sys.exit(0)
                
                # Hard align when close and misaligned
                if is_close and arrow and arrow not in ACCEPTABLE_DIRECTIONS:
                    print(f"\n⚠️  Misaligned ({arrow}) near waypoint...")
                    self._hard_align(target_name, 'N')
                    continue
                
                # Creep mode for final approach
                if dist <= FINAL_CREEP_DIST:
                    if not self.creep_mode:
                        print(f"\n🐢  Final approach ({dist}m)...")
                        self._stop_movement()
                        self.creep_mode = True
                    
                    if arrow and arrow not in ACCEPTABLE_DIRECTIONS:
                        print(f"\n⚠️  Creep misaligned ({arrow})...")
                        self._hard_align(target_name, 'N')
                    
                    self._do_creep_pulse()
                    continue
                else:
                    if self.creep_mode:
                        self._stop_movement()
                        self._start_autorun()
                
                # Normal steering
                if self.autorun_active and arrow:
                    if arrow in ('W', 'SW'):
                        pydirectinput.keyDown(LEFT_KEY); time.sleep(0.2); pydirectinput.keyUp(LEFT_KEY)
                    elif arrow in ('E', 'SE'):
                        pydirectinput.keyDown(RIGHT_KEY); time.sleep(0.2); pydirectinput.keyUp(RIGHT_KEY)
                    elif arrow == 'NW':
                        pydirectinput.keyDown(LEFT_KEY); time.sleep(0.1); pydirectinput.keyUp(LEFT_KEY)
                    elif arrow == 'NE':
                        pydirectinput.keyDown(RIGHT_KEY); time.sleep(0.1); pydirectinput.keyUp(RIGHT_KEY)
                
                if not self.autorun_active:
                    self._start_autorun()
                    
            else:
                # Lost detection
                lost = time.time() - self.last_dist_time
                
                if lost > OCR_STALE_LIMIT:
                    print(f"\n⚠️  Lost for {lost:.0f}s, searching...")
                    self._stop_movement()
                    
                    pydirectinput.keyDown(RIGHT_KEY)
                    found = False
                    search_start = time.time()
                    
                    while time.time() - search_start < 6.0:
                        if check_kill():
                            print("\n🛑 Kill key pressed during search")
                            pydirectinput.keyUp(RIGHT_KEY)
                            self.shutdown()
                            sys.exit(0)
                        
                        time.sleep(0.1)
                        a, d, _ = self.get_target_status(self.get_screen(), target_name)
                        if d:
                            found = True
                            break
                    
                    pydirectinput.keyUp(RIGHT_KEY)
                    
                    if found:
                        print(f"   ✓ Reacquired at {d}m")
                        self._start_autorun()
                        self.last_progress_time = time.time()
                        self.last_dist_reading = d  # Reset baseline
                        self.close_read_count = 0
                    else:
                        print(f"   ✗ Not found, full reset...")
                        self.target_lock_active = False
                        self.close_read_count = 0
                        self._initial_align(target_name)
                        self._start_autorun()
                        self.last_progress_time = time.time()
                        self.last_dist_reading = 99999

            time.sleep(NAV_FREQ)


if __name__ == "__main__":
    nav = SWGMasterNavigator()
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
 
# run: python "E:\SWGEMU_bot\jedai\Scripts\movement\navigation_v0.01.py"