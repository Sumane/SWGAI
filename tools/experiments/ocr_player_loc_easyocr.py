"""
OLD APPROACH, kept for reference: read the player coordinates with EasyOCR
instead of the YOLO digit model (which replaced it).

    python tools/experiments/ocr_player_loc_easyocr.py --model <navigation_v3 best.pt> --image <screenshot>
"""
import argparse
import cv2
import easyocr
import numpy as np
from ultralytics import YOLO

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True, help="detector with a 'minimap_coordinates' class")
ap.add_argument("--image", required=True)
ap.add_argument("--debug-out", default="ocr_debug.png")
args = ap.parse_args()

# 1. Initialize EasyOCR
reader = easyocr.Reader(['en'], gpu=True)

# 2. Paths
model_path = args.model
image_path = args.image
debug_save_path = args.debug_out

# 3. Load Model and Image
model = YOLO(model_path)
results = model(image_path, conf=0.25) 
original_img = cv2.imread(image_path)

print("-" * 30)

for r in results:
    for box in r.boxes:
        cls = int(box.cls[0])
        label = model.names[cls]
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        if "minimap_coordinates" in label:
            print(f"🎯 MATCH FOUND: Processing Coordinates...")
            
            # --- THE SAFETY PADDING ---
            # Shift x1 LEFT by 10 pixels to capture the leading minus sign
            # Shift y1 UP by 5 pixels to capture the top of the numbers
            x1_safe = max(0, x1 - 10)
            y1_safe = max(0, y1 - 5)
            x2_stretched = min(original_img.shape[1], x1 + 350) 
            y2_safe = min(original_img.shape[0], y2 + 5)
            
            crop = original_img[y1_safe:y2_safe, x1_safe:x2_stretched]
            
            if crop.size == 0: continue

            # --- IMAGE PREPARATION ---
            # 3x Resize + Grayscale
            crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            
            # Using a slightly lower threshold (130) to keep thin minus signs solid
            _, binary = cv2.threshold(gray, 130, 255, cv2.THRESH_BINARY_INV)

            # --- OCR (LIST MODE) ---
            ocr_results = reader.readtext(
                binary, 
                detail=0, 
                allowlist='0123456789- '
            )

            if ocr_results:
                clean_parts = [p.strip() for p in ocr_results if p.strip()]
                full_string = " ".join(clean_parts)
                
                # Replace common OCR errors (like double dashes or spaces in numbers)
                full_string = full_string.replace(" -", "-").replace("- ", "-")
                final_parts = full_string.split()

                print(f"📍 ------------------------------")
                print(f"📍 RAW PIECES: {clean_parts}")
                if len(final_parts) >= 3:
                    print(f"✅ X: {final_parts[0]} | Y: {final_parts[1]} | Z: {final_parts[2]}")
                else:
                    print(f"⚠️ Found {len(final_parts)} parts: {final_parts}")
                print(f"📍 ------------------------------")
            
            cv2.imwrite(debug_save_path, binary)

print("Script finished. Check ocr_debug.png - make sure the minus sign is visible!")

