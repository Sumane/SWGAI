"""
Manual check of the waypoint cascade (group -> panel -> components) plus
colour-isolated Tesseract OCR on random screenshots. Saves the binarised
crops and a results file per screenshot for you to eyeball.

    python tools/validation/test_hud_ocr.py --source <screenshots> --samples 5

Needs pytesseract and Tesseract (set tools.tesseract_cmd in config).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import os
import cv2
import torch
import re
import random
import warnings
import numpy as np
from ultralytics import YOLO
import pytesseract

# --- 1. SETTINGS ---
warnings.filterwarnings("ignore")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

from tools.common import data_dir, model_weights, settings

if settings.raw.get('tools', {}).get('tesseract_cmd'):
    pytesseract.pytesseract.tesseract_cmd = settings.raw['tools']['tesseract_cmd']

MODELS = {k: str(model_weights(k)) for k in ("group", "panel", "comp")}

SOURCE_DIR = None                        # set from --source
TEST_DIR = str(data_dir("validation"))   # results go here

# Color Bounds (HSV) - These target the specific text colors in SWG
# Green (Distances):
LOWER_GREEN = np.array([35, 50, 50])
UPPER_GREEN = np.array([90, 255, 255])
# Cyan/Blue (Descriptions):
LOWER_CYAN = np.array([80, 50, 50])
UPPER_CYAN = np.array([130, 255, 255])

# --- 2. INIT ---
print("🔄 Loading Models...")
m_group = YOLO(MODELS["group"]).to(device)
m_panel = YOLO(MODELS["panel"]).to(device)
m_comp = YOLO(MODELS["comp"]).to(device)

def get_binary_text(crop, is_dist):
    """Isolates colored pixels and turns them into clean White-on-Black."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    
    if is_dist:
        mask = cv2.inRange(hsv, LOWER_GREEN, UPPER_GREEN)
    else:
        mask = cv2.inRange(hsv, LOWER_CYAN, UPPER_CYAN)
    
    # Optional: Slightly thicken the font if it's too thin for Tesseract
    kernel = np.ones((2,2), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)
    
    # Add a 10px black border margin (Tesseract hates text touching edges)
    return cv2.copyMakeBorder(mask, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=0)

def run_validation(num_samples=5):
    if not os.path.exists(SOURCE_DIR):
        print(f"❌ ERROR: Source directory not found: {SOURCE_DIR}")
        return

    all_files = [f for f in os.listdir(SOURCE_DIR) if f.lower().endswith(('.png', '.jpg'))]
    print(f"📂 Found {len(all_files)} images in source.")
    
    samples = random.sample(all_files, min(num_samples, len(all_files)))
    if not samples:
        print("❓ No samples selected. Check your SOURCE_DIR.")
        return

    for filename in samples:
        print(f"📸 Processing {filename}...")
        folder_name = os.path.splitext(filename)[0]
        sub_dir = os.path.join(TEST_DIR, folder_name)
        if not os.path.exists(sub_dir): os.makedirs(sub_dir)

        img = cv2.imread(os.path.join(SOURCE_DIR, filename))
        
        results_log = []
        res_g = m_group.predict(img, conf=0.7, verbose=False)[0]

        for g_idx, g_box in enumerate(res_g.boxes):
            gx1, gy1, gx2, gy2 = map(int, g_box.xyxy[0])
            group_crop = img[gy1:gy2, gx1:gx2]

            res_p = m_panel.predict(group_crop, conf=0.7, verbose=False)[0]
            sorted_p = sorted(res_p.boxes, key=lambda b: b.xyxy[0][1])

            for p_idx, p_box in enumerate(sorted_p):
                px1, py1, px2, py2 = map(int, p_box.xyxy[0])
                p_crop = group_crop[py1:py2, px1:px2]

                res_c = m_comp.predict(p_crop, conf=0.3, verbose=False)[0]
                
                desc, dist = "[Empty]", "[Empty]"
                for c_idx, c_box in enumerate(res_c.boxes):
                    cx1, cy1, cx2, cy2 = map(int, c_box.xyxy[0])
                    cls = int(c_box.cls[0])
                    
                    # 5px safety buffer
                    y_min, y_max = max(0, cy1-5), min(p_crop.shape[0], cy2+5)
                    x_min, x_max = max(0, cx1-5), min(p_crop.shape[1], cx2+5)
                    raw_crop = p_crop[y_min:y_max, x_min:x_max]
                    
                    if raw_crop.size == 0: continue
                    
                    # COLOR ISOLATION
                    binary_img = get_binary_text(raw_crop, is_dist=(cls == 1))
                    
                    # Save Binary Debug Image
                    cls_name = "desc" if cls == 0 else "dist"
                    cv2.imwrite(os.path.join(sub_dir, f"row{p_idx}_{cls_name}_binary.png"), binary_img)
                    
                    # OCR
                    config = r'--oem 3 --psm 7'
                    if cls == 1: config += r' -c tessedit_char_whitelist=0123456789m'
                    
                    text_out = pytesseract.image_to_string(binary_img, config=config).strip()
                    
                    if cls == 0: desc = text_out
                    else: dist = text_out

                results_log.append(f"Row {p_idx}: {desc} | {dist}")

        with open(os.path.join(sub_dir, "binary_results.txt"), "w") as f:
            f.write("\n".join(results_log))

    print(f"\n🏁 Finished. Check {TEST_DIR}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Manual HUD OCR check")
    ap.add_argument("--source", required=True, help="folder of screenshots")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--out", default=TEST_DIR)
    a = ap.parse_args()
    SOURCE_DIR, TEST_DIR = a.source, a.out
    run_validation(a.samples)