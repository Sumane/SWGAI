"""Screen capture and safe cropping."""

from __future__ import annotations

import cv2
import numpy as np


class Screen:
    """Grabs frames from one monitor as BGR numpy arrays."""

    def __init__(self, monitor_index: int):
        self.monitor_index = monitor_index

    def grab(self) -> np.ndarray:
        import mss  # imported here so offline tools/tests don't need it

        with mss.mss() as sct:
            sct_img = sct.grab(sct.monitors[self.monitor_index])
            return cv2.cvtColor(np.array(sct_img), cv2.COLOR_BGRA2BGR)


def safe_crop(img, x1, y1, x2, y2):
    """Crop clamped to the image. Returns None if the crop is empty."""
    if img is None:
        return None
    h, w = img.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    crop = img[y1:y2, x1:x2]
    return crop if crop.size > 0 else None
