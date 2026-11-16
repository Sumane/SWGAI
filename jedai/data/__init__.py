"""Static game knowledge shipped with the bot (wiki-level: travel points, badge areas)."""

from __future__ import annotations

import json
from pathlib import Path


def load_world_points() -> dict:
    """{planet: {"travel": [...], "badges": [...]}} from world_points.json (x east, z north)."""
    return json.loads(Path(__file__).with_name("world_points.json").read_text())


def load_clone_routes() -> dict:
    """{planet: {city: {"spawn": [x, z], "waypoints": [[x, z], ...]}}} from clone_routes.json."""
    d = json.loads(Path(__file__).with_name("clone_routes.json").read_text())
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_travel_ports() -> dict:
    """{planet: {point name: {"terminal": [x, z], "board": [x, z], ...}}} from travel_ports.json."""
    d = json.loads(Path(__file__).with_name("travel_ports.json").read_text())
    return {k: v for k, v in d.items() if not k.startswith("_")}
