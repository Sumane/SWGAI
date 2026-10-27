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


def test_project_target_ahead_and_behind():
    assert Navigator.project_target((0, 0), (1, 0), 100) == (100, 0)
    assert Navigator.project_target((0, 0), (1, 0), -5) is None


def test_steering_uses_angle_not_metres(nav):
    nav.heading = (1.0, 0.0)
    # 5 degrees off at 20m: sin(5°)=0.087 < 0.15 deadband -> straight.
    # (The old unnormalised cross product gave 1.7 here and steered.)
    x, z = 20 * math.cos(math.radians(5)), 20 * math.sin(math.radians(5))
    assert nav.steer_toward((0, 0), x, z) == 0
    # 30 degrees off -> steer, sign follows the cross product
    x, z = 20 * math.cos(math.radians(30)), 20 * math.sin(math.radians(30))
    assert nav.steer_toward((0, 0), x, z) == 1
    assert nav.steer_toward((0, 0), x, -z) == -1


def _drive(nav, start, heading, n, step=2.0):
    x, z = start
    for _ in range(n):
        x, z = x + heading[0] * step, z + heading[1] * step
        nav.update_heading((x, z))
    return x, z


def test_first_estimate_starts_planner(nav):
    pos = _drive(nav, (0, 0), (1, 0), 4)
    assert nav.heading_stable()
    nav.try_estimate_target(pos, 200, "N", dstar_straight=True)      # sets p1
    pos = _drive(nav, pos, (1, 0), 3)
    nav.try_estimate_target(pos, 194, "N", dstar_straight=True)
    assert nav.est_locked and nav.r.planner_active
    assert nav.target_wx == pytest.approx(pos[0] + 194)


def test_refresh_with_active_planner_does_not_crash_and_moves_goal(nav):
    """The original f-string crashed exactly here."""
    pos = _drive(nav, (0, 0), (1, 0), 4)
    nav.try_estimate_target(pos, 200, "N", True)
    pos = _drive(nav, pos, (1, 0), 3)
    nav.try_estimate_target(pos, 194, "N", True)
    old_goal = nav.r.planner.goal
    # distance drifts > 50m -> refresh allowed
    nav.try_estimate_target(pos, 300, "N", True)
    pos = _drive(nav, pos, (1, 0), 6)
    nav.try_estimate_target(pos, 290, "N", True)
    assert nav.r.planner.goal != old_goal
    assert nav.r.planner.has_path()


def test_estimate_ignored_unless_arrow_north(nav):
    pos = _drive(nav, (0, 0), (1, 0), 6)
    nav.try_estimate_target(pos, 100, "NE", True)
    nav.try_estimate_target(pos, 100, "NE", True)
    assert not nav.est_locked


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


def test_attempt_cell_marks_blocked_when_no_progress(nav, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    nav.r.read_position = lambda: (0.0, 0.0)          # bot never moves
    nav.heading = (0.95, 0.31)                         # already facing the cell
    ok, _ = nav.attempt_cell((0, 0), 7.5, 2.5, duration=0.01)
    assert not ok and nav.r.db.get_state("naboo", 7.5, 2.5) == BLOCKED
