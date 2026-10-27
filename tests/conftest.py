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


_stub("keyboard", is_pressed=lambda key: False)
_stub("pydirectinput", press=lambda k: None, keyDown=lambda k: None, keyUp=lambda k: None)
_stub("mss", mss=None)
_stub("easyocr", Reader=None)
_stub("ultralytics", YOLO=None)
