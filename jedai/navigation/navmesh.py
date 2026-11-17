"""
Core3 navmesh files -> the bot's 5 m cells.

Core3 (`./core3 dumpNavMeshesToFile`) writes one file per city/region to
bin/navmeshes/<planet>_<city|region>_<name>. Format (Core3 RecastNavMesh.cpp,
32-bit Detour refs):

    NavMeshSetHeader  {int magic 'MSET', int version, int numTiles,
                       dtNavMeshParams {float orig[3], tileW, tileH, int maxTiles, maxPolys}}
    numTiles x { NavMeshTileHeader {uint tileRef, int dataSize}, tile data }

Tile data is standard Detour (version 7): dtMeshHeader (100 bytes), then
verts (float x3 each), then polys (32 bytes each), then links/detail/BV
which we don't need.

Axes: Core3 feeds Recast (x, height, -y), where x, y are the game's /loc
coordinates, so   game_x = rx,   game_y (the bot's "z", north) = -rz,
height = ry. Cell index = floor(coord / cell_size), same as navigator.py.

Coverage only exists inside cities and points of interest: open terrain
outside a navmesh's bounds is UNKNOWN, not blocked.
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass, field

import numpy as np

MSET = (ord("M") << 24) | (ord("S") << 16) | (ord("E") << 8) | ord("T")
DNAV = (ord("D") << 24) | (ord("N") << 16) | (ord("A") << 8) | ord("V")
DT_POLYTYPE_OFFMESH = 1

_SET_HDR = struct.Struct("<iii3fffii")       # 40 bytes
_TILE_HDR = struct.Struct("<Ii")             # 8 bytes
_MESH_HDR = struct.Struct("<15i10f")         # 100 bytes
_POLY = struct.Struct("<I6H6HHBB")           # 32 bytes


@dataclass
class NavMesh:
    name: str
    planet: str
    polys: list = field(default_factory=list)      # list of (N,3) arrays in game coords (x, y_north, height)
    bmin: tuple = (math.inf, math.inf)
    bmax: tuple = (-math.inf, -math.inf)
    walkable_height: float = 0.0
    walkable_radius: float = 0.0
    walkable_climb: float = 0.0
    component: np.ndarray | None = None             # connected-component id per poly
    main: int = -1                                  # id of the biggest (ground) component
    adj: list = field(default_factory=list)         # per poly: {neighbour index: portal midpoint (x, y, h)}

    def ground_polys(self):
        """Polys connected to the main ground mesh (drops rooftops and other islands)."""
        return [p for p, c in zip(self.polys, self.component) if c == self.main]


def _area(p) -> float:
    x, y = p[:, 0], p[:, 1]
    return abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))) / 2


def _components(polys, links, portals=None):
    """
    Union polys that share an edge. Inside a tile Detour stores the neighbour
    index; across tiles it only flags "external", so external edges are matched
    by lying on the same tile border with overlapping spans and similar height.
    """
    par = list(range(len(polys)))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    ext = {}
    for k, (neis, base) in enumerate(links):
        pts = polys[k]
        for e, nv in enumerate(neis):
            if nv == 0:
                continue
            a, c = pts[e], pts[(e + 1) % len(pts)]
            if nv & 0x8000:
                if abs(a[0] - c[0]) < 1e-3:
                    key, lo, hi = ("x", round(a[0], 1)), *sorted((a[1], c[1]))
                else:
                    key, lo, hi = ("y", round(a[1], 1)), *sorted((a[0], c[0]))
                ext.setdefault(key, []).append((lo, hi, (a[2] + c[2]) / 2, k))
            else:
                j = base + nv - 1
                par[find(k)] = find(j)
                if portals is not None:
                    portals.setdefault(k, {})[j] = tuple((a + c) / 2)
    for (axis, at), edges in ext.items():
        edges.sort()
        for i, (lo, hi, z, k) in enumerate(edges):
            for lo2, hi2, z2, k2 in edges[i + 1:]:
                if lo2 > hi - 0.01:
                    break
                if abs(z - z2) < 2.0 and min(hi, hi2) - max(lo, lo2) > 0.05:
                    par[find(k)] = find(k2)
                    if portals is not None:
                        m = (max(lo, lo2) + min(hi, hi2)) / 2
                        mid = (at, m, (z + z2) / 2) if axis == "x" else (m, at, (z + z2) / 2)
                        portals.setdefault(k, {})[k2] = mid
                        portals.setdefault(k2, {})[k] = mid
    return np.array([find(k) for k in range(len(polys))])


def load(path: str) -> NavMesh:
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    nm = NavMesh(name=name, planet=name.split("_", 1)[0])
    magic, version, num_tiles, *_ = _SET_HDR.unpack_from(data, 0)
    if magic != MSET:
        raise ValueError(f"{name}: not a Core3 navmesh (magic {magic:#x})")
    off = _SET_HDR.size
    xs, ys = [], []
    links = []
    for _ in range(num_tiles):
        _ref, size = _TILE_HDR.unpack_from(data, off)
        off += _TILE_HDR.size
        tile = memoryview(data)[off:off + size]
        off += size
        h = _MESH_HDR.unpack_from(tile, 0)
        if h[0] != DNAV:
            raise ValueError(f"{name}: bad tile magic")
        poly_count, vert_count = h[6], h[7]
        nm.walkable_height, nm.walkable_radius, nm.walkable_climb = h[15], h[16], h[17]
        p = _MESH_HDR.size
        v = np.frombuffer(tile, np.float32, vert_count * 3, p).reshape(-1, 3)
        game = np.stack([v[:, 0], -v[:, 2], v[:, 1]], 1).astype(np.float64)
        p += vert_count * 12
        base = len(nm.polys)
        for i in range(poly_count):
            rec = _POLY.unpack_from(tile, p + i * _POLY.size)
            verts, neis = rec[1:7], rec[7:13]
            n, area_type = rec[14], rec[15]
            # keep every poly (even off-mesh/degenerate) so in-tile indices line up
            pts = game[list(verts[:max(n, 1)])]
            off_mesh = (area_type >> 6) == DT_POLYTYPE_OFFMESH or n < 3
            nm.polys.append(pts)
            links.append(((), base) if off_mesh else (neis[:n], base))
            if not off_mesh:
                xs += [pts[:, 0].min(), pts[:, 0].max()]
                ys += [pts[:, 1].min(), pts[:, 1].max()]
    if xs:
        nm.bmin, nm.bmax = (min(xs), min(ys)), (max(xs), max(ys))
    portals = {}
    comp = _components(nm.polys, links, portals)
    keep = [i for i, (neis, _b) in enumerate(links) if neis != ()]
    new = {old: n for n, old in enumerate(keep)}
    nm.polys = [nm.polys[i] for i in keep]
    nm.component = comp[keep]
    nm.adj = [{new[j]: mid for j, mid in portals.get(old, {}).items() if j in new} for old in keep]
    area = {}
    for p, c in zip(nm.polys, nm.component):
        area[c] = area.get(c, 0.0) + _area(p)
    nm.main = max(area, key=area.get) if area else -1
    return nm


@dataclass
class CellMap:
    """Walkable fraction per bot cell over a navmesh's bounds."""
    planet: str
    cell: float
    i0: int                 # cell index of column 0 (x)
    j0: int                 # cell index of row 0 (y/north)
    walk: np.ndarray        # (rows=y, cols=x) walkable fraction 0..1

    def fraction(self, wx: float, wz: float):
        i = int(math.floor(wx / self.cell)) - self.i0
        j = int(math.floor(wz / self.cell)) - self.j0
        if 0 <= j < self.walk.shape[0] and 0 <= i < self.walk.shape[1]:
            w = float(self.walk[j, i])
            return None if math.isnan(w) else w
        return None

    def cells(self, clear_min: float = 0.6, blocked_max: float = 0.1):
        """Yield ((cx, cz), state) with state 'clear' | 'blocked' for confident cells."""
        for j in range(self.walk.shape[0]):
            for i in range(self.walk.shape[1]):
                w = self.walk[j, i]
                if w >= clear_min:
                    yield (self.i0 + i, self.j0 + j), "clear"
                elif w <= blocked_max:
                    yield (self.i0 + i, self.j0 + j), "blocked"


