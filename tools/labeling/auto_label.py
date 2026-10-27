"""
Auto-label factory: run an existing model over new images, write YOLO
labels, split train/val, draw preview images for review, then optionally
train a fresh model on the result.

Replaces AUTO-LABEL-FACTORY/auto-label.py (+ MODELPATH.py / DATAPATH.py)
and wp_panel_dataset/auto.py (padded labels).

    # the old factory default (coordinate-digit model on mined crops)
    python tools/labeling/auto_label.py --model co_digit \
        --source <data_dir>/crops/coords --name coord_digits

    # padded panel labels, the old auto.py (1000 random images, arrow guard)
    python tools/labeling/auto_label.py --model panel --source <crops> \
        --name wp_panel --conf 0.5 --imgsz 640 --pad-left 1 --pad-right 40 \
        --sample 1000 --keep-empty --val 0.2

Output: <data_dir>/datasets/<name>/{images,labels}/{train,val}, preview/,
dataset.yaml, classes.txt. Review preview/ before training!
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import random
import shutil

import cv2

from tools.common import (data_dir, list_images, load_yolo, make_layout, pad_box,
                          split_items, write_dataset_yaml, xyxy_to_yolo, yolo_line)

PREVIEW_MIN_SIZE = 400   # upscale tiny crops so boxes/text are readable
COLOURS = [(0, 255, 0), (0, 128, 255), (255, 0, 128), (255, 255, 0), (0, 255, 255), (255, 0, 255),
           (128, 255, 0), (0, 64, 255), (255, 128, 0), (64, 0, 255), (0, 255, 128), (255, 64, 0)]


def label_image(model, path, a):
    """Return (label_lines, (w, h)) for one image, or (None, None) if unreadable."""
    img = cv2.imread(str(path))
    if img is None:
        return None, None
    h, w = img.shape[:2]
    kw = {"conf": a.conf, "verbose": False}
    if a.imgsz:
        kw["imgsz"] = a.imgsz
    lines = []
    for box in model.predict(img, **kw)[0].boxes:
        x1, y1, x2, y2 = pad_box(*box.xyxy[0].tolist(), w, h,
                                 a.pad_left, a.pad_right, a.pad_vert, a.pad_vert)
        lines.append(yolo_line(int(box.cls[0]), xyxy_to_yolo(x1, y1, x2, y2, w, h)))
    return lines, (w, h)


def draw_preview(src, lines, names, dest):
    img = cv2.imread(str(src))
    if img is None:
        return
    ih, iw = img.shape[:2]
    scale = max(1.0, PREVIEW_MIN_SIZE / min(ih, iw))
    if scale > 1.0:
        img = cv2.resize(img, (int(iw * scale), int(ih * scale)), interpolation=cv2.INTER_NEAREST)
    dh, dw = img.shape[:2]
    thick = max(1, int(scale))
    font = max(0.4, 0.45 * scale)
    for line in lines:
        cls, cx, cy, bw, bh = line.split()
        cls = int(cls)
        cx, cy, bw, bh = map(float, (cx, cy, bw, bh))
        x1, y1 = int((cx - bw / 2) * dw), int((cy - bh / 2) * dh)
        x2, y2 = int((cx + bw / 2) * dw), int((cy + bh / 2) * dh)
        colour = COLOURS[cls % len(COLOURS)]
        label = str(names.get(cls, cls))
        cv2.rectangle(img, (x1, y1), (x2, y2), colour, thick)
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font, thick)
        ty = max(y1 - 4, th + 4)
        cv2.rectangle(img, (x1, ty - th - 4), (x1 + tw + 4, ty + base), colour, -1)
        cv2.putText(img, label, (x1 + 2, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, font, (0, 0, 0), thick)
    cv2.putText(img, src.name, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(dest / src.name), img)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="config key, models/ folder, or .pt path")
    ap.add_argument("--source", required=True, help="folder of images (searched recursively)")
    ap.add_argument("--name", required=True, help="dataset name under <data_dir>/datasets")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--val", type=float, default=0.15, help="validation fraction")
    ap.add_argument("--sample", type=int, default=None, help="only use a random N images")
    ap.add_argument("--pad-left", type=int, default=0)
    ap.add_argument("--pad-right", type=int, default=0)
    ap.add_argument("--pad-vert", type=int, default=0)
    ap.add_argument("--keep-empty", action="store_true",
                    help="keep images with no detections (as negatives with empty labels)")
    ap.add_argument("--train", action="store_true", help="train straight away without asking")
    ap.add_argument("--train-model", default="yolov8s.pt")
    ap.add_argument("--train-epochs", type=int, default=100)
    ap.add_argument("--train-imgsz", type=int, default=340)
    a = ap.parse_args()

    model = load_yolo(a.model)
    names = model.names
    images = list_images(a.source, recursive=True)
    if not images:
        raise SystemExit(f"❌ No images found in {a.source}")
    if a.sample:
        images = random.sample(images, min(a.sample, len(images)))
    print(f"Found {len(images)} images. Classes: {names}")

    labelled, skipped = {}, 0
    for i, path in enumerate(images, 1):
        lines, _ = label_image(model, path, a)
        if lines is None or (not lines and not a.keep_empty):
            skipped += 1
        else:
            labelled[path] = lines
        if i % 50 == 0:
            print(f"  {i}/{len(images)} processed...")
    print(f"  Labelled: {len(labelled)} | Skipped: {skipped}")
    if not labelled:
        raise SystemExit("❌ No detections in any image. Check the model / --conf.")

    root = data_dir("datasets", a.name)
    dirs = make_layout(root)
    preview = root / "preview"
    preview.mkdir(exist_ok=True)
    train_set, val_set = split_items(list(labelled), a.val, seed=None)
    for split, items in (("train", train_set), ("val", val_set)):
        for p in items:
            shutil.copy2(p, dirs[f"images_{split}"] / p.name)
            (dirs[f"labels_{split}"] / (p.stem + ".txt")).write_text("\n".join(labelled[p]))
            draw_preview(p, labelled[p], names, preview)
    print(f"  Train: {len(train_set)} | Val: {len(val_set)}")

    class_list = [names[i] for i in sorted(names)]
    (root / "classes.txt").write_text("\n".join(class_list) + "\n")
    yaml_path = write_dataset_yaml(root, class_list)
    print(f"\nDataset: {yaml_path}\n👀 Review the previews before training: {preview}")

    if a.train or input("\nPreviews look good? Start training? [y/N]: ").strip().lower() == "y":
        from tools.training.train import train
        train("default", dataset=str(root), name=a.name, model=a.train_model,
              epochs=a.train_epochs, imgsz=a.train_imgsz)
    else:
        print(f"Training skipped. Later: python tools/training/train.py --dataset {a.name}")


if __name__ == "__main__":
    main()
