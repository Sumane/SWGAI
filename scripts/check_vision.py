"""
First in-game check: can JedAI see the game? Sends no keys.

    python scripts/check_vision.py

Grabs one frame from the configured display, saves it to
<data_dir>/check_vision.png, then prints your coordinates and every waypoint
it can read. Stand somewhere with the waypoint window open and a couple of
waypoints listed.
"""
import _bootstrap  # noqa: F401

import time

import cv2

from jedai.config import settings
from jedai.perception.hud import HudReader
from jedai.perception.screen import Screen
from jedai.platform_io import capture_display


def main():
    screen = Screen.from_settings(settings)
    print(f"Capturing display {capture_display(settings) or '(default)'} monitor {settings.monitor_index}")
    frame = screen.grab()
    out = settings.data_path("check_vision.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    print(f"Frame {frame.shape[1]}x{frame.shape[0]} saved to {out}  <- open it: is it the game?")

    print("Loading models...")
    hud = HudReader.from_settings(settings)

    t = time.time()
    x, y, z = hud.read_coords(frame)
    print(f"\nCoordinates: x={x} y={y} z={z}   ({(time.time() - t) * 1000:.0f} ms)")
    if x is None:
        print("  ✗ No coordinates. Is the minimap visible with coordinates shown?")

    t = time.time()
    wps = list(hud.waypoints(frame))
    print(f"Waypoints: {len(wps)}   ({(time.time() - t) * 1000:.0f} ms)")
    for wp in wps:
        print(f"  '{wp.name}'  {wp.distance}m  arrow={wp.arrow}")
    if not wps:
        print("  ✗ No waypoints. Is the waypoint window open with waypoints in it?")


if __name__ == "__main__":
    main()
