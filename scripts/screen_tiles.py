"""
Cut a screenshot into full-resolution tiles (and named regions) so small text
is readable by a vision model that shrinks big images.

    python scripts/screen_tiles.py shot.png [--out DIR] [--rows 3 --cols 3] [--overlap 0.1]

Named regions come from harness.screen_regions in settings.yaml (chat, minimap...).
"""
import argparse

import _bootstrap  # noqa: F401
import cv2

from jedai.config import settings
from jedai.perception.tiles import save_pieces

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rows", type=int, default=3)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--overlap", type=float, default=0.1)
    a = ap.parse_args()
    img = cv2.imread(a.image)
    if img is None:
        raise SystemExit(f"can't read {a.image}")
    out = a.out or a.image.rsplit(".", 1)[0] + "_tiles"
    regions = {k: tuple(v) for k, v in settings.raw.get("harness", {}).get("screen_regions", {}).items()}
    for p in save_pieces(img, out, a.rows, a.cols, a.overlap, regions):
        print(p)
