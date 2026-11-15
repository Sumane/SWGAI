"""
Problem areas ("stuck in a box"): list them, see their own-layer map, decide.

    python scripts/review_areas.py                    # list all
    python scripts/review_areas.py --show 3           # map picture of area 3: main map vs its layer
    python scripts/review_areas.py --set 3 STATUS     # pending_review | multi_level | no_go | merged | dismissed
    python scripts/review_areas.py --merge 3          # copy the area's layer cells into the main map, status merged

Pictures go to data/areas/area_<id>.png (inside the repo).
"""
import argparse

import _bootstrap  # noqa: F401
import numpy as np

from jedai.config import settings
from jedai.navigation.grid_db import BLOCKED, CLEAR, NavDatabase

STATUSES = ("pending_review", "multi_level", "no_go", "merged", "dismissed")


def cells(db, planet, a, res=5):
    r = a["radius"]
    return db.get_cells_in_region(planet, a["cx"] - r, a["cx"] + r, a["cz"] - r, a["cz"] + r, res)


def show(db, a, out):
    import cv2
    r, res, px = a["radius"], 5, 6
    n = int(2 * r / res) + 1
    panels = []
    for planet, title in ((a["planet"], "main map"), (f"{a['planet']}@area{a['id']}", "area layer")):
        img = np.full((n * px, n * px, 3), 60, np.uint8)
        for c in cells(db, planet, a):
            i = int((c["cx"] * res - (a["cx"] - r)) / res)
            j = int((a["cz"] + r - c["cz"] * res) / res)
            if 0 <= i < n and 0 <= j < n:
                col = (60, 170, 60) if c["state"] == CLEAR else (40, 40, 200) if c["state"] == BLOCKED else (60, 60, 60)
                img[j * px:(j + 1) * px, i * px:(i + 1) * px] = col
        cv2.circle(img, (n * px // 2, n * px // 2), 6, (255, 255, 255), 2)
        cv2.putText(img, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        panels.append(img)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), np.hstack([panels[0], np.full((n * px, 8, 3), 0, np.uint8), panels[1]]))
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int)
    ap.add_argument("--set", nargs=2, metavar=("ID", "STATUS"))
    ap.add_argument("--merge", type=int)
    a = ap.parse_args()
    db = NavDatabase(str(settings.path("nav_db")), resolution=int(settings.planner.get("resolution_m", 5)))
    by_id = {x["id"]: x for x in db.areas()}
    if a.set:
        aid, st = int(a.set[0]), a.set[1]
        if st not in STATUSES:
            raise SystemExit(f"status must be one of {STATUSES}")
        db.set_area_status(aid, st)
        print(f"area {aid}: {st}")
    elif a.merge:
        ar = by_id[a.merge]
        n = 0
        for c in cells(db, f"{ar['planet']}@area{ar['id']}", ar):
            wx, wz = c["cx"] * 5 + 2.5, c["cz"] * 5 + 2.5
            (db.mark_blocked if c["state"] == BLOCKED else db.mark_clear)(ar["planet"], wx, wz, 5)
            n += 1
        db.set_area_status(ar["id"], "merged")
        print(f"area {ar['id']}: {n} cells copied into the main map")
    elif a.show:
        from pathlib import Path
        show(db, by_id[a.show], Path(__file__).resolve().parents[1] / "data" / "areas" / f"area_{a.show}.png")
    else:
        if not by_id:
            print("no problem areas")
        for x in by_id.values():
            print(f"#{x['id']:3d} {x['planet']:9s} ({x['cx']:7.0f},{x['cz']:7.0f}) h={x['cy']}  r={x['radius']:.0f} m  "
                  f"visits {x['visits']}  {x['status']}")
            for line in x["notes"].strip().splitlines():
                print(f"       {line}")


if __name__ == "__main__":
    main()
