"""
Do one goal from wherever the character is, chaining the skills we have.
Every run starts with /find clear + clean_waypoints. Death is only checked when
a step fails; if we're dead then, the clone is taken and the run stops.

    python scripts/go.py travel "Keren Starport"                    # from the nearest port we know
    python scripts/go.py travel "Keren Starport" --from "Theed Shuttle B"
    python scripts/go.py goto -4991 4072                            # just get somewhere (outdoors)
    python scripts/go.py recover                                    # clone and walk out, if dead
    python scripts/go.py find mission --dry-run                     # /tar mission and report what's there and how far
    python scripts/go.py find bank                                  # walk up to the closest bank terminal
    python scripts/go.py mission --dry-run                          # go to a mission terminal, read the list, take nothing
    python scripts/go.py mission                                    # ...and take a delivery mission

Starts the key controller itself if it isn't running. F8 stops it. You can
take over at any point; rerun the command and it carries on from where the
character is. Logs: data/harness/logs/<time>/skills.jsonl, navigation runs in
data/debug_logs/, step screenshots in data/live/{recover,travel}/.
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402,F401


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="goal", required=True)
    t = sub.add_parser("travel"); t.add_argument("to"); t.add_argument("--from", dest="start")
    g = sub.add_parser("goto"); g.add_argument("x", type=float); g.add_argument("z", type=float)
    g.add_argument("--stop", type=float, default=5.0)
    sub.add_parser("recover")
    f = sub.add_parser("find"); f.add_argument("what"); f.add_argument("--dry-run", action="store_true")
    m = sub.add_parser("mission"); m.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    from pathlib import Path
    from jedai.config import settings
    from jedai.harness.chain import Chain
    from jedai.harness.log import SkillLog
    from jedai.harness.skills import default_registry
    from jedai.harness.state import WorldState
    from jedai.navigation.runner import NavigationRunner

    root = Path(__file__).resolve().parents[1]
    controller = None
    if subprocess.run(["pgrep", "-f", "run_controller.py"], capture_output=True).returncode != 0:
        print("[go] starting the key controller")
        controller = subprocess.Popen([sys.executable, str(root / "scripts" / "run_controller.py")],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    hdir = settings.path("harness_dir")
    state = WorldState(hdir / "world_state.db")
    state.set("planet", settings.planet)
    runner = NavigationRunner(settings)
    mounted = bool(state.get("mounted", False))
    runner.apply_vehicle(settings.nav.get("default_vehicle", "swoop") if mounted
                         else settings.nav.get("foot_vehicle", "running"))
    log = SkillLog(hdir / "logs")
    chain = Chain(default_registry(runner, state, settings), state, log, settings, runner.read_position)
    print("Switch to the game: starting in 5 s")
    time.sleep(5)
    try:
        if a.goal != "recover":
            chain.start()
        if a.goal == "travel":
            res = chain.travel(a.to, start=a.start)
        elif a.goal == "find":
            res = chain.find(a.what, dry_run=a.dry_run)
        elif a.goal == "mission":
            res = chain.mission(dry_run=a.dry_run)
        elif a.goal == "goto":
            res = chain.go_to(a.x, a.z, "route_goto", stop=a.stop)
        else:
            res = chain.recover_if_dead() or "not dead: nothing to do"
        print(f"\n[go] {res}")
        for i, (skill, r) in enumerate(chain.steps, 1):
            print(f"   {i}. {skill}: {r.status} {r.reason}")
    finally:
        runner.shutdown()
        log.close()
        if controller is not None:
            controller.terminate()


if __name__ == "__main__":
    main()
