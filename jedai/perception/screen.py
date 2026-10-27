"""Screen capture and safe cropping."""

from __future__ import annotations

import cv2
import numpy as np


class Screen:
    """
    Grabs frames from one monitor as BGR numpy arrays.
    On Linux `display` picks the X display (e.g. ":2", the game's own window).
    """

    def __init__(self, monitor_index: int, display: str | None = None):
        self.monitor_index = monitor_index
        self.display = display
        self._sct = None

    @classmethod
    def from_settings(cls, settings):
        from ..platform_io import capture_display
        return cls(settings.monitor_index, capture_display(settings))

    def _mss(self):
        if self._sct is None:
            import mss  # imported here so offline tools/tests don't need it
            self._sct = mss.mss(display=self.display) if self.display else mss.mss()
        return self._sct

    def grab(self) -> np.ndarray:
        sct = self._mss()
        mons = sct.monitors
        if self.monitor_index >= len(mons):
            raise RuntimeError(f"screen.monitor_index {self.monitor_index} doesn't exist; "
                               f"this display has monitors 1..{len(mons) - 1}")
        return cv2.cvtColor(np.array(sct.grab(mons[self.monitor_index])), cv2.COLOR_BGRA2BGR)


def safe_crop(img, x1, y1, x2, y2):
    """Crop clamped to the image. Returns None if the crop is empty."""
    if img is None:
        return None
    h, w = img.shape[:2]
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w, int(x2)), min(h, int(y2))
    crop = img[y1:y2, x1:x2]
    return crop if crop.size > 0 else None
