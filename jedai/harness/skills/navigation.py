"""Movement skills: navigate_to (via an in-game waypoint), mount, dismount."""

from __future__ import annotations

import math
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

    def _drive(self, state, x, z, label, stop_dist=None):
        here = state.position()
        expect = math.hypot(x - here[1], z - here[2]) if here else None
        # find our waypoint by its distance (the game doesn't keep the label);
        # drive to the known coordinates
        try:
            outcome = self.runner.navigate(target_name=label, expect_dist=expect,
                                           target_xz=(x, z), stop_dist=stop_dist)
        finally:
            # FIX (7 Nov): a crashed run skipped this, so the harness still
            # believed it was mounted after the navigator had dismounted
            _sync_mount(self.runner, state)
        pos = self.runner.read_position()
        if pos is not None:
            state.set_position(self.settings.planet, pos[0], pos[1])
        return outcome, (math.hypot(pos[0] - x, pos[1] - z) if pos is not None else None)

    def _walk_in(self, state, x, z, label, stop):
        """Get off (if mounted) and walk to within `stop` metres."""
        if getattr(self.runner, "mounted", False):
            c = self.settings.nav
            self.runner.keys.dismount(c.get("dismount_method", "command"),
                                      c.get("dismount_command", "/dismount"), c.get("dismount_key", ""))
            self.sleep(float(c.get("dismount_settle_secs", 2.0)))
            self.runner.store_vehicle()
            self.runner.apply_vehicle(c.get("foot_vehicle", "running"))
            _sync_mount(self.runner, state)
        return self._drive(state, x, z, label, stop_dist=stop)

    def _badge_confirmed(self, baseline):
        """New 'badge' line in chat since `baseline` (the mark from runner.chat_mark())."""
        self.sleep(float(self.settings.raw["harness"].get("badge_message_wait_s", 2.5)))
        return [ln for ln in self.runner.chat_since(baseline) if "badge" in ln]

    def run(self, state: WorldState, x: float, z: float, label: str, radius: float | None = None,
            stop: float | None = None, **_) -> Result:
        """radius: a badge area (arrival is confirmed from chat). stop: just get within this many
        metres (a terminal, a doorway), walking the last part."""
        h = self.settings.raw["harness"]
        badge = bool(radius)
        verify = badge and h.get("verify_badges", True)
        baseline = self.runner.chat_mark() if verify else None
        self.runner.keys.chat(h["waypoint_command"].format(x=x, z=z, label=label))
        self.sleep(float(h.get("waypoint_settle_s", 1.5)))
        outcome, d = self._drive(state, x, z, label)
        data = {"outcome": outcome}
        if outcome == "arrived" and stop and not badge and d is not None and d > stop:
            outcome, d = self._walk_in(state, x, z, label, stop)
            data["outcome"] = outcome
        if outcome == "arrived" and badge:
            # a vehicle stops ~50 m out; badges need you inside a ~10 m circle
            if d is not None and d > radius:
                outcome, d = self._walk_in(state, x, z, label, max(3.0, 0.6 * radius))
            confirmed = self._badge_confirmed(baseline) if verify and outcome == "arrived" else []
            if verify and outcome == "arrived" and not confirmed:
                # nothing in chat yet: go right onto the spot and look again
                outcome, d = self._walk_in(state, x, z, label, 3.0)
                if outcome == "arrived":
                    confirmed = self._badge_confirmed(baseline)
            data["outcome"] = outcome
            if d is not None:
                data["dist_to_target"] = round(d, 1)
            if confirmed:
                data["badge_message"] = confirmed[-1]
                return Result("ok", "badge_confirmed", data)
            if verify and outcome == "arrived":
                return Result("failed", "no_badge_message", data)
            if outcome == "arrived" and d is not None and d > radius + 3:
                return Result("stuck", "outside_radius", data)
        if d is not None:
            data["dist_to_target"] = round(d, 1)
        if outcome == "arrived":
            return Result("ok", "", data)
        if outcome == "dead":
            return Result("failed", "dead", data)
        if outcome == "no_waypoint":
            return Result("failed", "no_waypoint", data)
        if outcome.startswith("crashed"):
            return Result("failed", "crashed", data)
        return Result("stuck", outcome, data)


class FollowWaypoint(Skill):
    """Drive to a waypoint that is already set (e.g. by /find) when its position isn't known:
    the navigator picks it by the distance it shows and estimates where it is as it goes."""
    name = "follow_waypoint"

    def __init__(self, runner, settings, sleep=time.sleep):
        self.runner, self.settings, self.sleep = runner, settings, sleep

    def run(self, state, expect: float | None = None, label: str | None = None, stop: float | None = None, **_) -> Result:
        try:
            outcome = self.runner.navigate(target_name=label, expect_dist=expect, target_xz=None, stop_dist=stop)
        finally:
            _sync_mount(self.runner, state)
        pos = self.runner.read_position()
        if pos is not None:
            state.set_position(self.settings.planet, pos[0], pos[1])
        data = {"outcome": outcome}
        if outcome == "arrived":
            return Result("ok", "", data)
        if outcome in ("dead", "no_waypoint") or outcome.startswith("crashed"):
            return Result("failed", outcome.split(":")[0], data)
        return Result("stuck", outcome, data)


class Mount(Skill):
    """Call the vehicle with its toolbar key (e.g. F1), then the chat commands that
    get on it (e.g. /tar spe, /mount), then switch the navigator to that vehicle."""
    name = "mount"

    def __init__(self, runner, settings, sleep=time.sleep):
        self.runner, self.settings, self.sleep = runner, settings, sleep

    def preconditions(self, state, **args):
        return "already mounted" if state.get("mounted") else None

    def run(self, state, **args) -> Result:
        if not self.settings.raw["harness"].get("mount_key", ""):
            return Result("failed", "no_mount_key")
        self.runner.mount()
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
        self.runner.store_vehicle()
        self.runner.apply_vehicle(c.get("foot_vehicle", "running"))
        _sync_mount(self.runner, state)
        return Result("ok")
