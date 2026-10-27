import cv2
import torch
import numpy as np
import mss
import time
import os
import sys
import json

# --- PATH INJECTOR ---
SCRIPT_DIR = r"E:\SWGEMU_bot\jedai\Scripts"
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

try:
    from depth_anything_v2.dpt import DepthAnythingV2
except ModuleNotFoundError:
    print("CRITICAL: 'depth_anything_v2' folder not found in Scripts directory!")
    sys.exit()

# =============================================================================
# DEPTH PERCEPTION MODULE
# Runs independently at ~2fps. Writes obstacle data to depth_bridge.json.
# Nav script reads the bridge and decides whether to yield control.
#
# IMPORTANT: Does NOT touch nav_state.json (the command bus).
# Reads current nav arrow from depth_bridge.json['nav_arrow'] — written there
# by the nav script each loop tick.
# =============================================================================

MODEL_CONFIG = {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]}
MODEL_PATH   = r"E:\SWGEMU_bot\jedai\models\depth_anything_v2_vits.pth"
DEVICE       = 'cuda' if torch.cuda.is_available() else 'cpu'
BRIDGE_FILE  = "depth_bridge.json"
MONITOR_IDX  = 2

# Depth thresholds
VETO_THRESHOLD  = 185   # c_val above this = wall / hard obstacle ahead
CLEAR_THRESHOLD = 140   # L/R sector below this = clear path

# ~2fps — depth inference is expensive, no need to run faster
LOOP_SLEEP = 0.5

