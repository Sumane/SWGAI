"""
Check the depth -> blocked/clear cells pipeline in game, before it goes near D*.

Stand somewhere with obvious walls and open ground ahead, then (controller
running, game focused within the countdown):

    python scripts/depth_check.py                 # first person, hide HUD (Shift+Ctrl+H), grab, HUD back
    python scripts/depth_check.py --repeat 10     # a shot every 2 s while you walk about
    python scripts/depth_check.py --image shot.png --no-keys   # an existing screenshot

For each shot it saves data/depth_checks/<time>.png:  frame | depth | bird's-eye
cells (green clear, red blocked, grey unknown; you are the dot at the bottom,
each square 5 m), and prints the grid. If walls show up at the wrong distance,
adjust --fov (field of view) and --eye (eye height): those two set the scale.

Needs Depth Anything V2 Small, as in tools/experiments/depth/depth_perception.py:
the depth_anything_v2 package in tools/experiments/depth/ and
<models_dir>/depth_anything_v2_vits.pth.
"""
import _bootstrap  # noqa: F401

import argparse
import sys
import time
from pathlib import Path

import cv2

from jedai.config import settings
from jedai.perception import depth_grid as dg

ROOT = Path(__file__).resolve().parent.parent


def load_model(device):
    import torch

    sys.path.insert(0, str(ROOT / "tools" / "experiments" / "depth"))
    from depth_anything_v2.dpt import DepthAnythingV2

    m = DepthAnythingV2(encoder="vits", features=64, out_channels=[48, 96, 192, 384])
    m.load_state_dict(torch.load(str(settings.path("models_dir") / "depth_anything_v2_vits.pth"),
                                 map_location="cpu"))
    return m.to(device).eval()


HUD_KEYS = list(settings.raw.get("game", {}).get("hud_toggle_keys", ["shift", "ctrl", "h"]))


def grab_without_hud(keys, screen, settle):
    """Shift+Ctrl+H (HUD off), grab, Shift+Ctrl+H (HUD back). The bot needs the HUD for everything else."""
    keys.tap_multi(HUD_KEYS)
    time.sleep(settle)
    frame = screen.grab()
    keys.tap_multi(HUD_KEYS)
    time.sleep(settle)
    return frame


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fov", type=float, default=60.0, help="horizontal field of view, degrees")
    ap.add_argument("--eye", type=float, default=1.7, help="eye height above the ground, metres")
    ap.add_argument("--pitch", type=float, default=0.0, help="camera pitch, degrees (+ = looking down)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--image", help="process this screenshot instead of grabbing")
    ap.add_argument("--no-keys", action="store_true", help="don't zoom to first person or toggle the HUD")
    ap.add_argument("--zoom-secs", type=float, default=2.0, help="how long to hold NUMPAD+ to reach first person")
    ap.add_argument("--settle", type=float, default=0.15, help="wait after the HUD toggle before grabbing")
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args()

    out = settings.path("data_dir") / "depth_checks"
    out.mkdir(parents=True, exist_ok=True)
    model = load_model(a.device)

    keys = screen = None
    if not a.image:
        from jedai.control.channel import CommandChannel
        from jedai.control.keys import KeyClient
        from jedai.perception.screen import Screen
        screen = Screen.from_settings(settings)
        if not a.no_keys:
            keys = KeyClient(CommandChannel(settings.path("command_file")))
            print("Switch to the game: starting in 4 s...")
            time.sleep(4)
            keys.tap("add", a.zoom_secs)            # NUMPAD+ held: zoom into first person

    for n in range(a.repeat):
        if a.image:
            frame = cv2.imread(a.image)
        elif keys:
            frame = grab_without_hud(keys, screen, a.settle)
        else:
            frame = screen.grab()
        t0 = time.time()
        rel = model.infer_image(frame)
        h, w = rel.shape
        f, cx, cy = dg.intrinsics(w, h, a.fov)
        fit = dg.fit_ground_scale(rel, f, cy, a.eye, a.pitch)
        if fit is None:
            print("Couldn't find the ground in the bottom middle of the view (wall right in front?)")
            continue
        pts = dg.depth_to_points(rel, *fit, f, cx, cy, a.eye, a.pitch)
        grid, rows, cols = dg.occupancy(*pts)
        ms = (time.time() - t0) * 1000
        name = out / f"{time.strftime('%Y%m%d_%H%M%S')}_{n:02d}.png"
        cv2.imwrite(str(name), dg.render(frame, rel, grid, rows, cols))
        print(f"\n{name}  ({ms:.0f} ms)  scale a={fit[0]:.2f} b={fit[1]:.2f}")
        sym = {dg.UNKNOWN: ".", dg.CLEAR: "o", dg.BLOCKED: "#"}
        for i in range(grid.shape[0] - 1, -1, -1):
            print(f"{rows[i + 1]:4.0f}m  " + " ".join(sym[int(c)] for c in grid[i]))
        print("       " + " ".join("^" if j == grid.shape[1] // 2 else " " for j in range(grid.shape[1])))
        if n + 1 < a.repeat:
            time.sleep(a.interval)


if __name__ == "__main__":
    main()
