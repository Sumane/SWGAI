"""
Walk a recorded route: a list of (x, z) points a player walked once (e.g. out
of a cloning facility, 16 Nov recording). No planner and no map: indoors the
route IS the knowledge. Works from position reads alone:

  - the facing isn't known at the start, so step forward and see which way
    the position moved;
  - turn towards the next point (LEFT/RIGHT held for angle / turn rate),
    step forward in short bursts, correct the facing from each burst;
  - a burst that goes nowhere = against a wall: step back, turn, try again.

Coordinates come in whole metres, so a facing is only taken from a move of
2 m or more and points are "reached" within reach_m.
"""

from __future__ import annotations

import math
import time


def _rot(v, deg):
    """Rotate (x, z) counter-clockwise by deg."""
    a = math.radians(deg)
    return (v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a))


def simplify(points, min_gap_m: float = 4.0):
    """Thin a recorded track: keep a point every min_gap_m, and always the last."""
    out = []
    for p in points:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= min_gap_m:
            out.append((float(p[0]), float(p[1])))
    if points and out[-1] != (float(points[-1][0]), float(points[-1][1])):
        out.append((float(points[-1][0]), float(points[-1][1])))
    return out


class RouteFollower:
    def __init__(self, read_position, keys, *, turn_rate_deg_s: float = 100.0, right_turn_sign: int = -1,
                 reach_m: float = 2.5, final_reach_m: float = 3.0, speed_m_s: float = 5.0,
                 max_burst_s: float = 1.4, settle_s: float = 0.3, max_stuck: int = 10,
                 timeout_s: float | None = None, should_stop=lambda: False, on_event=None,
                 sleep=time.sleep, clock=time.time):
        self.read_position, self.keys = read_position, keys
        self.turn_rate, self.sign = float(turn_rate_deg_s), int(right_turn_sign)
        self.reach, self.final_reach = reach_m, final_reach_m
        self.speed, self.max_burst, self.settle = speed_m_s, max_burst_s, settle_s
        self.max_stuck, self.timeout = max_stuck, timeout_s
        self.should_stop, self.sleep, self.clock = should_stop, sleep, clock
        self.on_event = on_event or (lambda kind, **kw: None)
        self.heading = None          # unit (x, z) the character faces, once known
        self.track = []              # positions seen, for the log
        self.actions = []            # every key tap with the position after it, for review

    # -- primitives ---------------------------------------------------------
    def _pos(self):
        for _ in range(5):
            p = self.read_position()
            if p is not None:
                return (float(p[0]), float(p[1]))
            self.sleep(0.2)
        return None

    def _forward(self, secs, p0=None):
        """Step forward; returns (before, after, metres moved)."""
        p0 = p0 or self._pos()
        self.keys.tap("FORWARD", secs)
        self.sleep(self.settle)
        p1 = self._pos()
        if p0 is None or p1 is None:
            return p0, p1, 0.0
        self.track.append(p1)
        moved = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        self.actions.append({"key": "FORWARD", "secs": round(secs, 2), "from": p0, "to": p1, "moved": round(moved, 1)})
        return p0, p1, moved

    def _turn(self, deg):
        """deg > 0 = counter-clockwise (seen from above, x east, z north)."""
        if abs(deg) < 1:
            return
        # RIGHT rotates the facing by right_turn_sign x angle (see Navigator)
        key = "RIGHT" if (deg > 0) == (self.sign > 0) else "LEFT"
        self.keys.tap(key, abs(deg) / self.turn_rate)
        self.actions.append({"key": key, "secs": round(abs(deg) / self.turn_rate, 2), "deg": round(deg)})
        self.sleep(0.1)
        if self.heading is not None:
            self.heading = _rot(self.heading, deg)

    def _learn_heading(self, p0, p1, moved, coarse_ok=False):
        # whole-metre reads: 2 m gives a fair direction; 1 m only a rough one
        # (used when nothing better is known, e.g. boxed into a clone tube)
        if moved >= 2.0 or (coarse_ok and moved >= 1.0):
            self.heading = ((p1[0] - p0[0]) / moved, (p1[1] - p0[1]) / moved)

    # -- the walk -----------------------------------------------------------
    def follow(self, waypoints) -> tuple[str, dict]:
        """('arrived' | 'stuck' | 'timeout' | 'stopped' | 'no_position', info)."""
        wps = [(float(x), float(z)) for x, z in waypoints]
        t0, stuck, i = self.clock(), 0, 0
        # 16 Nov, Keren Starport: a fixed 120 s ran out two-thirds of the way
        # along a 100 m walk that was going fine. Allow time by length.
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(wps, wps[1:]))
        timeout = self.timeout if self.timeout is not None else max(120.0, 5.0 * length + 60.0)
        pos = self._pos()
        if pos is None:
            return "no_position", {}
        self.track.append(pos)
        # start at the nearest point, not necessarily the first (we may already be part-way)
        i = min(range(len(wps)), key=lambda k: math.hypot(wps[k][0] - pos[0], wps[k][1] - pos[1]))
        while True:
            if self.should_stop():
                return "stopped", self._info(i, wps)
            if self.clock() - t0 > timeout:
                return "timeout", self._info(i, wps)
            pos = self._pos()
            if pos is None:
                return "no_position", self._info(i, wps)
            # reached this point (or already nearer the next one): move on
            while True:
                d = math.hypot(wps[i][0] - pos[0], wps[i][1] - pos[1])
                last = i == len(wps) - 1
                if d <= (self.final_reach if last else self.reach):
                    if last:
                        return "arrived", self._info(i, wps)
                    i += 1
                    continue
                if not last and math.hypot(wps[i + 1][0] - pos[0], wps[i + 1][1] - pos[1]) < d:
                    i += 1
                    continue
                break
            if self.heading is None:
                # Which way are we facing? Step and see. 16 Nov, Theed clone
                # tube: 0.6 s steps moved ~1 m whichever way it turned, so
                # take a longer step, accept a 1 m move as a rough facing,
                # and sweep round in 45 degree steps without backing off.
                p0, p1, moved = self._forward(1.0)
                self._learn_heading(p0, p1, moved, coarse_ok=True)
                if self.heading is None:
                    stuck += 1
                    self.on_event("route_probe_blocked", pos=pos, n=stuck)
                    if stuck > self.max_stuck:
                        return "stuck", self._info(i, wps)
                    self._turn(45)
                else:
                    stuck = 0
                continue
            # Aim further ahead while the route runs straight: fewer, longer
            # steps (each step costs about a second of reading the screen).
            j = i
            while j + 1 < len(wps) and math.hypot(wps[j + 1][0] - pos[0], wps[j + 1][1] - pos[1]) <= 14.0:
                ax, az = wps[j + 1][0] - pos[0], wps[j + 1][1] - pos[1]
                al = math.hypot(ax, az) or 1.0
                # every point up to j+1 within 1.5 m of the straight line to it?
                if all(abs((w[0] - pos[0]) * az - (w[1] - pos[1]) * ax) / al <= 1.5 for w in wps[i:j + 1]):
                    j += 1
                else:
                    break
            dx, dz = wps[j][0] - pos[0], wps[j][1] - pos[1]
            d_aim = math.hypot(dx, dz)
            ang = math.degrees(math.atan2(self.heading[0] * dz - self.heading[1] * dx,
                                          self.heading[0] * dx + self.heading[1] * dz))
            if abs(ang) > 12:
                self._turn(ang)
            burst = max(0.25, min(self.max_burst, 0.8 * d_aim / max(self.speed, 1.0)))
            p0, p1, moved = self._forward(burst, p0=pos)
            if moved >= 1.0:
                stuck = 0
                self.speed = 0.6 * self.speed + 0.4 * max(2.0, min(12.0, moved / burst))
                self._learn_heading(p0, p1, moved)
            else:
                stuck += 1
                self.on_event("route_blocked", pos=p1, wp=i, n=stuck)
                if stuck > self.max_stuck:
                    return "stuck", self._info(i, wps)
                if stuck >= 2:
                    # against something: back off, try to one side (wider each time), re-measure
                    self.keys.tap("BACK", 0.4)
                    self.sleep(self.settle)
                    self._turn((40 + 15 * (stuck // 2)) * (1 if stuck % 2 == 0 else -1))
                    q0, q1, m = self._forward(0.8)
                    self._learn_heading(q0, q1, m, coarse_ok=True)

    def _info(self, i, wps):
        pos = self.track[-1] if self.track else None
        left = math.hypot(wps[-1][0] - pos[0], wps[-1][1] - pos[1]) if pos else None
        return {"waypoint": i, "of": len(wps), "pos": pos, "to_end_m": left, "track": self.track[-200:],
                "actions": self.actions[-300:]}
