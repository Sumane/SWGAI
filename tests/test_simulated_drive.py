"""
End-to-end drive in a tiny simulated world: no game, no GPU.

The fake bot turns while LEFT/RIGHT are held and moves while FORWARD is held
(or autorun is on). The fake HUD reports its X/Z and the waypoint arrow as a
RELATIVE bearing, like SWG. A virtual clock makes the run instant.
"""
import math

import numpy as np
import pytest

from jedai.config import load_settings
from jedai.navigation import navigator as nav_mod, runner as runner_mod
from jedai.navigation.runner import NavigationRunner
from jedai.perception.hud import Waypoint

ARROWS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, d):
        self.t += max(0.0, d)


class World:
    SPEED, TURN = 16.0, math.radians(120)    # m/s, rad/s

    def __init__(self, clock, target, walls=(), right_is_clockwise=True, mounted_lag_s=0.0):
        self.clock, self.target, self.walls = clock, target, walls
        self.right = -1 if right_is_clockwise else 1     # yaw change when RIGHT is held
        self.x = self.z = 0.0
        self.yaw = math.pi / 2               # facing +Z ("north"), target is east
        self.held, self.autorun, self.last = set(), False, clock.t
        self.dismounts = 0
        self.mounted_lag_s = mounted_lag_s     # reported coords trail reality while mounted
        self.history = [(clock.t, 0.0, 0.0)]

    def blocked(self, x, z):
        return any(x0 <= x <= x1 and z0 <= z <= z1 for x0, z0, x1, z1 in self.walls)

    def step(self):
        dt, self.last = self.clock.t - self.last, self.clock.t
        if "RIGHT" in self.held:
            self.yaw += self.right * self.TURN * dt
        if "LEFT" in self.held:
            self.yaw -= self.right * self.TURN * dt
        if "FORWARD" in self.held or self.autorun:
            nx = self.x + math.cos(self.yaw) * self.SPEED * dt
            nz = self.z + math.sin(self.yaw) * self.SPEED * dt
            if not self.blocked(nx, nz):
                self.x, self.z = nx, nz
        self.history.append((self.clock.t, self.x, self.z))
        self.history = self.history[-400:]

    def reported_position(self):
        """What the HUD shows: on a vehicle, where we were mounted_lag_s ago."""
        lag = self.mounted_lag_s if self.dismounts == 0 else 0.0
        want = self.clock.t - lag
        h = self.history
        for i in range(len(h) - 1, 0, -1):
            t1, x1, z1 = h[i]
            t0, x0, z0 = h[i - 1]
            if t0 <= want <= t1:      # interpolate: movement between steps is a straight line
                f = (want - t0) / (t1 - t0) if t1 > t0 else 1.0
                return x0 + f * (x1 - x0), z0 + f * (z1 - z0)
        return h[0][1], h[0][2]

    def arrow(self):
        bearing = math.atan2(self.target[1] - self.z, self.target[0] - self.x)
        rel = (self.right * (bearing - self.yaw)) % (2 * math.pi)   # measured in the RIGHT direction
        return ARROWS[int((math.degrees(rel) + 22.5) // 45) % 8]

    def dist(self):
        return int(math.hypot(self.target[0] - self.x, self.target[1] - self.z))


class SimKeys:
    def __init__(self, world, clock):
        self.w, self.c, self.autorun_active = world, clock, False

    def _tick(self):
        self.w.step()

    def key_down(self, k): self._tick(); self.w.held.add(k)
    def key_up(self, k): self._tick(); self.w.held.discard(k)

    def tap(self, k, duration=0.0):
        self.key_down(k); self.c.sleep(duration); self.key_up(k); self.c.sleep(0.05)

    def press(self, k, state=None):
        self._tick()
        if k == "AUTORUN":
            self.w.autorun = not self.w.autorun

    def start_autorun(self):
        if not self.autorun_active:
            self.press("AUTORUN"); self.autorun_active = True

    def stop_movement(self):
        if self.autorun_active:
            self.press("AUTORUN"); self.autorun_active = False
        self.key_up("FORWARD")

    def release_all(self):
        self.w.held.clear(); self.w.autorun = False; self.autorun_active = False

    def dismount(self, method="command", command="/dismount", key=""):
        self.stop_movement()
        self.w.dismounts += 1
        self.w.SPEED = 10.5          # on foot


class SimHud:
    def __init__(self, world):
        self.w = world

    def read_coords(self, frame):
        x, z = self.w.reported_position()
        return round(x, 1), 0.0, round(z, 1)

    def waypoints(self, frame, should_stop=None):
        return iter([Waypoint("Theed Starport", self.w.dist(), (0, 0), self.w.arrow())])


class SimScreen:
    def __init__(self, world, clock):
        self.w, self.c = world, clock

    def grab(self):
        self.c.sleep(0.05)       # each frame costs time
        self.w.step()
        return np.zeros((2, 2, 3), np.uint8)


WALL = ((140, -60, 150, 60),)


@pytest.mark.parametrize("walls,clockwise,sign,arrives", [
    ((), True, -1, True),            # X east / Z north world, correct setting
    ((), False, 1, True),            # mirrored world, original setting
    (WALL, True, -1, True),          # wall in the way
    ((), True, 1, False),            # WRONG setting: must not arrive (shows why it matters)
], ids=["cw-open", "ccw-open", "cw-wall", "wrong-sign"])
def test_drives_to_waypoint(tmp_path, monkeypatch, walls, clockwise, sign, arrives):
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])                                    # default vehicle, waypoint 0
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    world = World(clock, target=(400.0, 0.0), walls=walls, right_is_clockwise=clockwise)
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    s.raw["navigation"]["right_turn_sign"] = sign
    r = NavigationRunner(s, hud=SimHud(world), keys=SimKeys(world, clock),
                         screen=SimScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 600        # give up after 10 sim-minutes

    r.navigate()
    import sqlite3
    done = sqlite3.connect(tmp_path / "nav.db").execute("select completed from nav_runs").fetchall()
    if arrives:
        assert world.dist() <= s.nav["stop_dist"], \
            f"ended {world.dist()}m away at ({world.x:.0f},{world.z:.0f})"
        assert done == [(1,)], "arrival not recorded in nav_runs"
    else:
        assert done == [(0,)]


def test_mounted_bot_dismounts_at_a_wall_and_only_maps_on_foot(tmp_path, monkeypatch):
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])                     # default vehicle (swoop), waypoint 0
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    world = World(clock, target=(400.0, 0.0), walls=WALL, right_is_clockwise=True)
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    s.raw["navigation"]["right_turn_sign"] = -1
    r = NavigationRunner(s, hud=SimHud(world), keys=SimKeys(world, clock),
                         screen=SimScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 600

    mapped_while_mounted = []
    real_mark = r.navigator.mark
    def spy(wx, wz, state, replan_now=False):
        ok = real_mark(wx, wz, state, replan_now)
        if ok and r.mounted:
            mapped_while_mounted.append((wx, wz))
        return ok
    r.navigator.mark = spy

    r.navigate()
    assert world.dismounts == 1
    assert r.current_vehicle == "running" and not r.mounted
    assert mapped_while_mounted == []
    assert world.dist() <= s.nav["stop_dist"]
