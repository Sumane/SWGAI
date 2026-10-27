import os
import random
import shutil

# --- CONFIG ---
BASE_DIR = r'E:\SWGEMU_bot\YOLO exports\distance_and_description_dataset'
IMAGE_DIR = os.path.join(BASE_DIR, 'images')
LABEL_DIR = os.path.join(BASE_DIR, 'labels')
DEST_DIR = os.path.join(BASE_DIR, 'processed')
SPLIT_RATIO = 0.8  # 80% train, 20% val

def split_dataset():
    # Create structure
    for s in ['train', 'val']:
        for d in ['images', 'labels']:
            os.makedirs(os.path.join(DEST_DIR, s, d), exist_ok=True)

    images = [f for f in os.listdir(IMAGE_DIR) if f.lower().endswith(('.png', '.jpg'))]
    random.shuffle(images)

    split_idx = int(len(images) * SPLIT_RATIO)
    train_imgs = images[:split_idx]
    val_imgs = images[split_idx:]

    def move_files(files, subset):
        for f in files:
            # Move Image
            shutil.copy(os.path.join(IMAGE_DIR, f), os.path.join(DEST_DIR, subset, 'images', f))
            # Move Label
            label_f = os.path.splitext(f)[0] + '.txt'
            label_src = os.path.join(LABEL_DIR, label_f)
            if os.path.exists(label_src):
                shutil.copy(label_src, os.path.join(DEST_DIR, subset, 'labels', label_f))

    move_files(train_imgs, 'train')
    move_files(val_imgs, 'val')
    print(f"Split complete: {len(train_imgs)} train, {len(val_imgs)} val.")

if __name__ == '__main__':
    split_dataset()

## python "E:\SWGEMU_bot\YOLO exports\distance_and_description_dataset\splitter.py"