"""
Clone and walk out of the cloning facility (the recover_from_death skill).

    python scripts/recover_from_death.py --dry-run     # character dead, clone window up: say what it would pick, no clicks
    python scripts/recover_from_death.py               # character dead: clone, close the decay report, walk out
    python scripts/recover_from_death.py --route-only  # character alive, standing in the clone tube: just walk out

Needs the key controller running (python scripts/run_controller.py in another
terminal, or harness_live.py paused with data/live/PAUSE).
Each step's screenshot + OCR: data/live/recover/. Problems: data/live/anomalies/.
Routes: jedai/data/clone_routes.json (Theed so far).
"""
import argparse
import time

import _bootstrap  # noqa: F401

from jedai.config import settings
from jedai.harness.skills.recover import RecoverFromDeath
from jedai.harness.state import WorldState
from jedai.navigation.runner import NavigationRunner

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--route-only", action="store_true")
    a = ap.parse_args()
    runner = NavigationRunner(settings)
    print("Switch to the game: starting in 5 s")
    time.sleep(5)
    try:
        print(RecoverFromDeath(runner, settings).run(WorldState(":memory:"), dry_run=a.dry_run,
                                                     route_only=a.route_only))
    finally:
        runner.keys.release_all()
