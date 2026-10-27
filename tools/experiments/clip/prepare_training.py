"""
Generate labels.json from the labeled/ folder structure
Run this before training
"""
import json
from pathlib import Path
import sys

def main():
    labeled_dir = Path("labeled")
    
    if not labeled_dir.exists():
        print("ERROR: No labeled/ folder found!")
        print("Run review.py first to label clusters")
        sys.exit(1)
    
    data = []
    
    # Walk through labeled/ folder
    for label_folder in labeled_dir.iterdir():
        if not label_folder.is_dir():
            continue
        
        label = label_folder.name
        image_count = 0
        
        for img_path in label_folder.glob("*"):
            if img_path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.bmp']:
                # Path relative to data directory (labeled/)
                rel_path = f"{label}/{img_path.name}"
                data.append({
                    "path": rel_path,
                    "label": label
                })
                image_count += 1
        
        print(f"  {label}: {image_count} images")
    
    if len(data) == 0:
        print("ERROR: No labeled images found!")
        sys.exit(1)
    
    # Create data directory if needed
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    
    # Save labels.json
    labels_file = data_dir / "labels.json"
    with open(labels_file, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"\n✓ Created {labels_file} with {len(data)} labeled images")
    print(f"  Classes: {len(set(d['label'] for d in data))}")
    
    # Also create processed folder structure that train_clip.py expects
    processed_dir = data_dir / "processed"
    processed_dir.mkdir(exist_ok=True)
    
    # Copy images to processed/ folder (train_clip.py looks here)
    import shutil
    for item in data:
        src = labeled_dir / item["path"]
        dst = processed_dir / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copy2(src, dst)
    
    print(f"✓ Copied images to {processed_dir}/")
    print("\nReady for training!")
    print("  Run: python train_clip.py --data_dir data/processed --labels_file data/labels.json --epochs 10 --freeze_clip")

if __name__ == "__main__":
    main()
