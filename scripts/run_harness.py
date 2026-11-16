"""
Harness v0: collect exploration badges on the current planet, unattended.

    python scripts/run_harness.py                 # until done, kill key, or 3 failures in a row
    python scripts/run_harness.py --max-steps 2   # short test
    python scripts/run_harness.py --skip badge_x  # leave out a badge
    python scripts/run_harness.py --list          # show badges + what's earned, touch nothing

Logs: data/harness/logs/<time>/skills.jsonl. State: data/harness/world_state.db
(paths.harness_dir, inside the repo so the live reviewer can read it).
Each navigation run is still recorded in data/debug_logs as before.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402,F401  (sets the GPU for vision)


def _sigterm_as_interrupt():
    """harness_live.py stops us with SIGTERM: turn it into KeyboardInterrupt so
    the finally blocks run and no movement key is left held down."""
    import signal

    def handler(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, handler)


def main():
    _sigterm_as_interrupt()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-steps", type=int)
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--skip", nargs="*", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--clear-failures", action="store_true", help="retry badges that failed before")
    ap.add_argument("--earned", nargs="+", metavar="KEY", help="record badges the character already has, then exit")
    a = ap.parse_args()

    from jedai.config import settings
    from jedai.data import load_world_points
    from jedai.harness.state import WorldState
    from jedai.harness.system2 import BadgeCollector

    wp = load_world_points()
    hdir = settings.path("harness_dir")
    state = WorldState(hdir / "world_state.db")
    # new code since the last run: give badges that failed before another go
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    code = max(p.stat().st_mtime for d in ("jedai", "scripts", "config")
               for p in (root / d).rglob("*") if p.suffix in (".py", ".yaml"))
    if a.clear_failures or state.get("code_version") != code:
        state.clear_failures()
        state.set("code_version", code)
    if a.earned:
        # the game says nothing when you enter a badge you already own, so the
        # chat check can't confirm those: record them by hand (Marc, 7 Nov)
        known = {b["key"] for p in wp.values() for b in p["badges"]}
        for key in a.earned:
            if key not in known:
                raise SystemExit(f"unknown badge {key}; see --list")
            state.add_badge(key)
            print(f"recorded {key} as earned")
        return
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

    h = settings.raw["harness"]
    runner = NavigationRunner(settings)
    runner.open_chatlog()
    # the navigator's vehicle profile must match the character: on foot unless we know better
    mounted = state.get("mounted", bool(h.get("start_mounted", False)))
    if h.get("force_mounted") is not None:
        mounted = bool(h["force_mounted"])
    # one-shot: forget badges counted before chat confirmation existed
    reset = root / "data" / "live" / "RESET_BADGES"
    if reset.exists():
        state.clear_badges()
        reset.unlink()
        print("[harness] badge list cleared (RESET_BADGES)")
    # one-shot correction from a reviewer: data/live/SET_MOUNTED containing true/false
    flag = root / "data" / "live" / "SET_MOUNTED"
    if flag.exists():
        mounted = flag.read_text().strip().lower() in ("1", "true", "yes")
        flag.unlink()
        print(f"[harness] mount state set from SET_MOUNTED: {mounted}")
    runner.apply_vehicle(settings.nav.get("default_vehicle", "swoop") if mounted
                         else settings.nav.get("foot_vehicle", "running"))
    state.set("mounted", mounted)
    log = SkillLog(hdir / "logs")
    registry = default_registry(runner, state, settings)
    recover = bool(h.get("recover_from_death", True))

    def housekeeping():
        # dead? (the CLONE ACTIVATED window is up): don't type into chat, recover first
        if recover and not state.get("dead", False) and registry.get("recover_from_death").is_dead():
            print("[harness] the clone window is on screen: the character is dead")
            state.set("dead", True)
        if not state.get("dead", False):
            runner.ensure_chatlog()

    sup = Supervisor(registry, state, log,
                     BadgeCollector(wp, skip_keys=set(a.skip), can_mount=bool(h.get("mount_key")),
                                    max_failures=int(h.get("max_badge_failures", 2)),
                                    clean_every=int(h.get("clean_waypoints_every", 1))),
                     planet=settings.planet, read_position=runner.read_position,
                     stop_check=lambda: kill_pressed(settings.kill_key, settings),
                     max_steps=a.max_steps, max_minutes=a.minutes,
                     max_consecutive_failures=h.get("max_consecutive_failures", 3),
                     stop_on_exception=bool(h.get("stop_on_exception", True)),
                     between_steps=housekeeping, recover_from_death=recover)
    try:
        reason = sup.run()
    finally:
        runner.shutdown()
        log.close()
    print(f"\nStopped: {reason}  ({sup.steps} steps)  log: {log.session_dir}")
    if reason == "dead":
        (root / "data" / "live").mkdir(parents=True, exist_ok=True)
        (root / "data" / "live" / "PAUSE").write_text(f"character died, see {log.session_dir}\n")
        print("Paused harness_live (data/live/PAUSE): the character died. Clone, then remove PAUSE.")
    if reason == "skill_exception":
        # don't let harness_live restart into the same bug: pause until someone looks
        (root / "data" / "live").mkdir(parents=True, exist_ok=True)
        (root / "data" / "live" / "PAUSE").write_text(f"skill exception, see {log.session_dir}\n")
        print("Paused harness_live (data/live/PAUSE): a skill crashed.")


if __name__ == "__main__":
    main()
