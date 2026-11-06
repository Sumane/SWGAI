"""Static game knowledge shipped with the bot (wiki-level: travel points, badge areas)."""

from __future__ import annotations

import json
from pathlib import Path


def load_world_points() -> dict:
    """{planet: {"travel": [...], "badges": [...]}} from world_points.json (x east, z north)."""
    return json.loads(Path(__file__).with_name("world_points.json").read_text())
