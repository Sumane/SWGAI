"""
Settings loader.

Reads config/settings.yaml, then overlays config/settings.local.yaml if it
exists (or the file named by the JEDAI_SETTINGS environment variable).

    from jedai.config import settings
    settings.nav["stop_dist"]          # -> 50
    settings.path("nav_db")            # -> absolute Path
    settings.model_path("minimap")     # -> .../models/mini_map_v01.00/weights/best.pt
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_FILE = CONFIG_DIR / "settings.yaml"
LOCAL_FILE = CONFIG_DIR / "settings.local.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def resolve_path(p: str | os.PathLike) -> Path:
    """Absolute paths are kept; relative ones are relative to the repo root."""
    p = Path(os.path.expandvars(os.path.expanduser(str(p))))
    return p if p.is_absolute() else (REPO_ROOT / p)


class Settings:
    def __init__(self, data: dict):
        self.raw = data

    # Section shortcuts
    @property
    def nav(self) -> dict:
        return self.raw["navigation"]

    @property
    def planner(self) -> dict:
        return self.raw["planner"]

    @property
    def keys(self) -> dict:
        return self.raw["keys"]

    @property
    def planet(self) -> str:
        return self.raw["game"]["planet"]

    @property
    def monitor_index(self) -> int:
        return int(self.raw["screen"]["monitor_index"])

    @property
    def kill_key(self) -> str:
        return self.raw["keys"].get("kill", "f8")

    def path(self, key: str) -> Path:
        """A path from the `paths:` section, made absolute."""
        return resolve_path(self.raw["paths"][key])

    def data_path(self, *parts: str) -> Path:
        """A path inside data_dir."""
        return self.path("data_dir").joinpath(*parts)

    def model_path(self, name: str) -> Path:
        """
        Resolve a model to its weights file. `name` can be:
          - a key from the `models:` section ("minimap")
          - a folder name under models_dir ("mini_map_v01.00")
          - a path to a .pt file
        """
        folder = self.raw.get("models", {}).get(name, name)
        p = Path(str(folder))
        if p.suffix.lower() == ".pt":
            return resolve_path(p)
        base = p if p.is_absolute() else self.path("models_dir") / p
        weights = base / "weights"
        for candidate in ("best.pt", "BEST.PT", "Best.pt"):
            if (weights / candidate).exists():
                return weights / candidate
        return weights / "best.pt"

    def load_vehicles(self) -> dict:
        """Vehicle speed table. Falls back to built-in defaults (with a warning)."""
        defaults = {
            "walking": {"max": 5.2},
            "running": {"max": 10.5},
            "speederbike": {"max": 14.0},
            "swoop": {"max": 16.0},
        }
        path = self.path("vehicles")
        try:
            with open(path) as f:
                defaults.update(json.load(f))
        except FileNotFoundError:
            print(f"[config] {path} not found, using built-in vehicle speeds")
        except json.JSONDecodeError as e:
            print(f"[config] WARNING: {path} is not valid JSON ({e}); using built-in speeds")
        return defaults


def load_settings(local_file: str | os.PathLike | None = None) -> Settings:
    with open(DEFAULT_FILE) as f:
        data = yaml.safe_load(f)
    local = local_file or os.environ.get("JEDAI_SETTINGS") or LOCAL_FILE
    local = resolve_path(local)
    if local.exists():
        with open(local) as f:
            data = _deep_merge(data, yaml.safe_load(f) or {})
    return Settings(data)


settings = load_settings()
