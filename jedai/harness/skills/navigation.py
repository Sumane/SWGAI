"""Movement skills: navigate_to (via an in-game waypoint), mount, dismount."""

from __future__ import annotations

import time

from ..skill import Result, Skill
from ..state import WorldState


def _sync_mount(runner, state: WorldState) -> None:
    state.set("mounted", bool(getattr(runner, "mounted", False)))
    state.set("vehicle", getattr(runner, "current_vehicle", None))


class NavigateTo(Skill):
    """Make a waypoint with the chat command, then let the navigator drive to it."""
    name = "navigate_to"

    def __init__(self, runner, settings, sleep=time.sleep):
        self.runner, self.settings, self.sleep = runner, settings, sleep

    def run(self, state: WorldState, x: float, z: float, label: str, **_) -> Result:
        h = self.settings.raw["harness"]
        self.runner.keys.type_text(h["waypoint_command"].format(x=x, z=z, label=label))
        self.sleep(float(h.get("waypoint_settle_s", 1.5)))
        outcome = self.runner.navigate(target_name=label)
        pos = self.runner.read_position()
        if pos is not None:
            state.set_position(self.settings.planet, pos[0], pos[1])
        _sync_mount(self.runner, state)
        data = {"outcome": outcome}
        if pos is not None:
            data["dist_to_target"] = round(((pos[0] - x) ** 2 + (pos[1] - z) ** 2) ** 0.5, 1)
        if outcome == "arrived":
            return Result("ok", "", data)
        if outcome == "no_waypoint":
            return Result("failed", "no_waypoint", data)
        if outcome.startswith("crashed"):
            return Result("failed", "crashed", data)
        return Result("stuck", outcome, data)


class Mount(Skill):
    """Call the vehicle with its toolbar key, then switch the navigator to that vehicle."""
    name = "mount"

    def __init__(self, runner, settings, sleep=time.sleep):
        self.runner, self.settings, self.sleep = runner, settings, sleep

    def preconditions(self, state, **args):
        return "already mounted" if state.get("mounted") else None

    def run(self, state, **args) -> Result:
        h = self.settings.raw["harness"]
        key = h.get("mount_key", "")
        if not key:
            return Result("failed", "no_mount_key")
        self.runner.keys.press(key)
        self.sleep(float(h.get("mount_settle_s", 3.0)))
        self.runner.apply_vehicle(self.settings.nav.get("default_vehicle", "swoop"))
        _sync_mount(self.runner, state)
        return Result("ok")


class Dismount(Skill):
    name = "dismount"

    def __init__(self, runner, settings, sleep=time.sleep):
        self.runner, self.settings, self.sleep = runner, settings, sleep

    def preconditions(self, state, **args):
        return None if state.get("mounted") else "not mounted"

    def run(self, state, **args) -> Result:
        c = self.settings.nav
        # not runner.dismount(): that also logs to the per-run recorder, which is
        # closed between navigation runs
        self.runner.keys.dismount(c.get("dismount_method", "command"),
                                  c.get("dismount_command", "/dismount"), c.get("dismount_key", ""))
        self.sleep(float(c.get("dismount_settle_secs", 2.0)))
        self.runner.apply_vehicle(c.get("foot_vehicle", "foot"))
        _sync_mount(self.runner, state)
        return Result("ok")
