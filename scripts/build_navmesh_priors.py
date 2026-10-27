"""
Turn Core3 navmesh dumps into the bot's 5 m cell maps, for every planet.

    python scripts/build_navmesh_priors.py "/run/media/marc/New Volume/SWGEMU_bot/navmeshes"

Writes data/navmesh_priors/<area>.npz (walkable fraction per cell) and
<area>.png, plus summary.txt. Rooftops and other islands not connected to
the main ground mesh are dropped. Only cities and points of interest have
navmeshes: open terrain between them stays unknown to the prior.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jedai.navigation import navmesh as N  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="folder with the navmesh dump files")
    ap.add_argument("--out", default="data/navmesh_priors")
    ap.add_argument("--cell", type=float, default=5.0)
    ap.add_argument("--px", type=int, default=3, help="pixels per cell in the pictures")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = []
    for name in sorted(os.listdir(a.src)):
        path = os.path.join(a.src, name)
        if name.startswith(".") or name.endswith(".obj") or not os.path.isfile(path):
            continue
        t = time.time()
        try:
            nm = N.load(path)
        except Exception as e:  # noqa: BLE001
            print(f"skip {name}: {e}")
            continue
        if not nm.polys:
            print(f"skip {name}: empty")
            continue
        cm = N.rasterise(nm, cell=a.cell)
        np.savez_compressed(os.path.join(a.out, name + ".npz"), walk=cm.walk.astype(np.float16),
                            i0=cm.i0, j0=cm.j0, cell=cm.cell, planet=cm.planet)
        N.render(cm, os.path.join(a.out, name + ".png"), px=a.px)
        ground = len(nm.ground_polys())
        clear = int((cm.walk >= 0.6).sum())
        blocked = int((cm.walk <= 0.1).sum())
        cx = (nm.bmin[0] + nm.bmax[0]) / 2
        cy = (nm.bmin[1] + nm.bmax[1]) / 2
        rows.append(f"{name:50s} centre ({cx:7.0f},{cy:7.0f})  size {nm.bmax[0]-nm.bmin[0]:5.0f}x{nm.bmax[1]-nm.bmin[1]:5.0f} m"
                    f"  polys {len(nm.polys):6d} ground {ground/len(nm.polys)*100:5.1f}%"
                    f"  cells clear {clear:6d} blocked {blocked:5d}")
        print(rows[-1], f"({time.time()-t:.1f}s)")
    with open(os.path.join(a.out, "summary.txt"), "w") as f:
        f.write("\n".join(rows) + "\n")
    print(f"\n{len(rows)} areas -> {a.out}")


if __name__ == "__main__":
    main()
