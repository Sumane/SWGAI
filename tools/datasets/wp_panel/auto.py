import os
import cv2
import random
import shutil
from ultralytics import YOLO

# --- PATHS ---
MODEL_PATH = r'E:\label_work\runs\detect\wp_panel_v12\weights\best.pt'
SOURCE_DIR = r'E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\raw_data'
DEST_BASE = r'E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\raw_data\auto-label'
PREVIEW_DIR = os.path.join(DEST_BASE, 'preview')

# --- SETTINGS ---
SAMPLE_SIZE = 1000
SPLIT_RATIO = 0.8 
RIGHT_PAD = 40  # Capture the arrow
LEFT_PAD = 1
VERT_PAD = 0    # Keep vertical tight to avoid row bleed

def auto_label_panels():
    model = YOLO(MODEL_PATH)
    
    # Setup Directory Structure
    for s in ['train', 'val']:
        for d in ['images', 'labels']:
            os.makedirs(os.path.join(DEST_BASE, s, d), exist_ok=True)
    os.makedirs(PREVIEW_DIR, exist_ok=True)

    all_files = [f for f in os.listdir(SOURCE_DIR) if f.lower().endswith(('.png', '.jpg'))]
    selected_files = random.sample(all_files, min(len(all_files), SAMPLE_SIZE))

    print(f"Auto-labeling {len(selected_files)} images with Arrow Padding...")

    for img_name in selected_files:
        img_path = os.path.join(SOURCE_DIR, img_name)
        img = cv2.imread(img_path)
        h, w, _ = img.shape
        
        results = model.predict(img_path, imgsz=640, conf=0.5, verbose=False)[0]
        
        # Determine Train/Val Split
        subset = 'train' if random.random() < SPLIT_RATIO else 'val'
        shutil.copy(img_path, os.path.join(DEST_BASE, subset, 'images', img_name))
        
        label_lines = []
        preview_img = img.copy()

        for box in results.boxes:
            cls = int(box.cls[0])
            # Get raw pixel coordinates (xyxy)
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            
            # Apply Padding
            x1_p = max(0, x1 - LEFT_PAD)
            y1_p = max(0, y1 - VERT_PAD)
            x2_p = min(w, x2 + RIGHT_PAD)
            y2_p = min(h, y2 + VERT_PAD)

            # Draw preview box (blue) for sanity check
            cv2.rectangle(preview_img, (x1_p, y1_p), (x2_p, y2_p), (255, 0, 0), 2)

            # Convert back to YOLO format (normalized center_x, center_y, width, height)
            dw = 1. / w
            dh = 1. / h
            x_center = ((x1_p + x2_p) / 2.0) * dw
            y_center = ((y1_p + y2_p) / 2.0) * dh
            width = (x2_p - x1_p) * dw
            height = (y2_p - y1_p) * dh
            
            label_lines.append(f"{cls} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")
            
        # Save Labels
        label_path = os.path.join(DEST_BASE, subset, 'labels', os.path.splitext(img_name)[0] + ".txt")
        with open(label_path, 'w') as f:
            f.writelines(label_lines)

        # Save Custom Preview (shows the padded boxes we actually saved)
        cv2.imwrite(os.path.join(PREVIEW_DIR, img_name), preview_img)

    print(f"Done! Padded labels and previews saved to {DEST_BASE}")

if __name__ == '__main__':
    auto_label_panels()

## run with python "E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\auto.py"