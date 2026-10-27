import os
from ultralytics import YOLO

# --- PATH CONFIG ---
MODEL_PATH = r"E:\label_work\runs\detect\wp_panel_v2_final\weights\best.pt"
SOURCE_IMAGES = r"E:\SWGEMU_bot\YOLO exports\wp_panel_0.02\auto-label-wpp\images"
# This will create E:\SWGEMU_bot\...\auto-wp-panel\predict
SAVE_ROOT = r"E:\SWGEMU_bot\Video capture\Desktop\screenshots"
SAVE_NAME = "auto-wp-panel"

def run_sensitive_autolabel():
    # Load your freshly trained model
    model = YOLO(MODEL_PATH)

    print(f"Starting sensitive auto-labeling at 20% confidence...")

    # Run Prediction
    results = model.predict(
        source=SOURCE_IMAGES,
        conf=0.30,           # High sensitivity to catch "misses"
        save=True,           # Save the preview images with boxes
        save_txt=True,       # Save the .txt labels for Label Studio
        save_conf=True,      # Helpful for you to see how close the misses were
        project=SAVE_ROOT,   # The base directory
        name=SAVE_NAME,      # The specific folder name
        exist_ok=True,       # Don't create 'auto-wp-panel2', just overwrite/use same folder
        imgsz=1280,          # Keep resolution high for small waypoints
        device=0             # Use that 5070 Ti
    )

    print(f"✅ Done! Previews and Labels saved to: {os.path.join(SAVE_ROOT, SAVE_NAME)}")

if __name__ == "__main__":
    run_sensitive_autolabel()

## python "E:\SWGEMU_bot\Video capture\Desktop\screenshots\auto-wp.py"