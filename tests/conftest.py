"""
Offline test setup: stub out Windows-only / GPU libraries so everything can be
imported and tested without the game, a GPU, or Windows.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _stub(name, **attrs):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


import os  # noqa: E402

os.environ.setdefault("JEDAI_NO_RECORD", "1")   # tests don't write run recordings into data/

_stub("keyboard", is_pressed=lambda key: False)
_stub("pydirectinput", press=lambda k: None, keyDown=lambda k: None, keyUp=lambda k: None)
try:
    import mss  # noqa: F401  (real one if installed: the Linux backend tests use it)
except ImportError:
    _stub("mss", mss=None)
_stub("easyocr", Reader=None)
_stub("ultralytics", YOLO=None)


# The kill-key check talks to the real keyboard / X server; tests never press it.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_kill_key(monkeypatch):
    import jedai.control.keys as keys

    class Never:
        def is_pressed(self, key):
            return False

    monkeypatch.setattr(keys, "_key_state", Never())
