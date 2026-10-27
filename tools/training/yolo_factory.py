"""
YOLO factory: Label Studio export -> train/val split -> train -> deploy.

    1. In Label Studio, export as "YOLO" and unzip into <data_dir>/import_stage
       (it should contain images/, labels/ and classes.txt)
    2. python tools/training/yolo_factory.py mini_map --epochs 200

Copies the export into <data_dir>/datasets/<name>, trains with the 'default'
preset (or --preset), and deploys best.pt to models/<name>_<timestamp>/.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import shutil
import time
from pathlib import Path

from tools.common import data_dir, find_classes, list_images, make_layout, split_items
from tools.training.train import train


def import_export(src: Path, dest: Path, val_fraction: float):
    img_dir, lbl_dir = src / "images", src / "labels"
    if not img_dir.exists():
        raise SystemExit(f"❌ No images/ folder in {src}")
    pairs = [p for p in list_images(img_dir) if (lbl_dir / (p.stem + ".txt")).exists()]
    print(f"📦 Found {len(pairs)} image/label pairs in {src}")
    if not pairs:
        raise SystemExit("❌ No matching pairs found. Check filenames!")

    classes = find_classes(src)
    if not classes:
        raise SystemExit("❌ classes.txt missing from the export")

    if dest.exists():
        shutil.rmtree(dest)
    dirs = make_layout(dest)
    train_set, val_set = split_items(pairs, val_fraction)
    for split, items in (("train", train_set), ("val", val_set)):
        for img in items:
            shutil.copy2(img, dirs[f"images_{split}"] / img.name)
            shutil.copy2(lbl_dir / (img.stem + ".txt"), dirs[f"labels_{split}"] / (img.stem + ".txt"))
        print(f"🚚 {split}: {len(items)}")
    (dest / "classes.txt").write_text("\n".join(classes) + "\n")
    return classes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="model / dataset name, e.g. mini_map")
    ap.add_argument("--source", default=None, help="Label Studio export folder (default <data_dir>/import_stage)")
    ap.add_argument("--preset", default="default")
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--val", type=float, default=0.2, help="validation fraction")
    a = ap.parse_args()

    start = time.time()
    src = Path(a.source) if a.source else data_dir("import_stage")
    dest = data_dir("datasets", a.name)
    classes = import_export(src, dest, a.val)
    print(f"🧠 Classes ({len(classes)}): {classes}")

    run_name = f"{a.name}_{time.strftime('%Y%m%d_%H%M')}"
    train(a.preset, dataset=str(dest), name=run_name, deploy=True,
          epochs=a.epochs, rect=True, imgsz=640)
    print(f"⏱️  Total factory time: {(time.time() - start) / 60:.1f} minutes")


if __name__ == "__main__":
    main()