def rasterise(nm: NavMesh, cell: float = 5.0, sub: int = 10, ground_only: bool = True) -> CellMap:
    """Fill polygons at cell/sub resolution, then average into cells.
    ground_only drops islands not connected to the main ground mesh (rooftops)."""
    import cv2

    polys = nm.ground_polys() if ground_only else nm.polys

    i0 = int(math.floor(nm.bmin[0] / cell)) - 1
    j0 = int(math.floor(nm.bmin[1] / cell)) - 1
    i1 = int(math.floor(nm.bmax[0] / cell)) + 2
    j1 = int(math.floor(nm.bmax[1] / cell)) + 2
    W, H = (i1 - i0) * sub, (j1 - j0) * sub
    fine = np.zeros((H, W), np.uint8)
    px = sub / cell
    for pts in polys:
        q = np.empty((len(pts), 2), np.int32)
        q[:, 0] = np.round((pts[:, 0] - i0 * cell) * px)
        q[:, 1] = np.round((pts[:, 1] - j0 * cell) * px)
        cv2.fillPoly(fine, [q], 1)
    walk = fine.reshape(j1 - j0, sub, i1 - i0, sub).mean(axis=(1, 3))
    # cells not wholly inside the navmesh's area are unknown (NaN), not blocked:
    # the mesh just stops there
    xs = (np.arange(i0, i1) * cell)
    ys = (np.arange(j0, j1) * cell)
    in_x = (xs >= nm.bmin[0] - 0.5) & (xs + cell <= nm.bmax[0] + 0.5)
    in_y = (ys >= nm.bmin[1] - 0.5) & (ys + cell <= nm.bmax[1] + 0.5)
    walk[~(in_y[:, None] & in_x[None, :])] = np.nan
    return CellMap(nm.planet, cell, i0, j0, walk)