# =============================================================================
class DepthController:

    def __init__(self):
        print(f"--- Initializing Depth Controller on {DEVICE} ---")
        self.model = DepthAnythingV2(**MODEL_CONFIG)
        self.model.load_state_dict(torch.load(MODEL_PATH, map_location='cpu'))
        self.model.to(DEVICE).eval()

        self.sct     = mss.mss()
        self.monitor = self.sct.monitors[MONITOR_IDX]

        # Write an initial bridge file so nav script detects the module immediately
        self._write_bridge({
            "c_danger":       0,
            "veto_forward":   False,
            "suggested_bias": "LEFT",
            "nav_arrow":      "N",
            "timestamp":      time.time(),
        })
        print(f"Bridge file initialised: {os.path.abspath(BRIDGE_FILE)}")

    def _write_bridge(self, data):
        try:
            with open(BRIDGE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[DEPTH] Bridge write error: {e}")

    def _read_nav_arrow(self):
        """
        Reads current nav arrow from bridge file.
        Nav script writes 'nav_arrow' into the bridge each tick.
        Falls back to 'N' if not available.
        """
        try:
            with open(BRIDGE_FILE, "r") as f:
                data = json.load(f)
            return data.get("nav_arrow", "N")
        except:
            return "N"

    def run(self):
        print("Depth Controller Active. Bridging to Navigation...")
        print(f"VETO threshold: {VETO_THRESHOLD} | Clear threshold: {CLEAR_THRESHOLD}")
        print("Press Q in the visualiser window to quit.\n")

        while True:
            t_start = time.time()

            # 1. Capture & inference
            sct_img     = self.sct.grab(self.monitor)
            frame       = cv2.cvtColor(np.array(sct_img), cv2.COLOR_BGRA2RGB)
            input_frame = cv2.resize(frame, (518, 518))

            with torch.no_grad():
                depth = self.model.infer_image(input_frame)

            # 2. Normalise to 0-255
            d_min, d_max = depth.min(), depth.max()
            if d_max - d_min < 1e-6:
                # Flat depth map (e.g. loading screen) — treat as no obstacle
                self._write_bridge({
                    "c_danger":       0,
                    "veto_forward":   False,
                    "suggested_bias": "LEFT",
                    "nav_arrow":      self._read_nav_arrow(),
                    "timestamp":      time.time(),
                })
                time.sleep(LOOP_SLEEP)
                continue

            depth_viz = ((depth - d_min) / (d_max - d_min) * 255).astype(np.uint8)
            h, w      = depth_viz.shape

            # 3. Horizon band — ignore UI (bottom) and sky (top)
            v_start = int(h * 0.30)
            v_end   = int(h * 0.75)

            # 4. Three sectors with side margins
            margin   = int(w * 0.10)
            usable_w = w - (2 * margin)
            sec_w    = usable_w // 3

            l_zone    = depth_viz[v_start:v_end, margin : margin + sec_w]
            c_zone_raw = depth_viz[v_start:v_end, margin + sec_w : margin + 2 * sec_w]
            r_zone    = depth_viz[v_start:v_end, margin + 2 * sec_w : w - margin]

            # Mask player character in centre zone (inner 50% of the centre slice)
            c_h, c_w  = c_zone_raw.shape
            c_zone    = c_zone_raw.copy()
            c_zone[:, c_w // 4 : 3 * c_w // 4] = 0

            l_val = float(np.mean(l_zone))
            c_val = float(np.mean(c_zone))
            r_val = float(np.mean(r_zone))

            # 5. Waypoint-biased steering decision
            #    Read nav arrow from the bridge (written by nav script)
            current_arrow = self._read_nav_arrow()
            veto          = c_val > VETO_THRESHOLD

            # Default: steer toward deepest (highest value = furthest away)
            if l_val < r_val:
                # Right is deeper
                bias = "RIGHT"
                # But if nav arrow says go left and left is clear, respect it
                if current_arrow in ("NW", "W") and l_val < CLEAR_THRESHOLD:
                    bias = "LEFT"
            else:
                # Left is deeper
                bias = "LEFT"
                # But if nav arrow says go right and right is clear, respect it
                if current_arrow in ("NE", "E") and r_val < CLEAR_THRESHOLD:
                    bias = "RIGHT"

            # 6. Write bridge
            bridge_data = {
                "c_danger":       int(c_val),
                "veto_forward":   bool(veto),
                "suggested_bias": bias,
                "nav_arrow":      current_arrow,   # Echo back for continuity
                "timestamp":      time.time(),
            }
            self._write_bridge(bridge_data)

            # 7. Visualiser
            viz_color    = cv2.applyColorMap(depth_viz, cv2.COLORMAP_INFERNO)
            status_color = (0, 0, 255) if veto else (0, 255, 0)

            # Draw horizon band
            cv2.rectangle(viz_color, (margin, v_start), (w - margin, v_end), (255, 255, 255), 1)
            # Draw sector dividers
            cv2.line(viz_color, (margin + sec_w, v_start),     (margin + sec_w, v_end),     (200, 200, 200), 1)
            cv2.line(viz_color, (margin + 2 * sec_w, v_start), (margin + 2 * sec_w, v_end), (200, 200, 200), 1)

            cv2.putText(viz_color, f"VETO:{veto} BIAS:{bias} ARW:{current_arrow}",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2)
            cv2.putText(viz_color, f"L:{l_val:.0f}  C:{c_val:.0f}  R:{r_val:.0f}",
                        (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)

            elapsed = time.time() - t_start
            cv2.putText(viz_color, f"{1/elapsed:.1f}fps" if elapsed > 0 else "",
                        (w - 70, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

            cv2.imshow('Depth Controller Bridge', viz_color)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            # Throttle to ~2fps — sleep remainder of 0.5s budget
            elapsed = time.time() - t_start
            sleep_remaining = LOOP_SLEEP - elapsed
            if sleep_remaining > 0:
                time.sleep(sleep_remaining)

        cv2.destroyAllWindows()
        print("\n[DEPTH] Shutting down.")
        # Write a final clear state so nav doesn't get stuck thinking there's an obstacle
        self._write_bridge({
            "c_danger":       0,
            "veto_forward":   False,
            "suggested_bias": "LEFT",
            "nav_arrow":      "N",
            "timestamp":      0,   # Timestamp 0 = immediately stale, nav ignores it
        })


if __name__ == "__main__":
    DepthController().run()

# run: python "E:\SWGEMU_bot\jedai\Scripts\movement\depth_perception.py"
