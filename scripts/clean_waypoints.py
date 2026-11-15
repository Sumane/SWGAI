"""
Delete the bot's own waypoints from the datapad (names starting with
harness.bot_waypoint_prefixes: badge_, route_, bot_).

    python scripts/clean_waypoints.py --dry-run     # list what it would delete + picture, no clicks
    python scripts/clean_waypoints.py               # delete them
    python scripts/clean_waypoints.py --keep badge_amidalas_sandy_beach

Needs the key controller running (harness_live.py starts one; pause it with
data/live/PAUSE so no harness run moves the character). Picture of the dry run:
data/live/datapad/dry_run.jpg
"""
import argparse
import time

import _bootstrap  # noqa: F401

from jedai.config import settings
from jedai.harness.skills.datapad import CleanWaypoints
from jedai.harness.state import WorldState
from jedai.navigation.runner import NavigationRunner

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep", nargs="*", default=[])
    ap.add_argument("--max", type=int, default=30)
    a = ap.parse_args()
    runner = NavigationRunner(settings)
    print("Switch to the game: starting in 5 s")
    time.sleep(5)
    res = CleanWaypoints(runner, settings).run(WorldState(":memory:"), keep=a.keep, dry_run=a.dry_run,
                                               max_delete=a.max)
    print(res)
