"""
Run the harness unattended with a remote reviewer (Claude) in the loop.

    python scripts/harness_live.py [--steps-per-run 1] [--shot-every 10]

What it does, forever until you stop it:
  * starts the key controller (run_controller.py); if it exits (F8 kill key),
    everything stops
  * runs `run_harness.py --max-steps N` as a child process
  * every --shot-every seconds: data/live/screen.jpg (small overview) plus
    data/live/tiles/ (full-resolution 3x3 tiles + named regions such as chat.jpg,
    see harness.screen_regions) so small text stays readable
  * writes data/live/status.json: running?, last exit, run count, code version
  * restarts the harness when it exits, or straight away when any file under
    jedai/, scripts/ or config/ changes (Claude has pushed a fix)
  * pauses (no restarts) while data/live/PAUSE exists; quits when data/live/STOP exists

Stop it yourself: the kill key (F8) stops everything, so does Ctrl+C here. Child output goes to data/live/harness_out.log.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LIVE = ROOT / "data" / "live"
WATCH = ["jedai", "scripts", "config"]


def code_version() -> float:
    """Newest mtime of any watched source file."""
    newest = 0.0
    for d in WATCH:
        for p in (ROOT / d).rglob("*"):
            if p.suffix in (".py", ".yaml", ".json") and "__pycache__" not in p.parts:
                newest = max(newest, p.stat().st_mtime)
    return newest


def write_status(**kw) -> None:
    tmp = LIVE / "status.json.tmp"
    tmp.write_text(json.dumps({"t": time.time(), **kw}, indent=1))
    tmp.replace(LIVE / "status.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps-per-run", type=int, default=1)
    ap.add_argument("--shot-every", type=float, default=10.0)
    ap.add_argument("--pause-between", type=float, default=5.0)
    a = ap.parse_args()
    LIVE.mkdir(parents=True, exist_ok=True)

    import cv2
    from jedai.config import settings
    from jedai.perception.screen import Screen
    from jedai.perception.tiles import save_pieces
    screen = Screen.from_settings(settings)
    regions = {k: tuple(v) for k, v in settings.raw.get("harness", {}).get("screen_regions", {}).items()}
    me = Path(__file__).resolve()
    me_version = me.stat().st_mtime

    # vision on the GPU the local LLM isn't using (see harness.cuda_visible_devices)
    env = dict(os.environ)
    cuda = str(settings.raw.get("harness", {}).get("cuda_visible_devices", "") or "")
    if cuda:
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["CUDA_VISIBLE_DEVICES"] = cuda
        print(f"[live] vision on CUDA device {cuda} (PCI order)")

    ctl_out = open(LIVE / "controller_out.log", "a")
    controller = subprocess.Popen([sys.executable, "-u", str(ROOT / "scripts/run_controller.py")],
                                  cwd=ROOT, stdout=ctl_out, stderr=subprocess.STDOUT)
    print("[live] controller started")
    time.sleep(2.0)

    runs, last_exit, child, out = 0, None, None, None
    fresh_code = True          # first run, and first run after a code change: retry failures
    version = code_version()
    last_shot = 0.0
    print(f"[live] watching {', '.join(WATCH)}; files in {LIVE}")
    try:
        while not (LIVE / "STOP").exists():
            if controller.poll() is not None:
                print(f"[live] controller exited ({controller.returncode}): kill key or crash, stopping")
                last_exit = "controller exited"
                break
            now = time.time()
            if now - last_shot >= a.shot_every:
                last_shot = now
                try:
                    img = screen.grab()
                    h, w = img.shape[:2]
                    small = cv2.resize(img, (960, int(960 * h / w)))
                    cv2.imwrite(str(LIVE / "screen.tmp.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    os.replace(LIVE / "screen.tmp.jpg", LIVE / "screen.jpg")
                    save_pieces(img, LIVE / "tiles", regions=regions)
                except Exception as e:  # noqa: BLE001 - a missed screenshot is fine
                    print(f"[live] screenshot failed: {e}")

            if me.stat().st_mtime != me_version:
                # this script itself changed: stop the children and restart in place
                print("[live] harness_live.py changed: restarting myself")
                for p in (child, controller):
                    if p is not None and p.poll() is None:
                        p.terminate()
                        try:
                            p.wait(10)
                        except subprocess.TimeoutExpired:
                            p.kill()
                os.execv(sys.executable, [sys.executable] + sys.argv)
            new_version = code_version()
            if new_version != version:
                fresh_code = True
            if child is not None and new_version != version:
                print("[live] code changed: restarting harness")
                child.terminate()
                try:
                    child.wait(10)
                except subprocess.TimeoutExpired:
                    child.kill()
                last_exit = "restarted_for_new_code"
            version = new_version

            if child is not None and child.poll() is not None:
                last_exit = child.returncode if last_exit is None else last_exit
                child, out = None, out.close()
                time.sleep(a.pause_between)
            if child is None and not (LIVE / "PAUSE").exists():
                runs += 1
                last_exit = None
                out = open(LIVE / "harness_out.log", "a")
                out.write(f"\n===== run {runs} {time.ctime()} =====\n")
                out.flush()
                child = subprocess.Popen(
                    [sys.executable, "-u", str(ROOT / "scripts/run_harness.py"),
                     "--max-steps", str(a.steps_per_run)] + (["--clear-failures"] if fresh_code else []),
                    cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, env=env)
                print(f"[live] run {runs} started" + (" (failures cleared)" if fresh_code else ""))
                fresh_code = False
            write_status(running=child is not None, runs=runs, last_exit=last_exit,
                         paused=(LIVE / "PAUSE").exists(), code_version=version)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        for p in (child, controller):
            if p is not None and p.poll() is None:
                p.terminate()
        write_status(running=False, runs=runs, last_exit="live loop stopped", code_version=version)
        print("[live] stopped")


if __name__ == "__main__":
    main()
