"""
Where is the waypoint? Trilateration from (position, distance) readings.

Every reading says "the target is exactly d metres from p". One reading is a
circle; as the bot moves, the circles intersect at the target. The waypoint
distance is read to the metre, so a few hundred metres of travel pins the
target to within a few metres, at any range.

The arrow (relative bearing, 8 sectors of 45°) is a weak second clue: the
target lies somewhere inside the sector it shows. That gives the first guess,
and later picks between the two mirror-image answers a straight drive leaves.

Solved with a few Levenberg-Marquardt steps on a 2-variable least-squares problem,
in pure Python (no numpy needed), fast enough to run every tick.
"""

from __future__ import annotations

import math

ARROW_DEG = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}


def arrow_bearing(heading, arrow: str, right_turn_sign: int = -1):
    """
    World-space direction angle (radians, atan2(z, x)) the arrow points to.
    The arrow is clockwise-from-facing when right_turn_sign == -1 (measured SWG convention).
    """
    if heading is None or arrow not in ARROW_DEG:
        return None
    return math.atan2(heading[1], heading[0]) + right_turn_sign * math.radians(ARROW_DEG[arrow])


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


class TargetEstimator:
    """
    Distances are precise (integer metres) and drive the fit. The arrow is only
    an 8-way sector, so it is used as an INTERVAL: an estimate inside the sector
    the arrow showed costs nothing, one outside it is pushed back. Treating the
    sector centre as a precise bearing biased every estimate towards the line of
    travel (756 m out at 3 km when driving 15 degrees off).
    """

    def __init__(self, min_spacing_m: float = 5.0, max_readings: int = 80,
                 dist_sigma_m: float = 3.0, sector_margin_deg: float = 7.5,
                 sector_sigma_deg: float = 3.0, right_turn_sign: int = -1):
        self.min_spacing = min_spacing_m
        self.max_readings = max_readings
        self.dist_sigma = dist_sigma_m          # covers integer rounding + mounted coord lag
        # half-width of an arrow sector plus slack for heading noise and coord lag
        self.half_width = math.radians(22.5 + sector_margin_deg)
        self.sector_sigma = math.radians(sector_sigma_deg)
        self.sign = right_turn_sign
        self.readings = []   # (x, z, d, bearing or None)
        self.estimate = None
        self.rms = None
        self.sigma_m = None  # honest 1-sigma position uncertainty (worst direction), metres

    def reset(self):
        self.readings.clear()
        self.estimate = None
        self.rms = None
        self.sigma_m = None

    def add(self, pos, dist, arrow=None, heading=None) -> bool:
        """Record a reading if we've moved far enough since the last one. Returns True if added."""
        if pos is None or dist is None:
            return False
        if self.readings:
            lx, lz = self.readings[-1][:2]
            if math.hypot(pos[0] - lx, pos[1] - lz) < self.min_spacing:
                return False
        self.readings.append((pos[0], pos[1], float(dist), arrow_bearing(heading, arrow, self.sign)))
        if len(self.readings) > self.max_readings:
            # keep the first reading (widest baseline) and the most recent ones
            self.readings = self.readings[:1] + self.readings[-(self.max_readings - 1):]
        return True

    def baseline(self) -> float:
        """How far apart the readings are (metres): small baseline = weak geometry."""
        if len(self.readings) < 2:
            return 0.0
        xs = [r[0] for r in self.readings]
        zs = [r[1] for r in self.readings]
        return math.hypot(max(xs) - min(xs), max(zs) - min(zs))

    # ------------------------------------------------------------------
    def _initial_guess(self):
        """Last reading projected along its arrow bearing; fallback: previous estimate."""
        for x, z, d, b in reversed(self.readings):
            if b is not None:
                return x + d * math.cos(b), z + d * math.sin(b)
        return self.estimate

    def _normal(self, tx, tz, use_arrow=True):
        """J^T J, J^T r and the cost at (tx, tz)."""
        wd, ws = 1.0 / self.dist_sigma, 1.0 / self.sector_sigma
        a11 = a12 = a22 = g1 = g2 = cost = 0.0
        for x, z, d, b in self.readings:
            dx, dz = tx - x, tz - z
            rng = math.hypot(dx, dz) or 1e-6
            r = (rng - d) * wd
            j1, j2 = dx / rng * wd, dz / rng * wd
            a11 += j1 * j1; a12 += j1 * j2; a22 += j2 * j2; g1 += j1 * r; g2 += j2 * r
            cost += r * r
            if use_arrow and b is not None:
                err = _wrap(math.atan2(dz, dx) - b)
                excess = abs(err) - self.half_width
                if excess > 0:                       # outside the sector the arrow showed
                    sgn = 1.0 if err > 0 else -1.0
                    r = excess * ws
                    j1, j2 = sgn * -dz / rng ** 2 * ws, sgn * dx / rng ** 2 * ws
                    a11 += j1 * j1; a12 += j1 * j2; a22 += j2 * j2; g1 += j1 * r; g2 += j2 * r
                    cost += r * r
        return a11, a12, a22, g1, g2, cost

    @staticmethod
    def _worst_sigma(a11, a12, a22):
        """sqrt of the largest eigenvalue of (J^T J)^-1, i.e. 1-sigma in the weakest direction."""
        tr, det = a11 + a22, a11 * a22 - a12 * a12
        lam_min = tr / 2 - math.sqrt(max(0.0, tr * tr / 4 - det))
        return math.sqrt(1.0 / lam_min) if lam_min > 1e-12 else float("inf")

    def _solve_from(self, tx, tz, iterations=25):
        lam = 1e-3   # Levenberg-Marquardt damping: robust on the long, flat valley of a straight drive
        cost = self._normal(tx, tz)[5]
        for _ in range(iterations):
            a11, a12, a22, g1, g2, _ = self._normal(tx, tz)
            for _ in range(8):
                b11, b22 = a11 * (1 + lam), a22 * (1 + lam)
                det = b11 * b22 - a12 * a12
                if abs(det) < 1e-18:
                    lam *= 10
                    continue
                sx = -(b22 * g1 - a12 * g2) / det
                sz = -(-a12 * g1 + b11 * g2) / det
                step = math.hypot(sx, sz)
                if step > 500:
                    sx, sz = sx * 500 / step, sz * 500 / step
                new_cost = self._normal(tx + sx, tz + sz)[5]
                if new_cost < cost:
                    tx, tz, cost, lam = tx + sx, tz + sz, new_cost, max(lam / 10, 1e-7)
                    break
                lam *= 10
            else:
                break
            if step < 0.05:
                break
        res = [math.hypot(tx - x, tz - z) - d for x, z, d, _ in self.readings]
        rms = math.sqrt(sum(r * r for r in res) / len(res))
        return tx, tz, rms, cost

    def solve(self):
        """Best target estimate as (x, z), or None. Also sets rms and sigma_m."""
        guess = self._initial_guess()
        if guess is None or not self.readings:
            return None
        lx, lz, ld = self.readings[-1][:3]
        if len(self.readings) < 3:
            self.estimate, self.rms = guess, None
            self.sigma_m = ld * math.tan(self.half_width)
            return guess
        # Multi-start around the latest position. A straight drive gives two
        # mirror-image answers either side of the line of travel, and a solver
        # started exactly on the line can sit there.
        g_ang = math.atan2(guess[1] - lz, guess[0] - lx)
        sols = []
        for a in (0, -30, -15, -6, 6, 15, 30):
            ang = g_ang + math.radians(a)
            sols.append(self._solve_from(lx + ld * math.cos(ang), lz + ld * math.sin(ang)))
        sols.sort(key=lambda t: t[3])
        bx, bz, rms, cost = sols[0]
        self.estimate, self.rms = (bx, bz), rms

        # Honest uncertainty:
        #  1. geometry: how well the readings pin the target (incl. active sector limits)
        a11, a12, a22 = self._normal(bx, bz)[:3]
        sigma = self._worst_sigma(a11, a12, a22)
        #  2. ambiguity: another, clearly different answer that fits about as well
        #     (the two mirror solutions of a straight drive inside the same sector)
        n = len(self.readings)
        for ox, oz, _, ocost in sols[1:]:
            sep = math.hypot(ox - bx, oz - bz)
            if sep > 2 * self.dist_sigma and ocost - cost < max(4.0, 0.1 * n):
                sigma = max(sigma, sep / 2)
        #  3. never worse than the arrow sector itself
        rng = math.hypot(bx - lx, bz - lz)
        self.sigma_m = min(sigma, max(rng * math.tan(self.half_width), 1.0))
        return self.estimate

    def confident(self, max_sigma_m: float = 25.0) -> bool:
        """Distances fit well AND the geometry pins the target to within max_sigma_m."""
        return (self.rms is not None and self.rms < 4 * self.dist_sigma
                and self.sigma_m is not None and self.sigma_m <= max_sigma_m
                and len(self.readings) >= 4)
