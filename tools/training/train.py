"""
Train a YOLO model from a preset (replaces train_wp_group_v0100/v0101.py,
wp_panel_dataset/train.py and distance_and_description_dataset/train.py).

    python tools/training/train.py --preset wp_panel
    python tools/training/train.py --preset wp_panel --name wp_panel_v3 --epochs 200
    python tools/training/train.py --preset default --dataset my_new_dataset
    python tools/training/train.py --list

Runs go to <data_dir>/runs/<name>. With --deploy the best weights are copied
to models/<name>/weights/best.pt (then point config/settings.yaml at it).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
import shutil
import time
from pathlib import Path

import yaml

from tools.common import data_dir, dataset_dir, find_classes, settings, write_dataset_yaml

PRESETS_FILE = Path(__file__).with_name("presets.yaml")
NON_TRAIN_KEYS = {"dataset", "classes", "model"}


def load_presets() -> dict:
    return yaml.safe_load(PRESETS_FILE.read_text())


def dataset_yaml_for(dataset: str, classes) -> Path:
    root = dataset_dir(dataset)
    if not (root / "images" / "train").exists():
        raise SystemExit(f"❌ Dataset not found or wrong layout: {root}\n"
                         f"   expected images/train, images/val, labels/train, labels/val")
    if classes == "from_dataset" or not classes:
        classes = find_classes(root)
        if not classes:
            raise SystemExit(f"❌ No classes.txt in {root} and none in the preset")
    return write_dataset_yaml(root, classes)


def train(preset_name: str, dataset=None, name=None, deploy=False, **overrides):
    from ultralytics import YOLO

    presets = load_presets()
    if preset_name not in presets:
        raise SystemExit(f"❌ Unknown preset '{preset_name}'. Try --list")
    p = dict(presets[preset_name])
    p.update({k: v for k, v in overrides.items() if v is not None})

    data_yaml = dataset_yaml_for(dataset or p.get("dataset", preset_name), p.get("classes"))
    run_name = name or f"{preset_name}_{time.strftime('%Y%m%d_%H%M')}"
    train_args = {k: v for k, v in p.items() if k not in NON_TRAIN_KEYS}

    print(f"🧠 Training '{run_name}' from preset '{preset_name}'")
    print(f"   data: {data_yaml}\n   args: {train_args}")
    results = YOLO(p.get("model", "yolov8n.pt")).train(
        data=str(data_yaml), project=str(data_dir("runs")), name=run_name,
        exist_ok=True, **train_args)

    best = Path(results.save_dir) / "weights" / "best.pt"
    print(f"\n✅ Done. Best weights: {best}")
    if deploy and best.exists():
        dest = settings.path("models_dir") / run_name / "weights" / "best.pt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, dest)
        for extra in ("args.yaml", "results.csv"):
            if (Path(results.save_dir) / extra).exists():
                shutil.copy2(Path(results.save_dir) / extra, dest.parent.parent / extra)
        print(f"📦 Deployed to {dest}\n   To use it, set models.<key>: {run_name} in config")
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default="default")
    ap.add_argument("--dataset", help="dataset folder name under <data_dir>/datasets, or a path")
    ap.add_argument("--name", help="run name (default: <preset>_<timestamp>)")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--batch", type=int)
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--model", help="starting weights, e.g. yolov8s.pt")
    ap.add_argument("--deploy", action="store_true", help="copy best.pt into models/<name>/")
    ap.add_argument("--list", action="store_true", help="show presets and exit")
    a = ap.parse_args()

    if a.list:
        for k, v in load_presets().items():
            print(f"  {k:<15} model={v.get('model')} epochs={v.get('epochs')} imgsz={v.get('imgsz')}")
        return
    train(a.preset, a.dataset, a.name, a.deploy,
          epochs=a.epochs, batch=a.batch, imgsz=a.imgsz, model=a.model)


if __name__ == "__main__":
    main()
