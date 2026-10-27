import os
import cv2
import random
from ultralytics import YOLO

# --- PATHS ---
MODEL_PATH = r'E:\label_work\runs\detect\wp_group_v0101\weights\best.pt'
DATA_LAKE = r'E:\SWGEMU_bot\Video capture\Desktop\screenshots\clean_data\screenshots'
CROP_DEST = r'E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\raw_data'

def mine_waypoint_crops(sample_size=1000):
    model = YOLO(MODEL_PATH)
    os.makedirs(CROP_DEST, exist_ok=True)
    
    # Get all potential screenshots
    all_files = [f for f in os.listdir(DATA_LAKE) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    
    # We want to find ALL instances first, then sample, 
    # because one screenshot might have 0 or multiple groups.
    all_detections = []

    print(f"Scanning {len(all_files)} images for Waypoint Groups...")

    for img_name in all_files:
        img_path = os.path.join(DATA_LAKE, img_name)
        # Use a high confidence threshold to ensure we only get "clean" data for the next stage
        results = model.predict(img_path, conf=0.85, imgsz=1280, verbose=False)
        
        for i, box in enumerate(results[0].boxes):
            # Store the image path and the specific box coordinates
            all_detections.append({
                'path': img_path,
                'name': img_name,
                'box': box.xyxy[0].tolist() # [x1, y1, x2, y2]
            })

    print(f"Found {len(all_detections)} total groups.")

    # Randomly sample the requested amount
    if len(all_detections) > sample_size:
        selected_samples = random.sample(all_detections, sample_size)
        print(f"Sampling {sample_size} random groups for the new dataset.")
    else:
        selected_samples = all_detections
        print(f"Using all {len(all_detections)} found groups.")

    # Execute the crops
    for idx, sample in enumerate(selected_samples):
        img = cv2.imread(sample['path'])
        if img is None:
            continue
            
        x1, y1, x2, y2 = map(int, sample['box'])
        
        # Crop and save
        # Adding a unique index to filename to prevent overwrites if multiple groups in 1 image
        crop = img[y1:y2, x1:x2]
        crop_filename = f"wp_group_{idx:04d}_{sample['name']}"
        cv2.imwrite(os.path.join(CROP_DEST, crop_filename), crop)

    print(f"Success! {len(selected_samples)} crops are ready in {CROP_DEST}")

if __name__ == '__main__':
    mine_waypoint_crops(1000)

## run with python "E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\data_miner.py"