"""
Depth image -> blocked/clear cells in front of the player.

The standard robot approach (depth -> 3D points -> occupancy grid), for SWG
in first person with the HUD hidden:

  1. Depth Anything V2 gives *relative inverse depth* per pixel
     (bigger = closer, unknown scale and offset).
  2. In first person the camera is at a fixed eye height and (if the view is
     level) the ground in the lower half of the image is a plane. For a level
     pinhole camera a ground pixel at row v is at distance
         Z = eye_height * f / (v - cy)          so 1/Z is linear in v.
     Fitting   relative = a * (1/Z) + b   on pixels that are probably ground
     (bottom middle of the image) gives the scale, so every pixel gets a
     distance in metres.
  3. Back-project each pixel to (right, up, forward) metres. A point's height
     above the ground tells walkable (about 0) from obstacle (well above).
  4. Bin into cells relative to the player: forward/right in metres. Rotate
     by the facing and add the position to get world cells for D*.

Pure numpy, no torch: the model is run elsewhere and passed in.
Known blind spots: water looks like ground, thin railings may vanish into
the background, a cliff edge reads as far ground (handled crudely by the
"ground drops away" check), and depth is noisy beyond ~30-35 m.
"""

from __future__ import annotations

import math

import numpy as np

UNKNOWN, CLEAR, BLOCKED = 0, 1, 2


def intrinsics(width: int, height: int, hfov_deg: float):
    """Focal length (pixels) and principal point for a horizontal field of view."""
    f = (width / 2) / math.tan(math.radians(hfov_deg) / 2)
    return f, width / 2, height / 2


def fit_ground_scale(rel: np.ndarray, f: float, cy: float, eye_height: float,
                     pitch_deg: float = 0.0, band=(0.70, 0.95), cols=(0.35, 0.65)):
    """
    Fit rel = a * invZ + b on a bottom-middle band assumed to be ground.
    Returns (a, b) or None if the fit is unusable (e.g. a wall right in front).
    """
    h, w = rel.shape
    r0, r1 = int(band[0] * h), int(band[1] * h)
    c0, c1 = int(cols[0] * w), int(cols[1] * w)
    v = np.arange(r0, r1, dtype=np.float64)[:, None].repeat(c1 - c0, axis=1)
    # alpha: ray angle below the optical axis; ang: below horizontal (pitch +ve = looking down)
    alpha = np.arctan((v - cy) / f)
    ang = alpha + math.radians(pitch_deg)
    ok = ang > math.radians(1.0)
    # ground hit: range R = h / sin(ang); depth along the optical axis z = R cos(alpha)
    inv_z = np.where(ok, np.sin(ang) / (eye_height * np.cos(alpha)), 0.0)
    x = inv_z[ok].ravel()
    y = rel[r0:r1, c0:c1][ok].ravel().astype(np.float64)
    if x.size < 50:
        return None
    # robust least squares: two passes, dropping the worst 20% (an obstacle in the band)
    keep = np.ones_like(x, dtype=bool)
    for _ in range(2):
        A = np.stack([x[keep], np.ones(keep.sum())], 1)
        sol, *_ = np.linalg.lstsq(A, y[keep], rcond=None)
        res = np.abs(sol[0] * x + sol[1] - y)
        keep = res <= np.quantile(res, 0.8)
    a, b = float(sol[0]), float(sol[1])
    if a <= 0:
        return None
    return a, b


def depth_to_points(rel: np.ndarray, a: float, b: float, f: float, cx: float, cy: float,
                    eye_height: float, pitch_deg: float = 0.0, step: int = 4, max_range: float = 40.0):
    """
    Pixels -> points (right, up_above_ground, forward) in metres, sampled every `step` px.
    Camera frame: x right, y down, z along the optical axis; then pitched to level.
    """
    h, w = rel.shape
    vs, us = np.mgrid[0:h:step, 0:w:step]
    d = rel[vs, us].astype(np.float64) - b
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        z_cam = np.where(d > 1e-6, a / d, np.inf)            # distance along the optical axis
        x_cam = (us - cx) / f * z_cam
        y_cam = (vs - cy) / f * z_cam                          # down
        p = math.radians(pitch_deg)                            # rotate camera->level frame
        fwd = z_cam * math.cos(p) - y_cam * math.sin(p)
        down = z_cam * math.sin(p) + y_cam * math.cos(p)
        up = eye_height - down                                 # height above the ground plane
    ok = np.isfinite(fwd) & np.isfinite(up) & (fwd > 0.5) & (fwd < max_range)
    return x_cam[ok], up[ok], fwd[ok]


