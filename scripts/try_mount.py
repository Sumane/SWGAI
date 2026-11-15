"""
Try the mount sequence one step at a time, saving full-resolution screenshots
(and their tiles) after each step, so we can see exactly where it fails.

Needs the key controller running (harness_live.py starts one; keep it paused
with data/live/PAUSE so no harness run interferes).

    python scripts/try_mount.py [--key F1] [--cmd "/tar spe" --cmd "/mount"] [--wait 4]

Output: data/live/mount_test/<step>_<name>/ (full.jpg + tiles + chat.jpg ...).
"""
import argparse
import time

import _bootstrap  # noqa: F401
import cv2

from jedai.config import settings
from jedai.control.channel import CommandChannel
from jedai.control.keys import KeyClient
from jedai.perception.screen import Screen
from jedai.perception.tiles import save_pieces


def main():
    h = settings.raw.get("harness", {})
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="F1")
    ap.add_argument("--cmd", action="append", default=None)
    ap.add_argument("--wait", type=float, default=4.0, help="seconds after each step before the screenshot")
    a = ap.parse_args()
    cmds = a.cmd if a.cmd is not None else list(h.get("mount_commands", ["/tar spe", "/mount"]))

    from pathlib import Path
    out = Path(__file__).resolve().parents[1] / "data" / "live" / "mount_test"
    regions = {k: tuple(v) for k, v in h.get("screen_regions", {}).items()}
    screen = Screen.from_settings(settings)
    keys = KeyClient(CommandChannel(settings.path("command_file")))

    def shot(i, name):
        img = screen.grab()
        d = out / f"{i}_{name}"
        d.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(d / "full.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        save_pieces(img, d, regions=regions)
        print(f"  saved {d}")

    print("Switch to the game now: starting in 5 s")
    time.sleep(5)
    shot(0, "before")
    print(f"press {a.key}")
    keys.press(a.key)
    time.sleep(a.wait)
    shot(1, "after_" + a.key.lower())
    for i, c in enumerate(cmds, start=2):
        print(f"chat: {c}")
        keys.chat(c)
        time.sleep(a.wait)
        shot(i, "after_" + c.strip("/").replace(" ", "_"))
    print("done")


if __name__ == "__main__":
    main()
