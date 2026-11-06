"""
Harness v0: collect exploration badges on the current planet, unattended.

    python scripts/run_harness.py                 # until done, kill key, or 3 failures in a row
    python scripts/run_harness.py --max-steps 2   # short test
    python scripts/run_harness.py --skip badge_x  # leave out a badge
    python scripts/run_harness.py --list          # show badges + what's earned, touch nothing

Logs: data/harness_logs/<time>/skills.jsonl. State: data/harness/world_state.db.
Each navigation run is still recorded in data/debug_logs as before.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-steps", type=int)
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    from jedai.config import settings
    from jedai.data import load_world_points
    from jedai.harness.state import WorldState
    from jedai.harness.system2 import BadgeCollector

    wp = load_world_points()
    state = WorldState(settings.data_path("harness", "world_state.db"))
    if a.list:
        pos = state.position()
        for b in wp.get(settings.planet, {}).get("badges", []):
            d = f"{math.hypot(b['x'] - pos[1], b['z'] - pos[2]):6.0f} m" if pos else ""
            mark = "x" if state.has_badge(b["key"]) else " "
            print(f"[{mark}] {b['key']:40s} ({b['x']:7.0f}, {b['z']:7.0f}) {d}")
        return

    from jedai.control.keys import kill_pressed
    from jedai.harness.log import SkillLog
    from jedai.harness.skills import default_registry
    from jedai.harness.supervisor import Supervisor
    from jedai.navigation.runner import NavigationRunner

    runner = NavigationRunner(settings)
    runner.apply_vehicle(settings.nav.get("default_vehicle", "swoop"))
    log = SkillLog(settings.data_path("harness_logs"))
    sup = Supervisor(default_registry(runner, state, settings), state, log,
                     BadgeCollector(wp, skip_keys=set(a.skip)),
                     planet=settings.planet, read_position=runner.read_position,
                     stop_check=lambda: kill_pressed(settings.kill_key, settings),
                     max_steps=a.max_steps, max_minutes=a.minutes)
    try:
        reason = sup.run()
    finally:
        runner.shutdown()
        log.close()
    print(f"\nStopped: {reason}  ({sup.steps} steps)  log: {log.session_dir}")


if __name__ == "__main__":
    main()
