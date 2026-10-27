"""
Crop every detection of a model out of a folder of screenshots — the first
step of building a dataset for the next model in a cascade.

Replaces DATA MINER/data_miner.py, wp_panel_dataset/data_miner.py and
gen_sub_dataset.py. The originals map to:

  # minimap crops for the coordinate model (DATA MINER)
  python tools/labeling/crop_detections.py --model minimap --source <screens> \
      --out <data_dir>/crops/minimap --class 0 --pad 2 --limit 500 --conf 0.25

  # waypoint-group crops for the panel model (wp_panel data_miner)
  python tools/labeling/crop_detections.py --model group --source <screens> \
      --out <data_dir>/crops/wp_group --conf 0.85 --imgsz 1280 --sample 1000

  # panel crops for the components model (gen_sub_dataset)
  python tools/labeling/crop_detections.py --model panel --source <screens> \
      --out <data_dir>/crops/wp_panel --conf 0.6 --pad 2

--model takes a config key (minimap, group...), a folder under models/, or a .pt path.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import random
from pathlib import Path

import cv2

from tools.common import list_images, load_yolo, pad_box


def detections(model, images, conf, imgsz, classes):
    """Yield (image_path, image, (x1,y1,x2,y2)) for each matching detection."""
    kw = {"conf": conf, "verbose": False}
    if imgsz:
        kw["imgsz"] = imgsz
    for path in images:
        img = cv2.imread(str(path))
        if img is None:
            continue
        for box in model.predict(img, **kw)[0].boxes:
            if classes and int(box.cls[0]) not in classes:
                continue
            yield path, img, tuple(map(int, box.xyxy[0].tolist()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--source", required=True, help="folder of screenshots")
    ap.add_argument("--out", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--class", dest="classes", type=int, action="append",
                    help="only keep this class id (repeatable)")
    ap.add_argument("--pad", type=int, default=0, help="pixels added on every side")
    ap.add_argument("--pad-right", type=int, default=0, help="extra pixels on the right (arrow guard)")
    ap.add_argument("--limit", type=int, default=None, help="stop after N crops")
    ap.add_argument("--sample", type=int, default=None,
                    help="scan everything, then keep a random N crops")
    ap.add_argument("--gray", action="store_true", help="save crops in greyscale")
    ap.add_argument("--prefix", default="crop")
    a = ap.parse_args()

    model = load_yolo(a.model)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    images = list_images(a.source)
    print(f"🔍 Scanning {len(images)} images from {a.source}")

    found = detections(model, images, a.conf, a.imgsz, a.classes)
    if a.sample:
        # Remember boxes only (not images) so memory stays small, then sample
        boxes = [(p, b) for p, _, b in found]
        print(f"   {len(boxes)} detections, sampling {min(a.sample, len(boxes))}")
        chosen = random.sample(boxes, min(a.sample, len(boxes)))
        found = ((p, cv2.imread(str(p)), b) for p, b in chosen)

    count = 0
    for path, img, (x1, y1, x2, y2) in found:
        if a.limit and count >= a.limit:
            break
        h, w = img.shape[:2]
        bx = pad_box(x1, y1, x2, y2, w, h, a.pad, a.pad + a.pad_right, a.pad, a.pad)
        crop = img[bx[1]:bx[3], bx[0]:bx[2]]
        if crop.size == 0:
            continue
        if a.gray:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        cv2.imwrite(str(out / f"{a.prefix}_{count:05d}_{path.stem}.png"), crop)
        count += 1
        if count % 50 == 0:
            print(f"📦 {count} crops...")

    print(f"\n✅ {count} crops saved to {out}")


if __name__ == "__main__":
    main()
