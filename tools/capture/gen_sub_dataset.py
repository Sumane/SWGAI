import os
import cv2
from ultralytics import YOLO

# --- CONFIG ---
MODEL_PATH = r"E:\label_work\runs\detect\wp_panel_v2_final3\weights\best.pt"
IMAGE_DIR = r"E:\SWGEMU_bot\YOLO exports\wp_panel_0.02\auto-label-wpp\images"
EXPORT_DIR = r"E:\SWGEMU_bot\YOLO exports\wp_panel_0.02\sub_dataset_crops"

os.makedirs(EXPORT_DIR, exist_ok=True)

def generate_crops():
    model = YOLO(MODEL_PATH)
    images = [f for f in os.listdir(IMAGE_DIR) if f.endswith(('.png', '.jpg'))]
    
    count = 0
    print(f"Cropping waypoints from {len(images)} images...")

    for img_name in images:
        img_path = os.path.join(IMAGE_DIR, img_name)
        img = cv2.imread(img_path)
        if img is None: continue

        # Run inference (using high confidence so we only get clean panels)
        results = model.predict(source=img, conf=0.6, verbose=False)[0]

        for i, box in enumerate(results.boxes):
            # Get pixel coordinates [x1, y1, x2, y2]
            xyxy = box.xyxy[0].tolist()
            x1, y1, x2, y2 = map(int, xyxy)

            # Crop with a tiny 2px buffer to keep the border intact
            crop = img[max(0, y1-2):y2+2, max(0, x1-2):x2+2]
            
            if crop.size > 0:
                crop_name = f"panel_{count:04d}.png"
                cv2.imwrite(os.path.join(EXPORT_DIR, crop_name), crop)
                count += 1

    print(f"✅ Success! Created {count} panel crops in {EXPORT_DIR}")

if __name__ == "__main__":
    generate_crops()

## python "E:\SWGEMU_bot\Video capture\Desktop\screenshots\gen_sub_dataset.py"