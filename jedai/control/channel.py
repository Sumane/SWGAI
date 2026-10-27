"""
The command channel between the navigator and the master controller.

It's a single small JSON file. The navigator writes a command, the controller
executes it and writes {"cmd": "IDLE"} back as the acknowledgement.

Commands:
    {"cmd": "IDLE"}
    {"cmd": "PRESS",    "key": "AUTORUN", "state": true}   # state optional
    {"cmd": "KEY_DOWN", "key": "FORWARD"}
    {"cmd": "KEY_UP",   "key": "FORWARD"}
    {"cmd": "TAP",      "key": "LEFT", "duration": 0.1}
    {"cmd": "TAP_MULTI","keys": ["BACK", "LEFT"], "duration": 0.5}
    {"cmd": "RELEASE_ALL"}
    {"cmd": "STOP_AUTORUN"}

Writes go to a temp file and are swapped in with os.replace, so the other side
never reads a half-written file. On Windows the swap can briefly fail while the
other process has the file open, so it retries.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class CommandChannel:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")

    def write(self, payload: dict, retries: int = 50) -> bool:
        data = json.dumps(payload)
        for _ in range(retries):
            try:
                with open(self._tmp, "w") as f:
                    f.write(data)
                os.replace(self._tmp, self.path)
                return True
            except PermissionError:
                time.sleep(0.002)   # other side has it open; try again
            except OSError as e:
                print(f"[channel] write error: {e}")
                return False
        # Last resort: plain overwrite (what the original code did)
        try:
            with open(self.path, "w") as f:
                f.write(data)
            return True
        except OSError as e:
            print(f"[channel] write error: {e}")
            return False

    def read(self) -> dict | None:
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def wait_for_idle(self, timeout: float = 2.0, poll: float = 0.005) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.read()
            if state and state.get("cmd") == "IDLE":
                return True
            time.sleep(poll)
        return False
