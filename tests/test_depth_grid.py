"""Depth -> cells geometry, on a synthetic first-person scene with a known wall."""
import math

import numpy as np

from jedai.perception.depth_grid import (BLOCKED, CLEAR, depth_to_points, fit_ground_scale,
                                         intrinsics, occupancy, to_world)


def synthetic_scene(w=640, h=360, hfov=60.0, eye=1.7, wall_at=12.0, wall_x=(-3.0, 3.0), a=7.0, b=0.2):
    """Relative inverse depth (rel = a/Z + b) for flat ground with a 4 m high wall ahead."""
    f, cx, cy = intrinsics(w, h, hfov)
    v, u = np.mgrid[0:h, 0:w].astype(float)
    z = np.full((h, w), 1e6)                                   # sky
    below = v > cy + 0.5
    z[below] = eye * f / (v[below] - cy)                       # ground
    zw = wall_at                                               # wall plane at z = wall_at
    x_at_wall = (u - cx) / f * zw
    y_up_at_wall = eye - (v - cy) / f * zw
    on_wall = (x_at_wall > wall_x[0]) & (x_at_wall < wall_x[1]) & (y_up_at_wall > 0) & \
              (y_up_at_wall < 4.0) & (z > zw)
    z[on_wall] = zw
    return a / z + b, f, cx, cy


def test_scale_fit_recovers_the_depth_model():
    rel, f, cx, cy = synthetic_scene()
    a, b = fit_ground_scale(rel, f, cy, 1.7)
    assert abs(a - 7.0) < 0.2 and abs(b - 0.2) < 0.02


def test_wall_ahead_is_blocked_and_ground_before_it_clear():
    rel, f, cx, cy = synthetic_scene(wall_at=12.0)
    a, b = fit_ground_scale(rel, f, cy, 1.7)
    right, up, fwd = depth_to_points(rel, a, b, f, cx, cy, 1.7, step=2)
    grid, rows, cols = occupancy(right, up, fwd)
    mid = grid.shape[1] // 2
    assert grid[0, mid] == CLEAR or grid[1, mid] == CLEAR     # 0-10 m ahead: ground
    assert grid[2, mid] == BLOCKED                            # 10-15 m: the wall
    assert grid[3, mid] != CLEAR                              # behind the wall: not clear


def test_relative_cells_land_in_the_right_world_cells():
    rel, f, cx, cy = synthetic_scene(wall_at=12.0)
    a, b = fit_ground_scale(rel, f, cy, 1.7)
    grid, rows, cols = occupancy(*depth_to_points(rel, a, b, f, cx, cy, 1.7, step=2))
    cells = dict(to_world(grid, rows, cols, pos=(100.0, 200.0), facing=(0.0, 1.0)))   # facing +Z
    assert cells[(100.0, 212.5)] == BLOCKED                    # 12.5 m straight ahead
