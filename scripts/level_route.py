"""
Check the navmesh offline: is there a way from where you stand to a spot on another level?

    python scripts/level_route.py -5622 6 4715 -5620 4710            # x height z  goal_x goal_z
    python scripts/level_route.py -5622 6 4715 -5620 4710 --same     # any level at the goal

Height is the middle number under the minimap. Uses paths.navmesh_dir from the settings
(the raw Core3 dumps) unless --dir is given. Prints the surfaces found at both ends and the
route the bot would walk (17 Nov: the delivery NPC on the terrace above the bot).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jedai.config import load_settings  # noqa: E402
from jedai.navigation import navmesh as N  # noqa: E402
from jedai.navigation.route import simplify  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("x", type=float)
    ap.add_argument("height", type=float)
    ap.add_argument("z", type=float)
    ap.add_argument("gx", type=float)
    ap.add_argument("gz", type=float)
    ap.add_argument("--dir", default=None)
    ap.add_argument("--planet", default=None)
    ap.add_argument("--same", action="store_true", help="don't insist on another level at the goal")
    a = ap.parse_args()
    s = load_settings()
    folder = a.dir or s.raw.get("paths", {}).get("navmesh_dir", "")
    planet = a.planet or s.planet
    idx = N.MeshIndex(folder, planet)
    nm = idx.at(a.x, a.z)
    if nm is None:
        print(f"no {planet} navmesh in {folder!r} covers ({a.x:.0f}, {a.z:.0f})")
        return 1
    print(f"mesh: {nm.name}  ({len(nm.polys)} polys, climb {nm.walkable_climb:.2f} m)")
    for label, (x, z) in (("you", (a.x, a.z)), ("goal", (a.gx, a.gz))):
        ks = N.polys_at(nm, x, z, near_m=2.0)
        hs = sorted({round(N._height_at(nm.polys[k], x, z), 1) for k in ks})
        print(f"surfaces at {label} ({x:.0f}, {z:.0f}): heights {hs or 'none'}")
    pts, info = N.route(nm, (a.x, a.z, a.height), (a.gx, a.gz), other_level=not a.same)
    if pts is None:
        print(f"no route: {info.get('why')}  {info}")
        return 2
    print(f"route: {info['length']} m over {info['polys']} polys, from height {info['start_h']} to {info['goal_h']}")
    for x, z in simplify(pts, min_gap_m=4.0):
        print(f"   ({x:8.1f}, {z:8.1f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
