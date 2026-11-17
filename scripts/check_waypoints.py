"""
What the waypoint panel reader (YOLO group -> panel -> components -> digits) makes of a
screenshot, row by row, with the raw digit string before parse_distance.

    python scripts/check_waypoints.py data/live/find/03_find_panel2.jpg
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bootstrap  # noqa: E402,F401


def main():
    import cv2
    from jedai.config import settings
    from jedai.perception import hud as H

    frame = cv2.imread(sys.argv[1])
    if frame is None:
        sys.exit(f"can't read {sys.argv[1]}")
    r = H.WaypointReader.from_settings(settings)
    groups = r.m_group.predict(frame, conf=0.4, verbose=False)
    n = len(groups[0].boxes) if groups and groups[0].boxes is not None else 0
    print(f"groups found: {n}")
    orig = H.read_digits
    def spy(model, crop, conf=0.3):
        raw = orig(model, crop, conf)
        print(f"   raw digits: {raw!r} -> {H.parse_distance(raw)}")
        return raw
    H.read_digits = spy
    rows = list(r.waypoints(frame))
    for w in rows:
        print(f"row: name={w.name!r} distance={w.distance} arrow={w.arrow} at {w.center}")
    if not rows:
        print("no rows: the group or panel model found nothing")


if __name__ == "__main__":
    main()
