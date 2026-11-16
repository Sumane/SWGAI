"""
Read the character's position out of every frame of a recording
(scripts/capture_session.py) and save the track, so recorded walks can be
turned into routes without anyone reading coordinates off screenshots.

    python scripts/route_from_capture.py data/captures/2026-11-16_101455_shuttle_theed_keren

Writes <capture>/track.json: [{"i": frame, "t": seconds, "x": .., "y": height, "z": ..}, ...]
(frames where the coordinates couldn't be read are left out).
"""
import json
import sys
from pathlib import Path

import _bootstrap  # noqa: F401
import cv2

from jedai.config import settings
from jedai.perception.hud import HudReader

if __name__ == "__main__":
    cap = Path(sys.argv[1])
    hud = HudReader.from_settings(settings)
    log = [json.loads(ln) for ln in (cap / "log.jsonl").read_text().splitlines() if ln.strip()]
    out, last = [], None
    for i, e in enumerate(log):
        frame = cv2.imread(str(cap / e["file"]))
        if frame is None:
            continue
        x, y, z = hud.read_coords(frame)
        if x is None or z is None or (x == z and abs(x) > 50):
            continue
        out.append({"i": i, "t": e["t"], "x": x, "y": y, "z": z, "buttons": e.get("buttons", [])})
        if i % 25 == 0:
            print(f"{i}/{len(log)}  ({x}, {y}, {z})")
    (cap / "track.json").write_text(json.dumps(out))
    print(f"{len(out)} positions from {len(log)} frames -> {cap / 'track.json'}")
