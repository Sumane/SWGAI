"""
speedometer.py — Calibrate max speeds per vehicle/mode for SWGEmu bot

Usage:
    python speedometer.py --mode walking
    python speedometer.py --mode speeder
    python speedometer.py --mode swoop

Press SPACE to start recording, run in a straight line for 5+ seconds,
press SPACE again to stop. Reports max/average speed.
"""
import cv2
import numpy as np
import mss
import time
import argparse
import json
import math
import os
import sys
from ultralytics import YOLO
from collections import deque

# Windows-only built-in keyboard check (no extra deps, works in PS/CMD)
if sys.platform == "win32":
    import msvcrt
else:
    msvcrt = None

# Same models as navigation
MODELS = {
    "minimap":  r"E:\SWGEMU_bot\jedai\models\mini_map_v01.00\weights\best.pt",
    "coords":   r"E:\SWGEMU_bot\jedai\models\player_coord_v1.01\weights\best.pt",
    "co_digit": r"E:\SWGEMU_bot\jedai\models\white_on_teal_digits_v01.00\weights\best.pt",
}

MONITOR_INDEX = 2
SAMPLE_WINDOW = 30  # frames for rolling average
MIN_SAMPLES = 20


def space_pressed():
    """Non-blocking check for spacebar. Windows console only."""
    if msvcrt and msvcrt.kbhit():
        key = msvcrt.getch()
        if key == b' ':
            return True
    return False


class Speedometer:
    def __init__(self):
        self.m_minimap = YOLO(MODELS["minimap"])
        self.m_coords = YOLO(MODELS["coords"])
        self.m_co_digit = YOLO(MODELS["co_digit"])
        
        self.positions = deque(maxlen=SAMPLE_WINDOW)
        self.timestamps = deque(maxlen=SAMPLE_WINDOW)
        self.speeds = []
        
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
    
    def _read_coords(self, frame):
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
        
        return (
            coord_values.get('x_pos'),
            coord_values.get('y_pos'),
            coord_values.get('z_pos')
        )
    
    def calculate_speed(self):
        if len(self.positions) < 2:
            return 0.0
        
        # FIXED: Use X and Z for ground-plane distance (Y is height)
        x1, z1 = self.positions[0]
        x2, z2 = self.positions[-1]
        t1 = self.timestamps[0]
        t2 = self.timestamps[-1]
        
        dt = t2 - t1
        if dt < 0.1:
            return 0.0
        
        dist = math.hypot(x2 - x1, z2 - z1)
        speed = dist / dt  # metres per second
        return speed
    
    def calibrate(self, mode_name):
        print(f"\n{'='*50}")
        print(f"  SPEEDOMETER — Calibrating: {mode_name.upper()}")
        print(f"{'='*50}")
        print("\nInstructions:")
        print("  1. Get on your vehicle / start walking")
        print("  2. Face open terrain (no obstacles)")
        print("  3. Press SPACE to START recording")
        print("  4. Run in a straight line for 5+ seconds")
        print("  5. Press SPACE to STOP")
        print(f"\n  Reading coords every 0.1s, window = {SAMPLE_WINDOW} frames")
        print(f"  Need {MIN_SAMPLES} valid readings minimum\n")
        
        recording = False
        waiting_for_start = True
        
        while waiting_for_start:
            frame = self.get_screen()
            x, y, z = self._read_coords(frame)
            
            status = "WAITING FOR SPACE..." if not recording else "RECORDING..."
            # FIXED: Display X/Z instead of X/Y
            coords = f"({x:.1f}, {z:.1f})" if x is not None else "(no coords)"
            speed = self.calculate_speed()
            
            print(f"\r  [{status}] Coords: {coords} | Speed: {speed:5.1f} m/s | Samples: {len(self.positions)}", 
                  end="", flush=True)
            
            # FIXED: Use msvcrt spacebar check instead of cv2.waitKey
            if space_pressed():
                if not recording:
                    recording = True
                    self.positions.clear()
                    self.timestamps.clear()
                    self.speeds = []
                    print("\n\n  >>> RECORDING STARTED <<<\n")
                else:
                    waiting_for_start = False
                    print("\n\n  >>> RECORDING STOPPED <<<\n")
            
            # FIXED: Store X and Z (ground plane), ignore Y (height)
            if recording and x is not None and z is not None:
                self.positions.append((x, z))
                self.timestamps.append(time.time())
                self.speeds.append(speed)
            
            time.sleep(0.1)
        
        # Analysis
        if len(self.speeds) < MIN_SAMPLES:
            print(f"  ⚠️  Only {len(self.speeds)} samples — need {MIN_SAMPLES}")
            return None
        
        valid_speeds = [s for s in self.speeds if s > 0.1]
        if not valid_speeds:
            print("  ⚠️  No valid speed readings")
            return None
        
        max_speed = max(valid_speeds)
        avg_speed = sum(valid_speeds) / len(valid_speeds)
        p95_speed = sorted(valid_speeds)[int(len(valid_speeds) * 0.95)]
        
        print(f"  Results for {mode_name}:")
        print(f"    Max speed:    {max_speed:.2f} m/s")
        print(f"    Avg speed:    {avg_speed:.2f} m/s")
        print(f"    95th percentile: {p95_speed:.2f} m/s")
        print(f"    Samples:      {len(valid_speeds)}")
        print(f"\n  Recommended NAV constant:")
        print(f"    '{mode_name}': {p95_speed:.2f}")
        
        return {
            'max': max_speed,
            'avg': avg_speed,
            'p95': p95_speed,
            'samples': len(valid_speeds)
        }


def main():
    parser = argparse.ArgumentParser(description='Calibrate SWGEmu movement speeds')
    parser.add_argument('--mode', choices=['walking', 'running', 'speeder', 'swoop', 'vehicle'],
                        default='walking', help='Movement mode to calibrate')
    parser.add_argument('--save', action='store_true', help='Save to speed_config.json')
    args = parser.parse_args()
    
    spd = Speedometer()
    result = spd.calibrate(args.mode)
    
    if result and args.save:
        config_path = r"E:\SWGEMU_bot\jedai\config\speed_config.json"
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            config = {}
        
        config[args.mode] = result
        
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"\n  ✓ Saved to {config_path}")
    
    print("\n  Done.")


if __name__ == "__main__":
    main()

# run: python "E:\SWGEMU_bot\jedai\Scripts\movement\speed detection and logic\speedometer.py"