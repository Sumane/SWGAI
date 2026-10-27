import math

import pytest

from jedai.config import load_settings
from jedai.navigation.dstar_lite import DStarLite
from jedai.navigation.grid_db import BLOCKED, CLEAR, NavDatabase
from jedai.navigation.navigator import Navigator

CFG = load_settings(local_file="none.yaml").nav


class FakeKeys:
    def __init__(self):
        self.log = []

    def __getattr__(self, name):
        return lambda *a, **k: self.log.append((name, a))


class FakeRunner:
    """Minimal stand-in for NavigationRunner."""

    def __init__(self, tmp_path):
        self.planet = "naboo"
        self.db = NavDatabase(str(tmp_path / "nav.db"))
        self.planner = DStarLite(self.db, self.planet)
        self.planner_active = False
        self.keys = FakeKeys()
        self.positions = []
        self.distance = None

    def read_position(self):
        return self.positions.pop(0) if self.positions else None

    def read_target_distance(self):
        return self.distance

    def should_stop(self):
        return False


@pytest.fixture
def nav(tmp_path):
    return Navigator(FakeRunner(tmp_path), CFG)


def test_steering_uses_angle_not_metres(nav):
    nav.heading = (1.0, 0.0)
    # 5 degrees off at 20m: sin(5°)=0.087 < 0.15 deadband -> straight.
    # (The old unnormalised cross product gave 1.7 here and steered.)
    x, z = 20 * math.cos(math.radians(5)), 20 * math.sin(math.radians(5))
    assert nav.steer_toward((0, 0), x, z) == 0
    # 30 degrees off. Heading east, target 30° anticlockwise (north-east).
    x, z = 20 * math.cos(math.radians(30)), 20 * math.sin(math.radians(30))
    # Measured SWG convention (right_turn_sign -1): RIGHT turns clockwise, so turn LEFT
    nav.cfg = dict(nav.cfg, right_turn_sign=-1)
    assert nav.steer_toward((0, 0), x, z) == -1
    assert nav.steer_toward((0, 0), x, -z) == 1
    # Original v0.20 assumption (sign 1) gave the opposite
    nav.cfg = dict(nav.cfg, right_turn_sign=1)
    assert nav.steer_toward((0, 0), x, z) == 1


def test_target_behind_keeps_turning_one_way(nav):
    """Sim failure: left/right/left taps for a target behind = drove 700 m the wrong way."""
    nav.heading = (1.0, 0.0)
    first = nav.steer_toward((0, 0), -100, 1)
    assert first != 0
    assert nav.steer_toward((0, 0), -100, -1) == first     # wobble across the line: same side
    nav.heading = (0.0, 1.0)
    nav.steer_toward((0, 0), -100, 0)
    nav.heading = (-1.0, 0.0)                               # turned round
    assert nav.steer_toward((0, 0), -100, 0) == 0


def _drive(nav, start, heading, n, step=2.0):
    x, z = start
    for _ in range(n):
        x, z = x + heading[0] * step, z + heading[1] * step
        nav.update_heading((x, z))
    return x, z


