"""
Take a shuttle to another travel point on this planet (the travel_to skill).

    python scripts/travel.py --list                          # travel points on this planet
    python scripts/travel.py --to "The Lake Retreat" --dry-run   # where am I, what would it type
    python scripts/travel.py --to "The Lake Retreat"         # buy the ticket and board

Stand at a shuttleport or starport (within ~150 m of the travel point), on
foot or mounted. Needs the key controller running (scripts/run_controller.py).
Steps + screenshots: data/live/travel/. Problems: data/live/anomalies/.
"""
import argparse
import time

import _bootstrap  # noqa: F401

from jedai.config import settings
from jedai.data import load_travel_ports, load_world_points
from jedai.harness.skills.travel import TravelTo
from jedai.harness.state import WorldState

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--to")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.list or not a.to:
        ports = load_travel_ports().get(settings.planet, {})
        for p in load_world_points().get(settings.planet, {}).get("travel", []):
            print(f"{p['name']:28s} {p['kind']:12s} ({p['x']:7.0f}, {p['z']:7.0f})  "
                  f"{'recorded' if p['name'] in ports else ''}")
        raise SystemExit
    from jedai.navigation.runner import NavigationRunner
    runner = NavigationRunner(settings)
    print("Switch to the game: starting in 5 s")
    time.sleep(5)
    try:
        print(TravelTo(runner, settings).run(WorldState(":memory:"), to=a.to, dry_run=a.dry_run))
    finally:
        runner.keys.release_all()