def occupancy(right, up, fwd, cell: float = 5.0, max_range: float = 35.0, width_m: float = 35.0,
              obstacle_min: float = 0.5, obstacle_max: float = 3.0, ground_tol: float = 0.35,
              min_points: int = 6):
    """
    Bin points into a player-relative grid: rows = forward bands, cols = left..right.
    A cell is BLOCKED if enough points sit between obstacle_min and obstacle_max
    above the ground, CLEAR if enough sit on the ground and none block it,
    otherwise UNKNOWN. Returns (grid, row_edges, col_edges).
    """
    nf = int(math.ceil(max_range / cell))
    nr = int(math.ceil(width_m / cell))
    if nr % 2 == 0:
        nr += 1                                             # a column straight ahead
    half = nr * cell / 2
    gi = np.floor(fwd / cell).astype(int)
    gj = np.floor((right + half) / cell).astype(int)
    inside = (gi >= 0) & (gi < nf) & (gj >= 0) & (gj < nr)
    gi, gj, u = gi[inside], gj[inside], up[inside]
    blocked = np.zeros((nf, nr), int)
    ground = np.zeros((nf, nr), int)
    np.add.at(blocked, (gi, gj), ((u > obstacle_min) & (u < obstacle_max)).astype(int))
    np.add.at(ground, (gi, gj), (np.abs(u) < ground_tol).astype(int))
    grid = np.full((nf, nr), UNKNOWN, int)
    grid[(ground >= min_points) & (blocked < min_points)] = CLEAR
    grid[blocked >= min_points] = BLOCKED
    # anything further along a column than a blocked cell is behind the wall: unknown
    for j in range(nr):
        hit = np.where(grid[:, j] == BLOCKED)[0]
        if hit.size:
            grid[hit[0] + 1:, j] = np.where(grid[hit[0] + 1:, j] == BLOCKED, BLOCKED, UNKNOWN)
    return grid, np.arange(nf + 1) * cell, np.arange(nr + 1) * cell - half


def to_world(grid, row_edges, col_edges, pos, facing):
    """
    Player-relative grid -> list of ((world_x, world_z), state) at cell centres.
    facing: unit (x, z) direction the player looks. "Right" is clockwise of
    facing when right_turn_sign is -1 (the measured SWG convention).
    """
    fx, fz = facing
    rx, rz = fz, -fx                                       # clockwise 90° in X/Z
    out = []
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            if grid[i, j] == UNKNOWN:
                continue
            fwd = (row_edges[i] + row_edges[i + 1]) / 2
            rt = (col_edges[j] + col_edges[j + 1]) / 2
            out.append(((pos[0] + fx * fwd + rx * rt, pos[1] + fz * fwd + rz * rt), int(grid[i, j])))
    return out


def render(frame_bgr, rel, grid, row_edges, col_edges, scale_px: int = 12):
    """Side-by-side picture: frame | depth | bird's-eye grid (player at the bottom)."""
    import cv2

    h = 360
    fr = cv2.resize(frame_bgr, (int(frame_bgr.shape[1] * h / frame_bgr.shape[0]), h))
    dn = (rel - rel.min()) / max(float(rel.max() - rel.min()), 1e-6)
    dv = cv2.applyColorMap((dn * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    dv = cv2.resize(dv, (fr.shape[1], h))
    nf, nr = grid.shape
    cell_px = max(8, min(h // nf, 400 // nr))
    bev = np.full((nf * cell_px + 30, nr * cell_px + 20, 3), 30, np.uint8)
    col = {UNKNOWN: (70, 70, 70), CLEAR: (60, 170, 60), BLOCKED: (40, 40, 200)}
    for i in range(nf):
        for j in range(nr):
            y0 = (nf - 1 - i) * cell_px + 5
            x0 = j * cell_px + 10
            cv2.rectangle(bev, (x0, y0), (x0 + cell_px - 2, y0 + cell_px - 2), col[int(grid[i, j])], -1)
    cv2.circle(bev, (10 + nr * cell_px // 2, nf * cell_px + 15), 5, (255, 255, 255), -1)
    cv2.putText(bev, f"{row_edges[-1]:.0f}m", (2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1)
    bev = cv2.resize(bev, (int(bev.shape[1] * h / bev.shape[0]), h))
    return np.hstack([fr, dv, bev])
