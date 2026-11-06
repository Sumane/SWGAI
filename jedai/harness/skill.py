"""
The System 1 contract: every in-game action is a Skill returning a Result,
so System 2 can choose between them and the log treats them all the same.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .state import WorldState


@dataclass
class Result:
    status: str                    # "ok" | "failed" | "stuck"
    reason: str = ""               # short machine-readable reason
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class Skill:
    name: str = "skill"

    def preconditions(self, state: WorldState, **args) -> str | None:
        """None if the skill can run now, else why not."""
        return None

    def run(self, state: WorldState, **args) -> Result:
        """Do it. In-game problems return failed/stuck rather than raising."""
        raise NotImplementedError


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"skill '{skill.name}' already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        return self._skills[name]

    def names(self) -> list[str]:
        return sorted(self._skills)
