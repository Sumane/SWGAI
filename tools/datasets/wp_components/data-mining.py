import os
import cv2
import random
from ultralytics import YOLO

# --- MODELS ---
# Using your existing models
GROUP_MODEL_PATH = r'E:\label_work\runs\detect\wp_group_v1\weights\best.pt' # Assuming your window detector path
PANEL_MODEL_PATH = r'E:\label_work\runs\detect\wp_panel_v2_production3\weights\best.pt'

# --- PATHS ---
SCREENSHOT_DIR = r'E:\SWGEMU_bot\Video capture\Desktop\screenshots\clean_data\screenshots'
DEST_DIR = r'E:\SWGEMU_bot\YOLO exports\distance_and_description_dataset\auto-label'

SAMPLE_COUNT = 1000

def collect_logical_samples():
    group_model = YOLO(GROUP_MODEL_PATH)
    panel_model = YOLO(PANEL_MODEL_PATH)
    
    os.makedirs(DEST_DIR, exist_ok=True)

    all_files = [f for f in os.listdir(SCREENSHOT_DIR) if f.lower().endswith(('.png', '.jpg'))]
    random.shuffle(all_files)

    count = 0
    print("Initiating hierarchical extraction...")

    for filename in all_files:
        if count >= SAMPLE_COUNT: break

        img = cv2.imread(os.path.join(SCREENSHOT_DIR, filename))
        if img is None: continue

        # STEP 1: Find the wp_group (The Window)
        group_results = group_model.predict(img, conf=0.7, verbose=False)[0]
        
        for g_box in group_results.boxes:
            gx1, gy1, gx2, gy2 = map(int, g_box.xyxy[0].tolist())
            group_crop = img[gy1:gy2, gx1:gx2] # Isolate the UI window

            # STEP 2: Find wp_panels INSIDE the group crop
            panel_results = panel_model.predict(group_crop, conf=0.8, verbose=False)[0]
            
            if len(panel_results.boxes) > 0:
                # Pick one panel from this group to keep variety high
                p_box = random.choice(panel_results.boxes)
                px1, py1, px2, py2 = map(int, p_box.xyxy[0].tolist())

                # Apply your "Arrow Guard" padding (40px right)
                gh, gw = group_crop.shape[:2]
                pad_px2 = min(gw, px2 + 40)
                
                # STEP 3: Extract the specific panel from the group
                final_panel = group_crop[py1:py2, px1:pad_px2]

                # STEP 4: Grayscale
                gray_final = cv2.cvtColor(final_panel, cv2.COLOR_BGR2GRAY)

                # Save it
                save_name = f"logical_seed_{count:03d}_{filename}"
                cv2.imwrite(os.path.join(DEST_DIR, save_name), gray_final)
                
                count += 1
                print(f"Captured logical sample {count}/100")
                break # Move to next screenshot after one successful group extraction

    print(f"\nDone! Samples saved to: {DEST_DIR}")

if __name__ == '__main__':
    collect_logical_samples()

## python "E:\SWGEMU_bot\YOLO exports\distance_and_description_dataset\data-mining.py"