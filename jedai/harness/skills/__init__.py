"""System 1 skills available to the harness."""

from __future__ import annotations

from ..skill import SkillRegistry
from .datapad import CleanWaypoints
from .navigation import Dismount, Mount, NavigateTo
from .recover import RecoverFromDeath
from .travel import TravelTo


def default_registry(runner, state, settings) -> SkillRegistry:
    reg = SkillRegistry()
    for cls in (NavigateTo, Mount, Dismount, CleanWaypoints, RecoverFromDeath, TravelTo):
        reg.register(cls(runner, settings))
    return reg
