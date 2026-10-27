"""
Shared helpers for the data / labelling / training tools.

Each tool starts with:
    import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from tools.common import ...
so it runs from anywhere without installing anything.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import yaml

from jedai.config import resolve_path, settings  # noqa: F401  (re-exported for tools)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ---------------------------------------------------------------------------
# Paths and files
# ---------------------------------------------------------------------------
def data_dir(*parts) -> Path:
    """A folder inside paths.data_dir (from config)."""
    return settings.data_path(*parts)


def dataset_dir(name_or_path: str) -> Path:
    """A dataset folder: absolute path, or a name under <data_dir>/datasets/."""
    p = Path(name_or_path)
    return p if p.is_absolute() or p.exists() else data_dir("datasets", name_or_path)


def list_images(folder, recursive: bool = False) -> list[Path]:
    folder = Path(folder)
    it = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def model_weights(name_or_path: str) -> Path:
    """Model by config key ('minimap'), folder name ('wp_distance') or .pt path."""
    return settings.model_path(name_or_path)


def load_yolo(name_or_path: str):
    from ultralytics import YOLO

    path = model_weights(name_or_path)
    if not path.exists():
        raise SystemExit(f"❌ Model not found: {path}")
    print(f"🚀 Loading model: {path}")
    return YOLO(str(path))


# ---------------------------------------------------------------------------
# Boxes and labels
# ---------------------------------------------------------------------------
def pad_box(x1, y1, x2, y2, w, h, left=0, right=0, top=0, bottom=0):
    """Expand a pixel box and clamp it to the image."""
    return (max(0, int(x1) - left), max(0, int(y1) - top),
            min(w, int(x2) + right), min(h, int(y2) + bottom))


def xyxy_to_yolo(x1, y1, x2, y2, w, h):
    """Pixel corners -> normalised (cx, cy, bw, bh)."""
    return ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, (x2 - x1) / w, (y2 - y1) / h)


def yolo_line(cls_id: int, box) -> str:
    return f"{cls_id} {box[0]:.6f} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f}"


def read_classes(path) -> list[str]:
    """Read classes.txt. Handles plain names and '0. name' style lines."""
    names = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            names.append(re.sub(r"^\d+\.\s+", "", line))
    return names


def find_classes(folder) -> list[str] | None:
    folder = Path(folder)
    for cand in (folder / "classes.txt", folder / "labels" / "classes.txt"):
        if cand.exists():
            return read_classes(cand)
    return None


# ---------------------------------------------------------------------------
# Dataset layout:  root/images/{train,val}  root/labels/{train,val}
# ---------------------------------------------------------------------------
def make_layout(root) -> dict:
    root = Path(root)
    dirs = {f"{kind}_{split}": root / kind / split
            for kind in ("images", "labels") for split in ("train", "val")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def split_items(items: list, val_fraction: float, seed: int | None = 42):
    """Shuffle and split into (train, val). At least 1 val item if possible."""
    items = list(items)
    random.Random(seed).shuffle(items)
    n_val = max(1, int(len(items) * val_fraction)) if len(items) > 1 else 0
    return items[n_val:], items[:n_val]


def write_dataset_yaml(root, class_names: list[str], path=None) -> Path:
    """Write an ultralytics dataset yaml with an absolute root path."""
    root = Path(root).resolve()
    path = Path(path) if path else root / "dataset.yaml"
    data = {"path": str(root), "train": "images/train", "val": "images/val",
            "nc": len(class_names), "names": list(class_names)}
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def strip_label_studio_suffix(filename: str) -> str:
    """'image-8a2b3c.png' -> 'image.png' (Label Studio adds a hex suffix)."""
    return re.sub(r"-[a-f0-9]{6,12}\.", ".", filename)
