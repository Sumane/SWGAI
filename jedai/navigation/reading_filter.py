"""
Plausibility gate for HUD readings (coordinates and waypoint distance).

The bot can't move faster than its vehicle, so a reading can't change by more
than   base_tol + max_speed * slack * (time since the last good reading).
Anything bigger is an OCR misread (887 m read as 9887 m) and is dropped.

FIX (first in-game drive, 27 Oct 2026): the old coordinate filter rejected any
jump over a fixed 50 m from the last accepted reading. With ~2 s vision ticks
and a swoop doing 16 m/s, one long gap put the bot more than 50 m from the
last accepted position, and from then on EVERY reading was rejected: the
position froze, the speed read 0, and every cell attempt said "moved 0.0m".
The allowance now grows with time, and if several rejected readings agree
with each other, the old reading was the wrong one and the gate moves on.
"""

from __future__ import annotations

import math
import time


class MotionGate:
    def __init__(self, base_tol: float, max_speed, slack: float = 1.5,
                 recover_after: int = 3, clock=time.time):
        """
        base_tol: always-allowed change (reading noise, lag), same units as the value
        max_speed: number, or a function returning the current max speed (units/s)
        """
        self.base_tol = base_tol
        self._max_speed = max_speed if callable(max_speed) else (lambda: max_speed)
        self.slack = slack
        self.recover_after = recover_after
        self.clock = clock
        self.reset()

    def reset(self, value=None):
        self.last = value
        self.t_last = self.clock() if value is not None else None
        self.pending = []          # recent rejected (value, t)
        self.rejected = 0

    @staticmethod
    def _diff(a, b) -> float:
        if isinstance(a, (tuple, list)):
            return math.hypot(a[0] - b[0], a[1] - b[1])
        return abs(a - b)

    def _allowed(self, dt: float) -> float:
        return self.base_tol + self._max_speed() * self.slack * max(0.0, dt)

    def check(self, value):
        """Returns the value if plausible (and remembers it), else None."""
        if value is None:
            return None
        now = self.clock()
        if self.last is None or self._diff(value, self.last) <= self._allowed(now - self.t_last):
            self.last, self.t_last = value, now
            self.pending = []
            return value
        self.rejected += 1
        self.pending.append((value, now))
        self.pending = self.pending[-self.recover_after:]
        if len(self.pending) >= self.recover_after and all(
                self._diff(v1, v0) <= self._allowed(t1 - t0)
                for (v0, t0), (v1, t1) in zip(self.pending, self.pending[1:])):
            # several readings agree with each other: the old one was wrong
            self.last, self.t_last = value, now
            self.pending = []
            return value
        return None
