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
from jedai.navigation.grid_db import BLOCKED
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
        self.slide = True      # walking into a wall at an angle slides along it (as in SWG)
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
        back = "BACK" in self.held and not ("FORWARD" in self.held or self.autorun)
        if "FORWARD" in self.held or self.autorun or back:
            # Move in <=1 m sub-steps and stop at the first wall. (One big step
            # per key event let the bot tunnel through a 10 m wall during a
            # 3 s hold, which made a wall test pass for the wrong reason.)
            total = self.SPEED * dt * (-0.5 if back else 1.0)   # reversing is slower
            n = max(1, int(math.ceil(total)))
            for _ in range(n):
                sx, sz = math.cos(self.yaw) * total / n, math.sin(self.yaw) * total / n
                if not self.blocked(self.x + sx, self.z + sz):
                    self.x, self.z = self.x + sx, self.z + sz
                elif self.slide and abs(sx) > 0.2 * abs(total / n) and not self.blocked(self.x + sx, self.z):
                    self.x += sx          # slide along a wall, like the game does
                elif self.slide and abs(sz) > 0.2 * abs(total / n) and not self.blocked(self.x, self.z + sz):
                    self.z += sz
                else:
                    break
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
    def __init__(self, world, misread_every=0):
        self.w = world
        self.misread_every = misread_every   # every Nth distance gets an extra digit, like the real OCR
        self._n = 0

    def _distance(self):
        d = self.w.dist()
        self._n += 1
        if self.misread_every and self._n % self.misread_every == 0:
            return int("9" + str(d)) if self._n % 2 else int(str(d)[0] + str(d))
        return d

    def read_coords(self, frame):
        x, z = self.w.reported_position()
        return float(round(x)), 0.0, float(round(z))     # the HUD shows whole metres

    def waypoints(self, frame, should_stop=None):
        return iter([Waypoint("Theed Starport", self._distance(), (0, 0), self.w.arrow())])


class SimScreen:
    def __init__(self, world, clock):
        self.w, self.c = world, clock

    def grab(self):
        self.c.sleep(0.05)       # each frame costs time
        self.w.step()
        return np.zeros((2, 2, 3), np.uint8)


WALL = ((140, -60, 150, 60),)
POCKET = ((140, -60, 150, 60), (60, 60, 150, 70), (60, -70, 150, -60))


