"""Speed from successive coordinate readings."""

from __future__ import annotations

import math
import time
from collections import deque


class VelocityTracker:
    """Rolling average ground speed (X/Z plane) in metres per second."""

    def __init__(self, history: int = 5, clock=time.time):
        self._history = deque(maxlen=history)
        self._last = None
        self._last_t = None
        self._clock = clock

    def reset(self):
        self._history.clear()
        self._last = None
        self._last_t = None

    def update(self, x: float, z: float):
        now = self._clock()
        if self._last is not None and self._last_t is not None:
            dt = now - self._last_t
            if dt > 0:
                self._history.append(math.hypot(x - self._last[0], z - self._last[1]) / dt)
        self._last = (x, z)
        self._last_t = now

    def speed(self) -> float:
        if not self._history:
            return 0.0
        return sum(self._history) / len(self._history)
