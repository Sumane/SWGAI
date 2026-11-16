"""
Using things without the mouse (Marc, 16 Nov 2026; SWG wiki "UI Commands"):

    /tar <start of name>          target the nearest thing whose name starts like that
    /ui action defaultAction      what a double-click does: use a terminal, talk to an NPC
    /ui action radialMenu         open the radial menu on the target
    /ui action conversationStart  talk to the targeted NPC
    /ui action conversationResponseX   pick answer X in an NPC conversation
    /ui action conversationStop
    /ui action untargetOnly       drop the target, no menu

No screen position needed, so it works whatever the camera is looking at.
"""

from __future__ import annotations

import time


def ui_action(keys, action: str) -> None:
    keys.chat(f"/ui action {action}")


def use_target(keys, name: str, action: str = "defaultAction", gap_s: float = 0.6, sleep=time.sleep) -> None:
    """Target by name and act on it, e.g. use_target(keys, "mission") opens the mission terminal."""
    keys.chat(f"/tar {name}")
    sleep(gap_s)
    ui_action(keys, action)