@pytest.mark.parametrize("walls,clockwise,sign,arrives", [
    ((), True, -1, True),            # X east / Z north world, correct setting
    ((), False, 1, True),            # mirrored world, original setting
    (WALL, True, -1, True),          # wall in the way
    # WRONG setting. v0.20 never arrived like this. Now it still does: until D*
    # takes over the bot follows the arrow, which is screen-relative and doesn't
    # use the setting at all. The setting still matters for D* steering.
    ((), True, 1, True),
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
    r.should_stop = lambda: clock.t > 1000 + 900        # give up after 15 sim-minutes

    r.navigate()
    import sqlite3
    done = sqlite3.connect(tmp_path / "nav.db").execute("select completed from nav_runs").fetchall()
    if arrives:
        assert world.dist() <= s.nav["stop_dist"], \
            f"ended {world.dist()}m away at ({world.x:.0f},{world.z:.0f})"
        assert done == [(1,)], "arrival not recorded in nav_runs"
    else:
        assert done == [(0,)]


def test_mounted_bot_dismounts_at_a_wall_and_only_blocks_on_foot(tmp_path, monkeypatch):
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])                     # default vehicle (swoop), waypoint 0
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    # A dead-end pocket: a swoop can slide along a plain wall now, but not out of this
    world = World(clock, target=(400.0, 0.0), walls=POCKET, right_is_clockwise=True)
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    s.raw["navigation"]["right_turn_sign"] = -1
    r = NavigationRunner(s, hud=SimHud(world), keys=SimKeys(world, clock),
                         screen=SimScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 900

    mapped_while_mounted = []
    real_mark = r.navigator.mark
    def spy(wx, wz, state, replan_now=False):
        ok = real_mark(wx, wz, state, replan_now)
        if ok and r.mounted and state == BLOCKED:
            mapped_while_mounted.append((wx, wz))
        return ok
    r.navigator.mark = spy

    r.navigate()
    assert world.dismounts == 1
    assert r.current_vehicle == "running" and not r.mounted
    assert mapped_while_mounted == []
    assert world.dist() <= s.nav["stop_dist"]


@pytest.mark.parametrize("target,misread_every", [((2900.0, 0.0), 0), ((-1500.0, 2600.0), 0),
                                                  ((900.0, -400.0), 7)],
                         ids=["2.9km-east", "3km-north-west", "1km-with-ocr-misreads"])
def test_far_waypoint_is_reached(tmp_path, monkeypatch, target, misread_every):
    """
    Marc's first in-game drive: a waypoint ~2.9 km away, swoop with lagging
    coordinates. v0.20 projected the target once from a single arrow=N reading
    (±22.5° = up to ±1100 m sideways at that range) and D* drove off to that
    point. The trilateration estimate must get the bot there.
    """
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    world = World(clock, target=target, right_is_clockwise=True, mounted_lag_s=0.5)
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    r = NavigationRunner(s, hud=SimHud(world, misread_every), keys=SimKeys(world, clock),
                         screen=SimScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 600
    r.navigate()
    assert world.dist() <= s.nav["stop_dist"], f"ended {world.dist()}m away"
    est = (r.navigator.target_wx, r.navigator.target_wz)
    # whole-metre coordinates (like the HUD) and 0.5 s lag: within ~100 m at 3 km
    # is plenty, the bot homes in on the arrow/distance at the end anyway
    assert math.hypot(est[0] - target[0], est[1] - target[1]) < 100


def _city(seed, n=45):
    import random
    rnd = random.Random(seed)
    walls = []
    for _ in range(n):
        x, z = rnd.uniform(60, 700), rnd.uniform(-250, 250)
        w, h = rnd.uniform(15, 60), rnd.uniform(15, 60)
        if math.hypot(x, z) < 40 or math.hypot(x - 800, z) < 40:
            continue
        walls.append((x, z, x + w, z + h))
    return tuple(walls)


class SlowScreen(SimScreen):
    """~2 s per tick like the real HUD reading (two grabs per tick)."""
    def grab(self):
        self.c.sleep(1.0)
        self.w.step()
        return np.zeros((2, 2, 3), np.uint8)


@pytest.mark.parametrize("seed", range(6))
def test_city_with_slow_vision_does_not_run_off(tmp_path, monkeypatch, seed):
    """
    Marc's second and third in-game drives (Theed): with ~2 s vision ticks the
    old cell-by-cell recalibration overshot, marked clear cells as walls, and
    the bot kept heading away from the waypoint. Buildings, 2 s ticks, coord
    lag and an OCR misread every 6th distance: it must get there without ever
    ending up much further away than it started.
    """
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    world = World(clock, target=(800.0, 0.0), walls=_city(seed), right_is_clockwise=True,
                  mounted_lag_s=0.5)
    worst = [0]
    step = world.step
    def tracked():
        step()
        worst[0] = max(worst[0], world.dist())
    world.step = tracked
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    r = NavigationRunner(s, hud=SimHud(world, misread_every=6), keys=SimKeys(world, clock),
                         screen=SlowScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 900
    r.navigate()
    assert world.dist() <= s.nav["stop_dist"], f"ended {world.dist()}m away"
    assert worst[0] <= 800 + 100, f"got {worst[0]}m away (started 800m)"


def test_run_is_recorded(tmp_path, monkeypatch):
    """Every run leaves a folder with ticks, events, screenshots and a report."""
    monkeypatch.delenv("JEDAI_NO_RECORD", raising=False)
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    world = World(clock, target=(400.0, 0.0), walls=WALL, right_is_clockwise=True)
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    s.raw["paths"]["debug_dir"] = str(tmp_path / "runs")
    r = NavigationRunner(s, hud=SimHud(world), keys=SimKeys(world, clock),
                         screen=SimScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 900
    r.navigate()
    (run,) = list((tmp_path / "runs").iterdir())
    for name in ("ticks.jsonl", "events.jsonl", "console.log", "report.html", "path.png",
                 "distance.png", "summary.txt"):
        assert (run / name).exists(), name
    assert list((run / "shots").glob("*.jpg"))
    kinds = (run / "events.jsonl").read_text()
    assert '"stuck"' in kinds and ('"unstick"' in kinds or '"follow"' in kinds) and '"end"' in kinds


@pytest.mark.parametrize("walls", [
    ((140, -150, 150, 150),),                                           # 300 m wall
    ((140, -100, 150, 100), (40, 100, 150, 110), (40, -110, 150, -100)),  # dead-end pocket
], ids=["300m-wall", "dead-end-pocket"])
def test_long_wall_and_pocket_with_slow_vision(tmp_path, monkeypatch, walls):
    """
    In-game (Theed): stuck 31 times within 20 m, D* steering straight back into
    the same wall after every unstick. Wall-following (angled into the wall so
    the game slides the bot along it) gets round; v0.20 logic and the first
    unstick version both failed these in 15 minutes.
    """
    clock = Clock()
    for mod in (nav_mod, runner_mod):
        monkeypatch.setattr(mod.time, "time", clock.time)
        monkeypatch.setattr(mod.time, "sleep", clock.sleep)
    answers = iter(["", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    world = World(clock, target=(400.0, 0.0), walls=walls, right_is_clockwise=True)
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    r = NavigationRunner(s, hud=SimHud(world), keys=SimKeys(world, clock),
                         screen=SlowScreen(world, clock))
    r.vel_tracker._clock = clock.time
    r.should_stop = lambda: clock.t > 1000 + 900
    r.navigate()
    assert world.dist() <= s.nav["stop_dist"], f"ended {world.dist()}m away"
