"""
Two-stage crop: find the waypoint window (group model), then one random
waypoint row inside it (panel model), padded on the right so the arrow is
included, saved in greyscale. Builds the dataset for the components model.
(Was distance_and_description_dataset/data-mining.py.)

    python tools/labeling/mine_panel_rows.py --source <screens> \
        --out <data_dir>/crops/panel_rows --count 1000
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import random
from pathlib import Path

import cv2

from tools.common import list_images, load_yolo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--group-model", default="group")
    ap.add_argument("--panel-model", default="panel")
    ap.add_argument("--group-conf", type=float, default=0.7)
    ap.add_argument("--panel-conf", type=float, default=0.8)
    ap.add_argument("--pad-right", type=int, default=40, help="arrow guard")
    ap.add_argument("--count", type=int, default=1000)
    a = ap.parse_args()

    group_model, panel_model = load_yolo(a.group_model), load_yolo(a.panel_model)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = list_images(a.source)
    random.shuffle(files)

    count = 0
    for path in files:
        if count >= a.count:
            break
        img = cv2.imread(str(path))
        if img is None:
            continue
        for g in group_model.predict(img, conf=a.group_conf, verbose=False)[0].boxes:
            gx1, gy1, gx2, gy2 = map(int, g.xyxy[0].tolist())
            group = img[gy1:gy2, gx1:gx2]
            panels = panel_model.predict(group, conf=a.panel_conf, verbose=False)[0].boxes
            if len(panels) == 0:
                continue
            px1, py1, px2, py2 = map(int, random.choice(panels).xyxy[0].tolist())
            row = group[py1:py2, px1:min(group.shape[1], px2 + a.pad_right)]
            if row.size == 0:
                continue
            cv2.imwrite(str(out / f"logical_seed_{count:04d}_{path.name}"),
                        cv2.cvtColor(row, cv2.COLOR_BGR2GRAY))
            count += 1
            print(f"Captured {count}/{a.count}")
            break   # one row per screenshot keeps variety high

    print(f"\n✅ Done! {count} samples saved to {out}")


if __name__ == "__main__":
    main()
