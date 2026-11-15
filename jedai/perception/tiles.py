"""
Split a full-resolution screenshot into readable pieces.

A 2560x1440 frame shrunk to fit a model's image input loses the chat text, HUD
numbers and small icons. Instead, cut it into overlapping tiles at full
resolution (plus named regions such as the chat window) and look at each one.
Used by scripts/harness_live.py and scripts/screen_tiles.py; meant for any
reviewer (Claude now, the local Qwen later).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def tile_boxes(width: int, height: int, rows: int = 3, cols: int = 3, overlap: float = 0.1):
    """[(name, x0, y0, x1, y1)] covering the frame; neighbours overlap by `overlap`."""
    tw, th = width / cols, height / rows
    ox, oy = tw * overlap, th * overlap
    out = []
    for r in range(rows):
        for c in range(cols):
            x0 = max(0, int(c * tw - ox))
            y0 = max(0, int(r * th - oy))
            x1 = min(width, int((c + 1) * tw + ox))
            y1 = min(height, int((r + 1) * th + oy))
            out.append((f"r{r}c{c}", x0, y0, x1, y1))
    return out


def region_box(width: int, height: int, frac):
    """Named region given as fractions (x0, y0, x1, y1) of the frame -> pixels."""
    x0, y0, x1, y1 = frac
    return int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)


def save_pieces(img: np.ndarray, folder: str | Path, rows: int = 3, cols: int = 3,
                overlap: float = 0.1, regions: dict | None = None, quality: int = 85) -> list[Path]:
    """
    Write tiles r{row}c{col}.jpg and each named region <name>.jpg at full
    resolution into `folder` (replacing the previous set). Returns the paths.
    Also writes index.txt: what each file covers, in pixels.
    """
    import cv2

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    h, w = img.shape[:2]
    boxes = tile_boxes(w, h, rows, cols, overlap)
    for name, frac in (regions or {}).items():
        boxes.append((name, *region_box(w, h, frac)))
    paths, lines = [], [f"frame {w}x{h}; tiles {rows}x{cols}, overlap {overlap:.0%}"]
    for name, x0, y0, x1, y1 in boxes:
        p = folder / f"{name}.jpg"
        tmp = folder / f"{name}.tmp.jpg"
        cv2.imwrite(str(tmp), img[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, quality])
        tmp.replace(p)
        paths.append(p)
        lines.append(f"{name}.jpg  x {x0}-{x1}  y {y0}-{y1}")
    (folder / "index.txt").write_text("\n".join(lines) + "\n")
    return paths
