import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from tools.common import pad_box, read_classes, strip_label_studio_suffix, xyxy_to_yolo
from tools.capture.rename_screenshots import rename_files
from tools.labeling.merge_datasets import merge
from tools.labeling.split_dataset import split_dataset
from tools.labeling.strip_confidence import strip_file

ROOT = Path(__file__).resolve().parent.parent


def _img(path):
    cv2.imwrite(str(path), np.zeros((8, 8, 3), np.uint8))


def test_box_helpers():
    assert pad_box(5, 5, 10, 10, 12, 12, left=1, right=40) == (4, 5, 12, 10)
    assert xyxy_to_yolo(0, 0, 50, 100, 100, 100) == (0.25, 0.5, 0.5, 1.0)
    assert strip_label_studio_suffix("shot-8a2b3c.png") == "shot.png"


def test_read_classes_both_styles(tmp_path):
    (tmp_path / "a.txt").write_text("0. description\n1. distance\n")
    (tmp_path / "b.txt").write_text("E\nN\n\n")
    assert read_classes(tmp_path / "a.txt") == ["description", "distance"]
    assert read_classes(tmp_path / "b.txt") == ["E", "N"]


def test_split_dataset(tmp_path):
    imgs, lbls = tmp_path / "images", tmp_path / "labels"
    imgs.mkdir(); lbls.mkdir()
    for i in range(10):
        _img(imgs / f"s{i}-abc123.png")
        if i < 8:
            (lbls / f"s{i}-abc123.txt").write_text("0 0.5 0.5 0.1 0.1")
    (tmp_path / "classes.txt").write_text("arrow\n")
    n_train, n_val = split_dataset(imgs, lbls, tmp_path / "out", val=0.25, strip_suffix=True)
    assert (n_train, n_val) == (6, 2)
    out_imgs = list((tmp_path / "out/images/train").glob("*.png"))
    assert all("-abc123" not in p.name for p in out_imgs)
    for p in out_imgs:
        assert (tmp_path / "out/labels/train" / (p.stem + ".txt")).exists()
    assert (tmp_path / "out/classes.txt").read_text().strip() == "arrow"


def test_strip_confidence(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("0 0.1 0.2 0.3 0.4 0.97\n1 0.5 0.5 0.1 0.1 0.5\n")
    strip_file(f)
    assert f.read_text() == "0 0.1 0.2 0.3 0.4\n1 0.5 0.5 0.1 0.1"


def test_merge_renames_image_and_label_together(tmp_path):
    for root in ("a", "b"):
        (tmp_path / root / "images/train").mkdir(parents=True)
        (tmp_path / root / "labels/train").mkdir(parents=True)
        _img(tmp_path / root / "images/train/same.png")
        (tmp_path / root / "labels/train/same.txt").write_text(root)
    merge(tmp_path / "a", tmp_path / "b")
    assert (tmp_path / "b/labels/train/prev_same.txt").read_text() == "a"
    assert (tmp_path / "b/labels/train/same.txt").read_text() == "b"


def test_rename_dry_run(tmp_path):
    for i in range(3):
        _img(tmp_path / f"img{i}.png")
    assert rename_files(tmp_path, "swg", dry_run=True) == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["img0.png", "img1.png", "img2.png"]


CLI_TOOLS = [
    "tools/training/train.py", "tools/training/resume.py", "tools/training/yolo_factory.py",
    "tools/labeling/auto_label.py", "tools/labeling/crop_detections.py",
    "tools/labeling/mine_panel_rows.py", "tools/labeling/prelabel.py",
    "tools/labeling/split_dataset.py", "tools/labeling/merge_datasets.py",
    "tools/labeling/strip_confidence.py", "tools/capture/rename_screenshots.py",
    "tools/capture/video_watcher.py", "tools/capture/video_to_screenshots.py",
]


@pytest.mark.parametrize("script", CLI_TOOLS)
def test_cli_help_runs(script):
    r = subprocess.run([sys.executable, str(ROOT / script), "--help"],
                       capture_output=True, text=True, cwd="/")
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()


def test_train_list_presets():
    r = subprocess.run([sys.executable, str(ROOT / "tools/training/train.py"), "--list"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "wp_direction" in r.stdout
