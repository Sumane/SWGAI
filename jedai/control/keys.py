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

    def send(self, payload: dict, wait_for_idle: bool = True, timeout: float | None = None):
        if not self.channel.write(payload):
            return
        timeout = self.ack_timeout if timeout is None else timeout
        if wait_for_idle and not self.channel.wait_for_idle(timeout):
            print(f"\n[keys] No acknowledgement for {payload.get('cmd')} "
                  f"within {timeout:.1f}s. Is the controller running?")

    def press(self, key: str, state=None):
        payload = {"cmd": "PRESS", "key": key}
        if state is not None:
            payload["state"] = state
        self.send(payload)

    def tap(self, key: str, duration: float = 0.0):
        # the controller acknowledges after it lets go, so a turn held for 2+ s used to print
        # "No acknowledgement ... Is the controller running?" every time (17 Nov)
        self.send({"cmd": "TAP", "key": key, "duration": duration}, timeout=self.ack_timeout + duration)
        time.sleep(0.05)

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
        # typing takes ~50 ms a character: wait for all of it, or the next command
        # (the Enter that sends it) overwrites the channel and is lost
        self.send({"cmd": "TYPE", "text": text}, timeout=self.ack_timeout + 0.12 * len(text))

    def click(self, x: int, y: int, button: str = "left"):
        """Mouse click at screen pixel (x, y) of the game display."""
        self.send({"cmd": "CLICK", "x": int(x), "y": int(y), "button": button})

    def move_mouse(self, x: int, y: int):
        self.send({"cmd": "MOVE", "x": int(x), "y": int(y)})

    def combo(self, *keys: str, duration: float = 0.08):
        """Hold keys together, e.g. combo("CTRL", "d") for the datapad."""
        self.send({"cmd": "TAP_MULTI", "keys": list(keys), "duration": duration})

    def chat(self, text: str, open_wait: float = 0.4, send_wait: float = 0.3):
        """Send a chat line / slash command: Enter, type it, Enter.
        The pauses let the chat box open before typing and finish before sending
        (without them SWG received "/mount" as "mmount")."""
        import time
        self.press("ENTER")
        time.sleep(open_wait)
        self.type_text(text)
        time.sleep(send_wait)
        self.press("ENTER")

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