def render(cm: CellMap, path: str, px: int = 4, marks=None):
    """Picture: north up. green = walkable, dark = blocked/no mesh."""
    import cv2

    raw = cm.walk[::-1].astype(np.float64)              # north at the top
    unknown = np.isnan(raw)
    w = np.nan_to_num(raw)
    img = np.zeros((*w.shape, 3), np.uint8)
    img[..., 1] = (40 + 180 * w).astype(np.uint8)
    img[..., 2] = (40 * (1 - w)).astype(np.uint8)
    img[..., 0] = 30
    img[unknown] = (90, 90, 90)
    img = cv2.resize(img, (w.shape[1] * px, w.shape[0] * px), interpolation=cv2.INTER_NEAREST)
    for (wx, wz), label in (marks or []):
        i = int(math.floor(wx / cm.cell)) - cm.i0
        j = cm.walk.shape[0] - 1 - (int(math.floor(wz / cm.cell)) - cm.j0)
        c = (i * px + px // 2, j * px + px // 2)
        cv2.circle(img, c, 6, (255, 255, 255), 2)
        cv2.putText(img, label, (c[0] + 8, c[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    # 100 m grid with labels
    step = int(100 / cm.cell)
    for i in range(0, cm.walk.shape[1], 1):
        if (cm.i0 + i) % step == 0:
            cv2.line(img, (i * px, 0), (i * px, img.shape[0]), (90, 90, 90), 1)
            cv2.putText(img, f"{(cm.i0 + i) * cm.cell:.0f}", (i * px + 2, 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
    for j in range(cm.walk.shape[0]):
        if (cm.j0 + j) % step == 0:
            y = (cm.walk.shape[0] - 1 - j) * px
            cv2.line(img, (0, y), (img.shape[1], y), (90, 90, 90), 1)
            cv2.putText(img, f"{(cm.j0 + j) * cm.cell:.0f}", (2, y - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
    cv2.imwrite(path, img)
    return img


class PriorIndex:
    """All built cell maps (data/navmesh_priors/*.npz), looked up by planet and position."""

    def __init__(self, folder: str = "data/navmesh_priors"):
        self.maps: dict[str, list[CellMap]] = {}
        if not os.path.isdir(folder):
            return
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".npz"):
                continue
            z = np.load(os.path.join(folder, name))
            cm = CellMap(str(z["planet"]), float(z["cell"]), int(z["i0"]), int(z["j0"]),
                         z["walk"].astype(np.float32))
            self.maps.setdefault(cm.planet, []).append(cm)

    def fraction(self, planet: str, wx: float, wz: float):
        """Walkable fraction of the bot cell at (wx, wz), or None where no navmesh covers it."""
        for cm in self.maps.get(planet, ()):
            f = cm.fraction(wx, wz)
            if f is not None:
                return f
        return None


# -- routing on the navmesh itself, levels included (17 Nov) ---------------------------------
#
# The cell maps above are flat: a terrace and the grass under its wall are the same cell, so
# the bot stood 5 m from a delivery NPC, 2 m below him, and every check said "arrived" (Marc,
# 17 Nov). The polygons keep their heights and their links, so a path over them goes round to
# the steps.

def _inside(pts, x, y) -> bool:
    n, ok = len(pts), False
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ok = not ok
    return ok


def _height_at(pts, x, y) -> float:
    return float(np.mean(pts[:, 2]))


def polys_at(nm: NavMesh, x: float, y: float, near_m: float = 0.0):
    """Indices of polys containing (x, y); failing that, those within near_m of it."""
    hit = [k for k, p in enumerate(nm.polys)
           if p[:, 0].min() <= x <= p[:, 0].max() and p[:, 1].min() <= y <= p[:, 1].max() and _inside(p, x, y)]
    if hit or near_m <= 0:
        return hit
    out = []
    for k, p in enumerate(nm.polys):
        c = p[:, :2].mean(axis=0)
        if math.hypot(c[0] - x, c[1] - y) <= near_m + float(np.max(np.hypot(p[:, 0] - c[0], p[:, 1] - c[1]))):
            out.append(k)
    return out


def route(nm: NavMesh, start, goal, other_level: bool = False, climb: float | None = None):
    """
    start: (x, y, height) - where we stand, height from the HUD. goal: (x, y), height unknown
    (a /waypoint on a target reads height 0). other_level=True: of the surfaces at the goal,
    take the one furthest in height from ours (we're at its foot and it isn't reachable from
    here). Returns ([(x, y), ...], info) or (None, info).
    """
    import heapq
    climb = float(climb if climb is not None else max(nm.walkable_climb, 1.0))
    sx, sy, sh = start
    gx, gy = goal
    cand = polys_at(nm, sx, sy, near_m=3.0)
    if sh is not None:
        cand = sorted(cand, key=lambda k: abs(_height_at(nm.polys[k], sx, sy) - sh))
    if not cand:
        return None, {"why": "not on the navmesh here"}
    s = cand[0]
    goals = polys_at(nm, gx, gy, near_m=2.0)
    if not goals:
        return None, {"why": "no surface at the goal"}
    h0 = _height_at(nm.polys[s], sx, sy)
    goals = sorted(goals, key=lambda k: abs(_height_at(nm.polys[k], gx, gy) - h0), reverse=other_level)
    info = {"start_poly": s, "start_h": round(h0, 1)}
    for g in goals:
        gh = _height_at(nm.polys[g], gx, gy)
        if other_level and abs(gh - h0) <= climb:
            continue                             # same level as us: that's not the way
        cen = [p[:, :2].mean(axis=0) for p in nm.polys]
        dist = {s: 0.0}
        prev = {}
        heap = [(0.0, 0.0, s)]
        while heap:
            _f, d, k = heapq.heappop(heap)
            if k == g:
                break
            if d > dist.get(k, math.inf):
                continue
            for j, mid in (nm.adj[k] if k < len(nm.adj) else {}).items():
                nd = d + math.hypot(cen[k][0] - mid[0], cen[k][1] - mid[1]) + math.hypot(cen[j][0] - mid[0], cen[j][1] - mid[1])
                if nd < dist.get(j, math.inf):
                    dist[j], prev[j] = nd, (k, mid)
                    heapq.heappush(heap, (nd + math.hypot(cen[j][0] - gx, cen[j][1] - gy), nd, j))
        if g not in dist:
            continue
        pts, k = [(gx, gy)], g
        while k != s:
            k, mid = prev[k]
            pts.append((float(mid[0]), float(mid[1])))
        pts.append((sx, sy))
        pts.reverse()
        info.update(goal_poly=g, goal_h=round(gh, 1), length=round(sum(
            math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])), 1), polys=len(prev))
        return pts, info
    info["why"] = "no linked way to a surface at the goal" + (" on another level" if other_level else "")
    return None, info


class MeshIndex:
    """Raw navmesh files for one planet, loaded when first needed, by the area they cover."""

    def __init__(self, folder: str, planet: str):
        self.folder, self.planet, self._meshes = folder, planet, None

    def _all(self):
        if self._meshes is None:
            self._meshes = []
            if self.folder and os.path.isdir(self.folder):
                for name in sorted(os.listdir(self.folder)):
                    if not name.startswith(self.planet + "_") or name.endswith((".obj", ".png", ".npz")):
                        continue
                    try:
                        self._meshes.append(load(os.path.join(self.folder, name)))
                    except Exception as e:                      # one bad file shouldn't stop the rest
                        print(f"[navmesh] {name}: {e}")
        return self._meshes

    def at(self, x: float, y: float):
        for nm in self._all():
            if nm.bmin[0] - 5 <= x <= nm.bmax[0] + 5 and nm.bmin[1] - 5 <= y <= nm.bmax[1] + 5:
                return nm
        return None
