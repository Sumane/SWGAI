"""
Record a manual walkthrough for review: you do something in the game (e.g.
delete a waypoint in the datapad) and this saves what happened.

    python scripts/capture_session.py [--name delete_waypoint] [--every 0.5] [--minutes 10]

Saves to data/captures/<time>_<name>/:
  NNNN_<t>s.jpg   full-resolution frame whenever the screen changes (or the
                  mouse button state changes), mouse pointer circled in red
                  (filled while a button is held)
  log.jsonl       one line per saved frame: time, mouse x/y, buttons held,
                  keys held (X keycodes -> names), how much the screen changed
Stop with Ctrl+C. Doesn't send any input to the game.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import _bootstrap  # noqa: F401
import cv2
import numpy as np

from jedai.config import settings
from jedai.perception.screen import Screen

ROOT = Path(__file__).resolve().parents[1]


def pointer_reader():
    """(x, y, buttons, keys) from the game's X display, or a stub if Xlib isn't usable."""
    try:
        from Xlib import X, XK, display as xdisplay
        from jedai.platform_io import x_display_name
        d = xdisplay.Display(x_display_name(settings))
        root = d.screen().root

        def names(keymap):
            out = []
            for byte_i, byte in enumerate(keymap):
                for bit in range(8):
                    if byte & (1 << bit):
                        code = byte_i * 8 + bit
                        sym = d.keycode_to_keysym(code, 0)
                        out.append(XK.keysym_to_string(sym) or str(code))
            return out

        def read():
            p = root.query_pointer()
            mask = p.mask
            buttons = [n for n, m in (("left", X.Button1Mask), ("middle", X.Button2Mask),
                                      ("right", X.Button3Mask)) if mask & m]
            return p.root_x, p.root_y, buttons, names(d.query_keymap())
        return read
    except Exception as e:  # noqa: BLE001
        print(f"(mouse/keys not recorded: {e})")
        return lambda: (None, None, [], [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="walkthrough")
    ap.add_argument("--every", type=float, default=0.5, help="seconds between checks")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--min-change", type=float, default=0.5,
                    help="percent of pixels that must change to save a frame")
    a = ap.parse_args()

    out = ROOT / "data" / "captures" / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{a.name}"
    out.mkdir(parents=True, exist_ok=True)
    screen = Screen.from_settings(settings)
    read_pointer = pointer_reader()
    log = open(out / "log.jsonl", "w")
    prev_small, prev_buttons, n, t0 = None, None, 0, time.time()
    print(f"Recording to {out}\nDo the steps in the game now. Ctrl+C to stop.")
    try:
        while time.time() - t0 < a.minutes * 60:
            frame = screen.grab()
            mx, my, buttons, keys = read_pointer()
            small = cv2.cvtColor(cv2.resize(frame, (320, 180)), cv2.COLOR_BGR2GRAY)
            changed = 100.0 if prev_small is None else \
                float((cv2.absdiff(small, prev_small) > 12).mean() * 100)
            if changed >= a.min_change or buttons != prev_buttons or keys:
                img = frame.copy()
                if mx is not None:
                    cv2.circle(img, (mx, my), 14, (0, 0, 255), -1 if buttons else 3)
                t = time.time() - t0
                name = f"{n:04d}_{t:06.1f}s.jpg"
                cv2.imwrite(str(out / name), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
                log.write(json.dumps({"file": name, "t": round(t, 2), "mouse": [mx, my],
                                      "buttons": buttons, "keys": keys,
                                      "changed_pct": round(changed, 2)}) + "\n")
                log.flush()
                n += 1
                print(f"\r{n} frames saved", end="", flush=True)
                prev_small = small
            prev_buttons = buttons
            time.sleep(a.every)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
    print(f"\nDone: {n} frames in {out}")


if __name__ == "__main__":
    main()
