import heapq

import pytest

from jedai.navigation.dstar_lite import DStarLite
from jedai.navigation.grid_db import BLOCKED, CLEAR, UNEXPLORED, NavDatabase, world_to_cell

P = "naboo"


@pytest.fixture
def db(tmp_path):
    return NavDatabase(str(tmp_path / "nav.db"))


def test_grid_db_roundtrip(db):
    assert db.get_state(P, 12.0, 7.0) == UNEXPLORED
    db.mark_clear(P, 12.0, 7.0)
    assert db.get_state(P, 12.0, 7.0) == CLEAR
    db.mark_blocked(P, 30.0, 30.0)
    assert db.get_state(P, 30.0, 30.0) == BLOCKED
    # blocking subdivides a 5x5 block of 5m cells into 1m cells
    assert db.get_stats(P)["res1_blocked"] >= 25
    run = db.start_run(P, 0, 0, 0, 0)
    db.complete_run(run)


def _path(planner, start, max_steps=200):
    pos, out = start, []
    for _ in range(max_steps):
        planner.update_start(*pos)
        wp = planner.next_waypoint()
        if wp is None:
            break
        out.append(wp)
        pos = wp
    return out


def test_plans_around_a_wall(db):
    # wall of blocked cells at x=50..55 between start and goal, gap at z >= 40
    for z in range(-40, 40, 5):
        db.mark_blocked(P, 52.0, z + 2.5)
    pl = DStarLite(db, P, cost_unexplored=1.0)
    pl.set_goal(102.5, 2.5)
    pl.set_start(2.5, 2.5)
    pl.initialise()
    assert pl.has_path()
    path = _path(pl, (2.5, 2.5))
    assert path, "no path found"
    cells = {(world_to_cell(x, 5), world_to_cell(z, 5)) for x, z in path}
    wall = {(world_to_cell(52.0, 5), world_to_cell(z + 2.5, 5)) for z in range(-40, 40, 5)}
    assert not cells & wall, "path goes through the wall"
    assert abs(path[-1][0] - 102.5) <= 10 and abs(path[-1][1] - 2.5) <= 10


def test_no_node_loss_while_replanning(db):
    """
    Every node in the open set must still be in the heap. The original planner
    lost 10-24 nodes in this scenario (obstacles appearing while moving).
    """
    import random
    random.seed(2)
    pl = DStarLite(db, P)
    pl.set_goal(150.0, 0.0)
    pl.set_start(0.0, 0.0)
    pl.initialise()
    for _ in range(40):
        for _ in range(3):
            pl.update_cell(random.uniform(-50, 150), random.uniform(-50, 50), BLOCKED)
        wp = pl.next_waypoint()
        if wp is None:
            break
        pl.update_start(*wp)
        assert pl._open_set <= {node for _, node in pl._open}


def test_km_uses_last_update_position(db):
    pl = DStarLite(db, P)
    pl.set_goal(200.0, 0.0)
    pl.set_start(0.0, 0.0)
    pl.initialise()
    pl.update_start(5.0, 0.0)     # one cell
    pl.update_start(10.0, 0.0)    # one more cell
    # Paper: km = h(s0,s1) + h(s1,s2) = 5 + 5
    assert pl._km == pytest.approx(10.0)


def test_note_cell_reaches_planner_cache(db):
    """Cells the navigator writes to the DB must change the plan (cache miss fix)."""
    pl = DStarLite(db, P)
    pl.set_goal(52.5, 2.5)
    pl.set_start(2.5, 2.5)
    pl.initialise()
    first = pl.next_waypoint()
    db.mark_blocked(P, *first)
    pl.note_cell(*first, BLOCKED)
    assert pl.next_waypoint() != first


def test_clear_cost_preference_is_configurable(db):
    for x in range(0, 60, 5):          # a known-clear detour to the north
        db.mark_clear(P, x + 2.5, 22.5)
    a = DStarLite(db, P, cost_unexplored=3.0)
    b = DStarLite(db, P, cost_unexplored=1.0)
    for pl in (a, b):
        pl.set_goal(57.5, 2.5)
        pl.set_start(2.5, 2.5)
        pl.initialise()
    assert a.path_cost() > b.path_cost()


def test_marks_near_an_old_obstacle_are_not_ignored(db):
    """
    Regression: blocking a cell creates 1m sub-cells around it. A neighbour
    blocked later kept its stale 'unexplored' sub-cells, and get_state() (which
    prefers 1m data) returned unexplored. The original returned 0 here twice.
    """
    db.mark_blocked(P, 132.0, 12.0)
    db.mark_blocked(P, 142.0, 12.0)
    assert db.get_state(P, 142.0, 12.0) == BLOCKED
    db.mark_clear(P, 142.0, 12.0)
    assert db.get_state(P, 142.0, 12.0) == CLEAR
