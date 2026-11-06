"""System 1 skills available to the harness."""

from __future__ import annotations

from ..skill import SkillRegistry
from .navigation import Dismount, Mount, NavigateTo


def default_registry(runner, state, settings) -> SkillRegistry:
    reg = SkillRegistry()
    for cls in (NavigateTo, Mount, Dismount):
        reg.register(cls(runner, settings))
    return reg
