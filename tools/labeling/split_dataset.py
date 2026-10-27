"""
Split an images/ + labels/ folder into the training layout
(replaces wp_direction/split.py, splitter.py, and the split in yolo_factory).

    python tools/labeling/split_dataset.py --images <dir> --labels <dir> \
        --name wp_direction --val 0.2 --strip-ls-suffix

Only images that have a label file are used. Output:
<data_dir>/datasets/<name>/{images,labels}/{train,val} (+ classes.txt if found).
--pair-from-pool: label files are the "clean" set and images come from a big
pool folder; only matching names are used (was DirectionV2_merge.py).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import shutil
from pathlib import Path

from tools.common import (IMAGE_EXTS, dataset_dir, find_classes, list_images, make_layout,
                          split_items, strip_label_studio_suffix)


def pairs_from_images(images_dir: Path, labels_dir: Path):
    return [(img, labels_dir / (img.stem + ".txt")) for img in list_images(images_dir)
            if (labels_dir / (img.stem + ".txt")).exists()]


def pairs_from_labels(images_dir: Path, labels_dir: Path):
    out = []
    for lbl in sorted(labels_dir.glob("*.txt")):
        if lbl.name == "classes.txt":
            continue
        img = next((images_dir / (lbl.stem + ext) for ext in IMAGE_EXTS
                    if (images_dir / (lbl.stem + ext)).exists()), None)
        if img:
            out.append((img, lbl))
        else:
            print(f"Warning: no image for {lbl.stem}")
    return out


def split_dataset(images, labels, dest, val=0.2, strip_suffix=False, pair_from_pool=False, seed=42):
    images, labels, dest = Path(images), Path(labels), Path(dest)
    pairs = (pairs_from_labels if pair_from_pool else pairs_from_images)(images, labels)
    if not pairs:
        raise SystemExit("❌ No image/label pairs found")
    dirs = make_layout(dest)
    train_set, val_set = split_items(pairs, val, seed)
    for split, items in (("train", train_set), ("val", val_set)):
        for img, lbl in items:
            name = strip_label_studio_suffix(img.name) if strip_suffix else img.name
            shutil.copy2(img, dirs[f"images_{split}"] / name)
            shutil.copy2(lbl, dirs[f"labels_{split}"] / (Path(name).stem + ".txt"))
    classes = find_classes(labels.parent) or find_classes(labels)
    if classes:
        (dest / "classes.txt").write_text("\n".join(classes) + "\n")
    print(f"✅ Train: {len(train_set)} | Val: {len(val_set)} -> {dest}")
    return len(train_set), len(val_set)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--name", required=True, help="dataset name (or a path)")
    ap.add_argument("--val", type=float, default=0.2)
    ap.add_argument("--strip-ls-suffix", action="store_true",
                    help="remove the Label Studio '-a1b2c3' suffix from filenames")
    ap.add_argument("--pair-from-pool", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    split_dataset(a.images, a.labels, dataset_dir(a.name), a.val, a.strip_ls_suffix,
                  a.pair_from_pool, a.seed)


if __name__ == "__main__":
    main()
