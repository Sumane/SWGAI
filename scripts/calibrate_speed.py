"""
Calibrate the max speed of a vehicle / movement mode.

    python scripts/calibrate_speed.py --mode swoop --save

Press SPACE to start recording, travel in a straight line for 5+ seconds on
open ground, press SPACE again to stop. With --save the result is written to
config/vehicles.json (paths.vehicles), which is what the navigator reads.
Windows console only (uses msvcrt for the space bar).
"""
import _bootstrap  # noqa: F401

import argparse
import json
import math
import sys
import time
from collections import deque

from jedai.config import settings
from jedai.perception.hud import CoordinateReader
from jedai.perception.screen import Screen

msvcrt = None
if sys.platform == "win32":
    import msvcrt

SAMPLE_WINDOW = 30   # readings in the rolling window
MIN_SAMPLES = 20


def space_pressed():
    return bool(msvcrt and msvcrt.kbhit() and msvcrt.getch() == b" ")


def calibrate(coords: CoordinateReader, screen: Screen, mode: str):
    print(f"\n{'=' * 50}\n  SPEEDOMETER — Calibrating: {mode.upper()}\n{'=' * 50}")
    print("  1. Get on your vehicle / start walking\n  2. Face open terrain"
          "\n  3. SPACE to start, run straight 5+ seconds, SPACE to stop\n")

    positions, stamps, speeds = deque(maxlen=SAMPLE_WINDOW), deque(maxlen=SAMPLE_WINDOW), []
    recording = False

    def current_speed():
        if len(positions) < 2 or stamps[-1] - stamps[0] < 0.1:
            return 0.0
        (x1, z1), (x2, z2) = positions[0], positions[-1]
        return math.hypot(x2 - x1, z2 - z1) / (stamps[-1] - stamps[0])

    while True:
        x, _, z = coords.read(screen.grab())
        speed = current_speed()
        where = f"({x:.1f}, {z:.1f})" if x is not None and z is not None else "(no coords)"
        print(f"\r  [{'RECORDING...' if recording else 'WAITING FOR SPACE...'}] "
              f"Coords: {where} | Speed: {speed:5.1f} m/s | Samples: {len(positions)}",
              end="", flush=True)
        if space_pressed():
            if recording:
                print("\n\n  >>> RECORDING STOPPED <<<\n")
                break
            recording = True
            positions.clear(); stamps.clear(); speeds.clear()
            print("\n\n  >>> RECORDING STARTED <<<\n")
        if recording and x is not None and z is not None:
            positions.append((x, z))   # ground plane is X/Z, Y is height
            stamps.append(time.time())
            speeds.append(speed)
        time.sleep(0.1)

    valid = sorted(s for s in speeds if s > 0.1)
    if len(speeds) < MIN_SAMPLES or not valid:
        print(f"  ⚠️  Not enough valid samples ({len(valid)}); need {MIN_SAMPLES}")
        return None
    result = {"max": max(valid), "avg": sum(valid) / len(valid),
              "p95": valid[int(len(valid) * 0.95)], "samples": len(valid)}
    print(f"  Results for {mode}: max {result['max']:.2f} | avg {result['avg']:.2f} | "
          f"p95 {result['p95']:.2f} m/s ({result['samples']} samples)")
    return result


def main():
    parser = argparse.ArgumentParser(description="Calibrate SWGEmu movement speeds")
    # FIX: was 'speeder', which the navigator never looked up ('speederbike')
    parser.add_argument("--mode", default="walking",
                        help="walking, running, speederbike, swoop, or any new name")
    parser.add_argument("--save", action="store_true", help="write result to the vehicles file")
    args = parser.parse_args()

    result = calibrate(CoordinateReader.from_settings(settings),
                       Screen.from_settings(settings), args.mode)
    if result and args.save:
        path = settings.path("vehicles")
        try:
            with open(path) as f:
                table = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            table = {}
        table[args.mode] = {k: round(v, 2) if isinstance(v, float) else v for k, v in result.items()}
        with open(path, "w") as f:
            json.dump(table, f, indent=2)
        print(f"\n  ✓ Saved to {path}")


if __name__ == "__main__":
    main()
