"""
Extract travel points and exploration badge areas from Core3's public
planet_manager.lua into jedai/data/world_points.json.

    curl -L -o data/core3/planet_manager.lua \
      https://raw.githubusercontent.com/swgemu/Core3/unstable/MMOCoreORB/bin/scripts/managers/planet/planet_manager.lua
    python scripts/extract_world_points.py

Wiki-level knowledge (where starports and badges are), so it's allowed at
runtime under the project rule. Axes: Lua x -> x, Lua y -> z (north),
Lua z -> height.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PLANET = re.compile(r"^(\w+)\s*=\s*\{", re.M)
_TRAVEL = re.compile(r"\{\s*name\s*=\s*\"([^\"]+)\"([^}]*)\}")
_FIELD = re.compile(r"(\w+)\s*=\s*(-?[\d.]+)")
_BADGE = re.compile(r"\{\s*\"(badge_\w+)\"\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,"
                    r"\s*(-?[\d.]+)\s*,\s*(\d+)\s*\}")


def _block(text: str, start: int) -> str:
    """The {...} block whose opening brace is at or after `start` (brace matching)."""
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise ValueError("unbalanced braces")


def _section(planet_block: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*\{{", planet_block)
    return _block(planet_block, m.start()) if m else ""


def extract(text: str) -> dict:
    out = {}
    for m in _PLANET.finditer(text):
        planet = m.group(1)
        block = _block(text, m.start())
        travel = []
        for tm in _TRAVEL.finditer(_section(block, "planetTravelPoints")):
            f = {k: float(v) for k, v in _FIELD.findall(tm.group(2))}
            inter = int(f.get("interplanetaryTravelAllowed", 0)) == 1
            travel.append({"name": tm.group(1), "x": round(f["x"], 2), "z": round(f["y"], 2),
                           "height": round(f.get("z", 0.0), 2),
                           "kind": "starport" if inter else "shuttleport",
                           "interplanetary": inter})
        badges = [{"key": k, "badge_id": int(bid), "x": round(float(x), 2), "z": round(float(y), 2),
                   "radius": round(float(r), 2)}
                  for k, x, y, _z, r, bid in _BADGE.findall(_section(block, "badgeAreas"))]
        if travel or badges:
            out[planet] = {"travel": travel, "badges": badges}
    return dict(sorted(out.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "data/core3/planet_manager.lua"))
    ap.add_argument("--out", default=str(ROOT / "jedai/data/world_points.json"))
    a = ap.parse_args()
    data = extract(Path(a.src).read_text())
    Path(a.out).write_text(json.dumps(data, indent=1) + "\n")
    for p, d in data.items():
        print(f"{p:10s} travel {len(d['travel']):3d}  badges {len(d['badges']):3d}")


if __name__ == "__main__":
    main()
