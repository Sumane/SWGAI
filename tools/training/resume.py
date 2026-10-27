"""
Resume a crashed training run from its last checkpoint
(replaces resume-training.py and AUTO-LABEL-FACTORY/restart_training.py).

    python tools/training/resume.py wp_group_v0101          # run name under <data_dir>/runs
    python tools/training/resume.py path/to/weights/last.pt
    python tools/training/resume.py wp_group_v0101 --low-memory

--low-memory uses batch 4 and workers 0 (what you used after the DLL/paging crash).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))  # noqa: E401,E702

import argparse
from pathlib import Path

from tools.common import data_dir


def find_last(run: str) -> Path:
    p = Path(run)
    if p.suffix == ".pt":
        return p
    for cand in (data_dir("runs", run, "weights", "last.pt"),
                 data_dir("runs", "detect", run, "weights", "last.pt"),
                 p / "weights" / "last.pt"):
        if cand.exists():
            return cand
    raise SystemExit(f"❌ No last.pt found for '{run}'")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--low-memory", action="store_true")
    a = ap.parse_args()

    from ultralytics import YOLO

    last = find_last(a.run)
    print(f"▶️  Resuming from {last}")
    extra = {"batch": 4, "workers": 0, "close_mosaic": 10} if a.low_memory else {"workers": 0}
    YOLO(str(last)).train(resume=True, **extra)


if __name__ == "__main__":
    main()
