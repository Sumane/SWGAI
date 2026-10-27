"""
Master controller: the dumb key executor. Watches the command channel and
fires the matching key presses with pydirectinput. Run it before the navigator.
"""

from __future__ import annotations

import time

from .channel import CommandChannel


class MasterController:
    def __init__(self, channel: CommandChannel, keymap: dict, kill_key: str = "f8",
                 input_backend=None, key_state=None):
        """
        keymap: logical -> physical key, e.g. {"FORWARD": "up"}
        input_backend / key_state: injectable for tests (default: pydirectinput / keyboard)
        """
        self.channel = channel
        self.keymap = {k: v for k, v in keymap.items() if k != "kill"}
        self.kill_key = kill_key
        if input_backend is None or key_state is None:
            from ..config import settings
            from ..platform_io import make_input, make_key_state
            input_backend = input_backend or make_input(settings)
            key_state = key_state or make_key_state(settings)
        self.input = input_backend
        self.key_state = key_state
        self.autorun_on = False
        self.channel.write({"cmd": "IDLE"})

    def _key(self, logical: str | None) -> str | None:
        if not logical:
            return None
        return self.keymap.get(logical, logical.lower())

    def execute(self, instruction: dict):
        """Run one command. Returns True if it was a real command (not IDLE)."""
        cmd = instruction.get("cmd")
        if cmd == "IDLE" or cmd is None:
            return False

        if cmd == "PRESS":
            logical = instruction.get("key")
            physical = self._key(logical)
            if physical:
                self.input.press(physical)
                if logical == "AUTORUN":
                    self.autorun_on = instruction.get("state", not self.autorun_on)

        elif cmd == "KEY_DOWN":
            physical = self._key(instruction.get("key"))
            if physical:
                self.input.keyDown(physical)

        elif cmd == "KEY_UP":
            physical = self._key(instruction.get("key"))
            if physical:
                self.input.keyUp(physical)

        elif cmd == "TAP":
            physical = self._key(instruction.get("key"))
            if physical:
                self.input.keyDown(physical)
                time.sleep(instruction.get("duration", 0.0))
                self.input.keyUp(physical)

        elif cmd == "TAP_MULTI":
            physicals = [p for p in (self._key(k) for k in instruction.get("keys", [])) if p]
            for p in physicals:
                self.input.keyDown(p)
            time.sleep(instruction.get("duration", 0.0))
            for p in reversed(physicals):
                self.input.keyUp(p)

        elif cmd == "TYPE":
            # Type text character by character (e.g. a chat command)
            for ch in instruction.get("text", ""):
                self.input.press(ch)
                time.sleep(0.02)

        elif cmd == "RELEASE_ALL":
            self.release_all()

        elif cmd == "STOP_AUTORUN":
            if self.autorun_on:
                self.input.press(self.keymap["AUTORUN"])
                self.autorun_on = False

        else:
            print(f"[MC] Unknown command: {instruction}")
        return True

    def release_all(self):
        for physical in self.keymap.values():
            self.input.keyUp(physical)
        if self.autorun_on:
            self.input.press(self.keymap["AUTORUN"])
            self.autorun_on = False

    def run(self):
        print("=== Master Controller Online ===")
        print(f"Watching: {self.channel.path}")
        print(f"Press {self.kill_key.upper()} to stop.\n")
        while not self.key_state.is_pressed(self.kill_key):
            instruction = self.channel.read()
            if instruction and self.execute(instruction):
                self.channel.write({"cmd": "IDLE"})   # acknowledge
                time.sleep(0.005)
            else:
                time.sleep(0.01)
        print("\n[MC] Kill key detected. Releasing all keys and exiting.")
        for physical in self.keymap.values():
            self.input.keyUp(physical)
