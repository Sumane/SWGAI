"""
Look at a recorded run.

    python scripts/review_run.py            # the latest run
    python scripts/review_run.py <folder>   # a particular one
    python scripts/review_run.py --list     # all runs

Rebuilds report.html / path.png / distance.png in the run folder, prints what
went wrong (where the distance went up, and what the bot was doing), and the
last events. To share a run: zip its folder.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jedai.config import load_settings  # noqa: E402
from jedai.navigation.recorder import load_run, write_report  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?", help="run folder (default: the latest)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--events", type=int, default=25, help="how many of the last events to print")
    a = ap.parse_args()

    base = load_settings().path("debug_dir")
    runs = sorted(p for p in base.glob("*") if (p / "ticks.jsonl").exists()) if base.exists() else []
    if a.list:
        for r in runs:
            summary = (r / "summary.txt").read_text().splitlines()[:1] if (r / "summary.txt").exists() else [""]
            print(f"{r.name}   {summary[0]}")
        return
    run = Path(a.run) if a.run else (runs[-1] if runs else None)
    if run is None or not run.exists():
        sys.exit(f"No recorded runs in {base}")

    blocked, clear = (), ()
    try:  # cells from the map, for the picture
        from jedai.navigation.grid_db import BLOCKED, CLEAR, NavDatabase
        s = load_settings()
        ticks, _ = load_run(run)
        pts = [t["pos"] for t in ticks if t.get("pos")]
        if pts:
            db = NavDatabase(str(s.path("nav_db")))
            xs, zs = [p[0] for p in pts], [p[1] for p in pts]
            cells = db.get_cells_in_region(s.planet, min(xs) - 100, max(xs) + 100, min(zs) - 100, max(zs) + 100)
            blocked = [(c["cx"] * 5, c["cz"] * 5) for c in cells if c["state"] == BLOCKED]
            clear = [(c["cx"] * 5, c["cz"] * 5) for c in cells if c["state"] == CLEAR]
    except Exception:
        pass

    findings = write_report(run, blocked, clear)
    print(f"Run: {run}\n")
    print("\n".join(findings))
    _, events = load_run(run)
    print(f"\nLast {min(a.events, len(events))} events:")
    for ev in events[-a.events:]:
        detail = ", ".join(f"{k}={v}" for k, v in ev.items() if k not in ("t", "i", "kind", "shot"))
        print(f"  {ev['t']:7.1f}s  {ev['kind']:<16} {detail}")
    print(f"\nOpen {run / 'report.html'} in a browser for the map and screenshots.")


if __name__ == "__main__":
    main()
