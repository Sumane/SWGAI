"""
Pre-label images for correction in Label Studio: runs a model and saves
YOLO .txt labels (with confidence) plus preview images with boxes drawn.
(Replaces run_autolabel.py and auto-wp.py.)

    python tools/labeling/prelabel.py --model panel --source <images> --name wp_panel_check \
        --conf 0.30 --imgsz 1280

Output: <data_dir>/prelabels/<name>/ (labels/ + previews). Use
tools/labeling/strip_confidence.py on the labels before training on them.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse

from tools.common import data_dir, load_yolo


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default=None, help="e.g. 0 for the first GPU")
    a = ap.parse_args()

    out_root = data_dir("prelabels")
    kw = {"device": a.device} if a.device is not None else {}
    load_yolo(a.model).predict(source=a.source, conf=a.conf, imgsz=a.imgsz,
                               save=True, save_txt=True, save_conf=True,
                               project=str(out_root), name=a.name, exist_ok=True, **kw)
    print(f"✅ Labels and previews saved to {out_root / a.name}")


if __name__ == "__main__":
    main()
