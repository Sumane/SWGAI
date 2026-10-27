"""
Merge one dataset into another (same layout). Name clashes get a 'prev_'
prefix. (Was AUTO-LABEL-FACTORY/merge_datasets.py.)

    python tools/labeling/merge_datasets.py --from coord_digits_old --into coord_digits
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import shutil
from pathlib import Path

from tools.common import dataset_dir


def count_images(root: Path) -> int:
    return sum(len(list((root / "images" / s).glob("*"))) for s in ("train", "val")
               if (root / "images" / s).exists())


def merge(src: Path, dst: Path, prefix: str = "prev_"):
    before = count_images(dst)
    for split in ("train", "val"):
        img_dir = src / "images" / split
        if not img_dir.exists():
            print(f"  Skipping {img_dir} (not found)")
            continue
        (dst / "images" / split).mkdir(parents=True, exist_ok=True)
        (dst / "labels" / split).mkdir(parents=True, exist_ok=True)
        copied = renamed = 0
        for img in img_dir.iterdir():
            lbl = src / "labels" / split / (img.stem + ".txt")
            name = img.name
            if (dst / "images" / split / name).exists():
                name = prefix + name      # rename image and label together
                renamed += 1
            shutil.copy2(img, dst / "images" / split / name)
            if lbl.exists():
                shutil.copy2(lbl, dst / "labels" / split / (Path(name).stem + ".txt"))
            copied += 1
        print(f"  {split}: copied {copied} ({renamed} renamed)")
    after = count_images(dst)
    print(f"✅ Images: {before} -> {after} (+{after - before})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--into", dest="dst", required=True)
    a = ap.parse_args()
    src, dst = dataset_dir(a.src), dataset_dir(a.dst)
    if not src.exists():
        raise SystemExit(f"❌ Not found: {src}")
    merge(src, dst)


if __name__ == "__main__":
    main()
