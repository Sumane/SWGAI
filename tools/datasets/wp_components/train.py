from ultralytics import YOLO

def train_wp_components():
    # Use Nano - it's perfect for simple shapes like text blocks
    model = YOLO('yolov8n.pt') 

    model.train(
        data=r'E:\SWGEMU_bot\YOLO exports\distance_and_description_dataset\wp_components.yaml',
        epochs=150,
        imgsz=640,       # Keeps resolution high for the small text
        rect=True,       # Crucial for these long, thin strips
        batch=-1,        # Auto-detect best batch for 5070 Ti
        device=0,
        project=r'E:\label_work\runs\detect',
        name='wp_components_v3',
        # Augmentations: Keep it simple since UI is static
        mosaic=0,        
        hsv_v=0.4,       # Helps with those "Ghost" low-contrast waypoints
        perspective=0.0,
        overlap_mask=False
    )

if __name__ == '__main__':
    train_wp_components()

## python "E:\SWGEMU_bot\YOLO exports\distance_and_description_dataset\train.py"