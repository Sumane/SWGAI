from ultralytics import YOLO

def train_wp_panel_v2():
    # We continue with Nano for the fastest inference in the final bot
    model = YOLO('yolov8n.pt') 

    results = model.train(
        data=r'E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\wp_panel.yaml',
        epochs=150,      # Bumped epochs to let it settle on the new 1k dataset
        imgsz=640,
        rect=True,       # Still essential for the stacked waypoint rows
        batch=-1,        # Let it max out your VRAM
        device=0,
        project=r'E:\label_work\runs\detect',
        name='wp_panel_v2_production',
        # Precision Settings
        patience=30,     # Stop early if it stops improving
        save_period=10,  # Save a checkpoint every 10 epochs
        # Augmentation (Keep it tight for UI)
        mosaic=0,
        mixup=0,
        hsv_v=0.3,       # Allow for more brightness variance (night vs day)
        overlap_mask=False # Important for stacked boxes
    )

if __name__ == '__main__':
    train_wp_panel_v2()

## run with python "E:\SWGEMU_bot\YOLO exports\wp_panel_dataset\train.py"