def _arrow(pos, heading, target):
    """HUD arrow, clockwise from facing (measured SWG convention)."""
    b = math.atan2(target[1] - pos[1], target[0] - pos[0])
    rel = (math.atan2(heading[1], heading[0]) - b) % (2 * math.pi)
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((math.degrees(rel) + 22.5) // 45) % 8]


def _observe_drive(nav, target, start, heading, metres, turn_deg_per_m=0.0):
    x, z = start
    yaw = math.atan2(heading[1], heading[0])
    for _ in range(int(metres / 2)):
        yaw += math.radians(turn_deg_per_m) * 2
        x, z = x + 2 * math.cos(yaw), z + 2 * math.sin(yaw)
        nav.update_heading((x, z))
        d = int(math.hypot(target[0] - x, target[1] - z))
        nav.observe_target((x, z), d, _arrow((x, z), (math.cos(yaw), math.sin(yaw)), target))
    return x, z


def test_far_target_found_from_distances_not_one_arrow_reading(nav):
    """
    Marc's first real drive: target 2.9 km away, 15° off the arrow's centre.
    v0.20 projected the target once along the heading (~750 m sideways out)
    and D* drove there. Readings along a curve must pin it down.
    """
    target = (2900.0, 0.0)
    _observe_drive(nav, target, (0, 0), (math.cos(math.radians(40)), math.sin(math.radians(40))),
                   600, turn_deg_per_m=-0.1)
    assert nav.est_locked and nav.r.planner_active
    err = math.hypot(nav.target_wx - target[0], nav.target_wz - target[1])
    assert err < 100, err
    gx, gz = nav._goal_xy
    assert math.hypot(gx - target[0], gz - target[1]) < 150


def test_no_d_star_goal_until_confident(nav):
    """Driving straight at it, sideways position is unknown: follow the arrow, no D* goal."""
    _observe_drive(nav, (2900.0, 0.0), (0, 0), (1.0, 0.0), 100)
    assert not nav.est_locked and not nav.r.planner_active
    assert nav.target_wx is not None          # still a best guess for recalibration


def test_goal_moves_when_estimate_improves(nav):
    target = (300.0, 100.0)
    pos = _observe_drive(nav, target, (0, 0), (1.0, 0.0), 100)
    first = nav._goal_xy
    assert first is not None
    _observe_drive(nav, target, pos, (0.0, 1.0), 60)        # sideways: better geometry
    assert math.hypot(nav._goal_xy[0] - target[0], nav._goal_xy[1] - target[1]) < 30
    assert nav.r.planner.has_path()


def test_arrow_fallback_turns_round_when_target_behind(nav):
    assert nav.tick((0, 0), 1.0, "S", 100) != 0
    assert nav.tick((0, 0), 1.0, "SE", 100) == 1
    assert nav.tick((0, 0), 1.0, "SW", 100) == -1


def test_enter_recal_without_position_does_not_crash(nav):
    nav._recal_origin = (0, 0)
    assert nav._enter_recal(None) == "STOP"
    assert nav.state == Navigator.RECALIBRATING


def test_recal_candidates_skip_blocked(nav):
    nav.r.db.mark_blocked("naboo", 7.5, 2.5)
    cands = nav.recal_candidates((2.5, 2.5))
    assert len(cands) == 7
    assert all((c[1], c[2]) != (7.5, 2.5) for c in cands)


def test_tick_arrow_fallback_and_stop_debounce(nav):
    assert nav.tick((0, 0), 1.0, "NW", 100) == -1
    assert nav.tick((0, 0), 1.0, "E", 100) == 1
    results = [nav.tick((0, 0), 0.0, "N", 100) for _ in range(CFG["stop_debounce"])]
    assert results[-1] == "STOP"
    assert nav.tick((0, 0), 0.0, "N", 100) == "RECAL"


def test_clear_marks_reach_planner(nav):
    nav.r.planner.set_goal(100, 0)
    nav.r.planner.set_start(0, 0)
    nav.r.planner.initialise()
    nav.r.planner_active = True
    nav.mark_clear_interpolated((0, 0), 1.0, now=10.0)    # first call: remember start
    nav.mark_clear_interpolated((20, 0), 1.0, now=11.0)   # 20m later: stamp the line
    assert nav.r.db.get_state("naboo", 10, 0) == CLEAR
    assert nav.r.planner._state((2, 0)) == CLEAR


def test_attempt_cell_never_marks_blocked(nav, monkeypatch):
    """In-game: probes too short to get moving said "moved 0.0m" and walled in clear cells."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    nav.r.read_position = lambda: (0.0, 0.0)          # bot never moves
    nav.heading = (0.95, 0.31)                         # already facing the cell
    ok, _ = nav.attempt_cell((0, 0), 7.5, 2.5, duration=0.01)
    assert not ok and nav.r.db.get_state("naboo", 7.5, 2.5) != BLOCKED


def test_unstick_backs_off_turns_and_goes(nav, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    positions = iter([(0.0, 0.0), (0.0, -10.0)])
    nav.r.read_position = lambda: next(positions, (0.0, -10.0))
    nav.cfg = dict(nav.cfg, wall_follow=False)
    nav.heading = (1.0, 0.0)
    nav._last_arrow = "E"                              # target on the right
    assert nav.do_recal((0, 0)) is True and nav.state == Navigator.MOVING
    names = [n for n, _ in nav.r.keys.log]
    assert names.index("key_down") < names.index("tap")          # back off before turning
    assert ("tap", ("RIGHT", 0.45)) in nav.r.keys.log            # 45° at 100°/s
    assert nav.heading == pytest.approx((0.0, -1.0))             # measured from the move


# --- mounted vs on foot ------------------------------------------------------

def test_mounted_maps_clear_but_not_blocked(nav):
    nav.r.mounted = True
    nav.mark_clear_interpolated((0, 0), 1.0, now=10.0)
    nav.mark_clear_interpolated((20, 0), 1.0, now=11.0)
    assert nav.r.db.get_state("naboo", 10, 0) == CLEAR          # driven through = clear
    assert nav.mark(30, 30, BLOCKED, replan_now=True) is False  # can't confirm on a vehicle
    assert nav.r.db.get_state("naboo", 30, 30) != BLOCKED
    nav.r.mounted = False
    assert nav.mark(30, 30, BLOCKED) is True
    assert nav.r.db.get_state("naboo", 30, 30) == BLOCKED


def test_mounted_mapping_none_option(nav):
    nav.r.mounted = True
    nav.cfg = dict(nav.cfg, mounted_mapping="none")
    assert nav.mark(10, 0, CLEAR) is False


def test_stuck_twice_same_place_on_vehicle_dismounts(nav):
    nav.r.mounted = True
    nav.heading = (1.0, 0.0)
    assert nav._enter_recal((100, 100)) == "STOP"           # first time: recalibrate
    assert nav.r.db.get_state("naboo", 101, 100) != BLOCKED  # ...without marking a wall
    assert nav._enter_recal((105, 102)) == "DISMOUNT"        # second time within 15m


def test_stuck_in_different_places_does_not_dismount(nav):
    nav.r.mounted = True
    nav.heading = (1.0, 0.0)
    assert nav._enter_recal((100, 100)) == "STOP"
    assert nav._enter_recal((300, 100)) == "STOP"            # 200m away: new place


def test_on_foot_never_dismounts_and_maps(nav):
    nav.r.mounted = False
    nav.heading = (1.0, 0.0)
    for _ in range(3):
        assert nav._enter_recal((100, 100)) == "STOP"
    assert nav.r.db.get_state("naboo", 105, 100) == BLOCKED   # the cell in front
    assert nav.r.db.get_state("naboo", 105, 95) != BLOCKED    # never the cells beside it
    assert nav.r.db.get_state("naboo", 105, 105) != BLOCKED


def test_stuck_marks_only_dstar_next_cell(nav):
    """Marc's rule: can't get into the cell D* is routing to -> that cell is blocked, D* reroutes."""
    nav.r.mounted = False
    nav.r.planner.set_goal(100, 2.5)
    nav.r.planner.set_start(2.5, 2.5)
    nav.r.planner.initialise()
    nav.r.planner_active = True
    first = nav.r.planner.get_planned_path(max_steps=2)[1]
    nav.heading = (0.0, 1.0)                    # facing doesn't matter when D* has a route
    nav._enter_recal((2.5, 2.5))
    assert nav.r.db.get_state("naboo", *first) == BLOCKED
    blocked = [(x, z) for x in range(-5, 15, 5) for z in range(-5, 15, 5)
               if nav.r.db.get_state("naboo", x + 2.5, z + 2.5) == BLOCKED]
    assert len(blocked) == 1                    # only that one
    assert nav.r.planner.get_planned_path(max_steps=2)[1] != first   # rerouted


def test_stuck_starts_wall_following_angled_into_the_wall(nav, monkeypatch):
    """Wall-following is off by default now (in-game it drove big circles); still works if enabled."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    nav.cfg = dict(nav.cfg, wall_follow=True)
    nav.heading = (1.0, 0.0)                     # facing the wall (east)
    nav._last_arrow = "E"                        # target to the right
    assert nav.do_recal((0, 0)) is True
    esc = nav._escape
    assert esc["why"] == "follow"
    # RIGHT of east is south (-Z) with the measured convention; 60° from the wall
    assert esc["desired"][1] < -0.8 and esc["desired"][0] > 0.4
    # sliding south along the wall keeps following
    assert nav._follow_step((0.0, -5.0), esc) is True
    # moving freely the way it faces twice = past the wall
    p = (0.0, -5.0)
    for _ in range(2):
        p = (p[0] + esc["desired"][0] * 5, p[1] + esc["desired"][1] * 5)
        still = nav._follow_step(p, esc)
    assert still is False
