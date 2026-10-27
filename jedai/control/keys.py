"""
Navigator-side key commands. Sends logical keys (FORWARD, LEFT, AUTORUN...)
through the command channel; the master controller turns them into key presses.
"""

from __future__ import annotations

import time

from .channel import CommandChannel


_key_state = None


def kill_pressed(kill_key: str, settings=None) -> bool:
    """True if the kill key was pressed since the last check (watched on a background thread)."""
    global _key_state
    if _key_state is None:
        if settings is None:
            from ..config import settings
        from ..platform_io import make_key_state
        _key_state = make_key_state(settings, latch_keys=[kill_key])
    return _key_state.is_pressed(kill_key)


class KeyClient:
    def __init__(self, channel: CommandChannel, ack_timeout: float = 2.0):
        self.channel = channel
        self.ack_timeout = ack_timeout
        self.autorun_active = False

    def send(self, payload: dict, wait_for_idle: bool = True):
        if not self.channel.write(payload):
            return
        if wait_for_idle and not self.channel.wait_for_idle(self.ack_timeout):
            print(f"\n[keys] No acknowledgement for {payload.get('cmd')} "
                  f"within {self.ack_timeout}s. Is the controller running?")

    def press(self, key: str, state=None):
        payload = {"cmd": "PRESS", "key": key}
        if state is not None:
            payload["state"] = state
        self.send(payload)

    def tap(self, key: str, duration: float = 0.0):
        self.send({"cmd": "TAP", "key": key, "duration": duration})
        time.sleep(duration + 0.05)

    def tap_multi(self, keys, duration: float = 0.05):
        """Hold several keys together, e.g. ["ctrl", "h"]."""
        self.send({"cmd": "TAP_MULTI", "keys": list(keys), "duration": duration})
        time.sleep(duration + 0.05)

    def key_down(self, key: str):
        self.send({"cmd": "KEY_DOWN", "key": key})

    def key_up(self, key: str):
        self.send({"cmd": "KEY_UP", "key": key})

    def release_all(self):
        self.send({"cmd": "RELEASE_ALL"})
        self.autorun_active = False

    def type_text(self, text: str):
        self.send({"cmd": "TYPE", "text": text})

    def dismount(self, method: str = "command", command: str = "/dismount", key: str = ""):
        """Get off the vehicle: type a chat command, or press a (toolbar) key."""
        self.stop_movement()
        if method == "key":
            if not key:
                raise ValueError("navigation.dismount_key is empty but dismount_method is 'key'")
            self.press(key)
        else:
            self.press("ENTER")
            self.type_text(command)
            self.press("ENTER")

    # Movement helpers -------------------------------------------------------
    def start_autorun(self):
        if not self.autorun_active:
            self.press("AUTORUN")
            self.autorun_active = True

    def stop_movement(self):
        if self.autorun_active:
            self.press("AUTORUN")
            self.autorun_active = False
        self.key_up("FORWARD")
