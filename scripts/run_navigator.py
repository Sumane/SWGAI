"""
Drive to a waypoint. Start run_controller.py first.

    python scripts/run_navigator.py
    python scripts/run_navigator.py --fresh-map   # set the grid map aside first

It asks for a vehicle, scans your waypoints, and asks which one to go to.
Switch focus back to the game during the countdown. F8 stops it.

--fresh-map renames the current map (paths.nav_db) to <name>.<date_time>.db
and starts an empty one. Use it after a run that marked walls in the wrong
places; nothing is deleted.
"""
import _bootstrap  # noqa: F401

import argparse
import time

from jedai.config import settings
from jedai.navigation.runner import NavigationRunner

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh-map", action="store_true", help="set the grid map aside and start empty")
    a = ap.parse_args()
    if a.fresh_map:
        db = settings.path("nav_db")
        if db.exists():
            old = db.with_name(f"{db.stem}.{time.strftime('%Y-%m-%d_%H%M%S')}{db.suffix}")
            db.rename(old)
            print(f"Map set aside: {old.name}")
    NavigationRunner(settings).navigate()
