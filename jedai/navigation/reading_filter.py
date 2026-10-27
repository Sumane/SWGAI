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
                 recover_after: int = 3, recover_secs: float = 0.0, clock=time.time):
        """
        base_tol: always-allowed change (reading noise, lag), same units as the value
        max_speed: number, or a function returning the current max speed (units/s)
        """
        self.base_tol = base_tol
        self._max_speed = max_speed if callable(max_speed) else (lambda: max_speed)
        self.slack = slack
        self.recover_after = recover_after
        self.recover_secs = recover_secs    # rejected readings must keep agreeing this long
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
        # newest run of rejected readings that agree with each other
        chain = [self.pending[-1]]
        for v0, t0 in reversed(self.pending[:-1]):
            v1, t1 = chain[-1]
            if self._diff(v1, v0) > self._allowed(t1 - t0):
                break
            chain.append((v0, t0))
        if len(chain) >= self.recover_after and now - chain[-1][1] >= self.recover_secs:
            # several readings agree with each other (and nothing plausible
            # came in between): the old one was wrong
            self.last, self.t_last = value, now
            self.pending = []
            return value
        if len(self.pending) > 50:
            self.pending = self.pending[-50:]
        return None


class DistanceGate:
    """
    Waypoint distance filter that uses where the bot actually is.

    The distance to a fixed waypoint can't change by more than the bot has
    moved. FIX (in-game, 27 Oct): the old time-based allowance grew while no
    good reading came in, so after ~1 min of misreads a jump from 898 m to
    78 m (another waypoint's row) was "possible", and then 8986 m after 8 s:
    the watchdog stopped a run that was 78-898 m out. Now the allowance is
    tolerance + 1.2 x metres moved since the last good reading. Readings that
    break that are never believed, except while the gate is still settling
    (its first few readings, when the starting value itself may be a misread).
    """

    def __init__(self, tol: float = 15.0, settle_readings: int = 5,
                 recover_secs: float = 8.0, clock=time.time):
        self.tol = tol
        self.settle = settle_readings
        self.recover_secs = recover_secs
        self.clock = clock
        self.reset()

    def reset(self, value=None):
        self.last = value
        self.last_pos = None
        self.t_last = self.clock() if value is not None else None
        self.accepted = 0 if value is None else 1
        self.pending = []

    def allowed(self, pos) -> float:
        moved = 0.0
        if pos is not None and self.last_pos is not None:
            moved = math.hypot(pos[0] - self.last_pos[0], pos[1] - self.last_pos[1])
        elif self.t_last is not None:
            moved = 16.0 * min(self.clock() - self.t_last, 10.0)    # no position: bounded guess
        return self.tol + 0.02 * (self.last or 0) + 1.2 * moved

    def check(self, value, pos=None):
        if value is None:
            return None
        now = self.clock()
        if self.last is None or abs(value - self.last) <= self.allowed(pos):
            self.last, self.t_last = value, now
            if pos is not None:
                self.last_pos = pos
            self.accepted += 1
            self.pending = []
            return value
        if self.accepted < self.settle:
            # still settling: a run of readings that agree for recover_secs wins
            self.pending = [(v, t) for v, t in self.pending if abs(v - value) <= self.tol] + [(value, now)]
            if len(self.pending) >= 3 and now - self.pending[0][1] >= self.recover_secs:
                self.reset(value)
                self.last_pos = pos
                return value
        return None
