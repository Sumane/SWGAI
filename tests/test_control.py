import threading
import time

from jedai.config import load_settings
from jedai.control.channel import CommandChannel
from jedai.control.controller import MasterController
from jedai.control.keys import KeyClient

KEYS = load_settings(local_file="none.yaml").keys


class FakeInput:
    def __init__(self):
        self.events = []

    def press(self, k): self.events.append(("press", k))
    def keyDown(self, k): self.events.append(("down", k))
    def keyUp(self, k): self.events.append(("up", k))


class StopAfter:
    def __init__(self, seconds):
        self.until = time.time() + seconds

    def is_pressed(self, key):
        return time.time() > self.until


def test_channel_roundtrip(tmp_path):
    ch = CommandChannel(tmp_path / "sub" / "nav_state.json")
    assert ch.write({"cmd": "TAP", "key": "LEFT"})
    assert ch.read() == {"cmd": "TAP", "key": "LEFT"}
    assert not list(tmp_path.glob("sub/*.tmp"))     # temp file swapped away


def test_controller_maps_logical_keys(tmp_path):
    inp = FakeInput()
    mc = MasterController(CommandChannel(tmp_path / "c.json"), KEYS, "f8", inp, StopAfter(0))
    mc.execute({"cmd": "KEY_DOWN", "key": "FORWARD"})
    mc.execute({"cmd": "PRESS", "key": "AUTORUN"})
    mc.execute({"cmd": "TAP", "key": "LEFT", "duration": 0})
    mc.execute({"cmd": "RELEASE_ALL"})
    assert ("down", "up") in inp.events and ("press", "numlock") in inp.events
    assert ("down", "left") in inp.events and ("up", "left") in inp.events
    assert "f8" not in [k for _, k in inp.events]      # kill key never pressed
    assert mc.autorun_on is False                      # released by RELEASE_ALL


def test_navigator_and_controller_end_to_end(tmp_path):
    """KeyClient -> file -> MasterController -> key events, with acknowledgement."""
    path = tmp_path / "nav_state.json"
    inp = FakeInput()
    mc = MasterController(CommandChannel(path), KEYS, "f8", inp, StopAfter(2.0))
    t = threading.Thread(target=mc.run, daemon=True)
    t.start()
    kc = KeyClient(CommandChannel(path), ack_timeout=1.0)
    kc.start_autorun()
    kc.key_down("FORWARD")
    kc.stop_movement()
    t.join(3)
    assert inp.events[:3] == [("press", "numlock"), ("down", "up"), ("press", "numlock")]
    assert ("up", "up") in inp.events
