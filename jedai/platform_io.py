"""
Platform-specific input and key state.

    make_input(settings)      -> object with press / keyDown / keyUp   (used by the controller)
    make_key_state(settings)  -> object with is_pressed(key)            (kill key)
    capture_display(settings) -> X display name for screen capture, or None

Windows: pydirectinput + keyboard (as before).
Linux:   X11 XTest via python-xlib, aimed at one X display (config
         platform.x_display, e.g. ":2" for the game's own Xwayland window).
         No root needed, and keys only go to that display.
"""

from __future__ import annotations

import os
import sys
import time

# pydirectinput key names -> X keysym names
_KEYSYMS = {
    "left": "Left", "right": "Right", "up": "Up", "down": "Down",
    "numlock": "Num_Lock", "enter": "Return", "return": "Return", "esc": "Escape",
    "escape": "Escape", "space": "space", " ": "space", "tab": "Tab",
    "backspace": "BackSpace", "delete": "Delete", "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next", "insert": "Insert",
    "shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L",
    "/": "slash", "-": "minus", "=": "equal", ".": "period", ",": "comma",
    ";": "semicolon", "'": "apostrophe", "[": "bracketleft", "]": "bracketright",
    "\\": "backslash", "`": "grave",
}
_KEYSYMS.update({f"f{i}": f"F{i}" for i in range(1, 13)})


def keysym_name(key: str) -> str:
    """pydirectinput-style key name -> X keysym name ('left' -> 'Left', '/' -> 'slash')."""
    if key in _KEYSYMS:
        return _KEYSYMS[key]
    low = key.lower()
    if low in _KEYSYMS:
        return _KEYSYMS[low]
    return low if len(key) == 1 else key


def x_display_name(settings) -> str | None:
    name = settings.raw.get("platform", {}).get("x_display") or os.environ.get("DISPLAY")
    return name or None


def backend_name(settings) -> str:
    b = settings.raw.get("platform", {}).get("input_backend", "auto")
    if b != "auto":
        return b
    return "pydirectinput" if sys.platform == "win32" else "xtest"


# ---------------------------------------------------------------------------
class XTestInput:
    """Sends key events to one X display with the XTest extension."""

    def __init__(self, display_name: str | None = None, press_hold: float = 0.03):
        from Xlib import display as xdisplay

        self.d = xdisplay.Display(display_name)
        if not self.d.has_extension("XTEST"):
            raise RuntimeError(f"X display {display_name or os.environ.get('DISPLAY')} has no XTEST extension")
        self.press_hold = press_hold
        self._cache = {}

    def _keycode(self, key: str) -> int:
        if key not in self._cache:
            from Xlib import XK

            ks = XK.string_to_keysym(keysym_name(key))
            if ks == 0 and len(key) == 1:
                ks = ord(key)
            kc = self.d.keysym_to_keycode(ks)
            if not kc:
                raise ValueError(f"No keycode for key {key!r} on this X display")
            self._cache[key] = kc
        return self._cache[key]

    def _fake(self, event_type, key):
        from Xlib.ext import xtest

        xtest.fake_input(self.d, event_type, self._keycode(key))
        self.d.sync()

    def keyDown(self, key: str):
        from Xlib import X

        self._fake(X.KeyPress, key)

    def keyUp(self, key: str):
        from Xlib import X

        self._fake(X.KeyRelease, key)

    def press(self, key: str):
        shifted = len(key) == 1 and key.isupper()
        if shifted:
            self.keyDown("shift")
        self.keyDown(key.lower() if shifted else key)
        time.sleep(self.press_hold)        # games poll input; an instant tap can be missed
        self.keyUp(key.lower() if shifted else key)
        if shifted:
            self.keyUp("shift")


class XKeyState:
    """Answers 'is this key held right now?' for one X display (no root needed)."""

    def __init__(self, display_name: str | None = None):
        from Xlib import display as xdisplay

        self.d = xdisplay.Display(display_name)
        self._cache = {}

    def is_pressed(self, key: str) -> bool:
        from Xlib import XK

        if key not in self._cache:
            self._cache[key] = self.d.keysym_to_keycode(XK.string_to_keysym(keysym_name(key)))
        kc = self._cache[key]
        if not kc:
            return False
        keymap = self.d.query_keymap()
        return bool(keymap[kc // 8] & (1 << (kc % 8)))


# ---------------------------------------------------------------------------
def make_input(settings):
    name = backend_name(settings)
    if name == "pydirectinput":
        import pydirectinput
        return pydirectinput
    if name == "xtest":
        return XTestInput(x_display_name(settings))
    raise ValueError(f"Unknown platform.input_backend: {name}")


class LatchedKeyState:
    """
    Watches some keys on a background thread (every 20 ms) and remembers a
    press until it is read. FIX: the navigator only checks the kill key once a
    tick, and a tick takes ~2 s while it reads the HUD, so a quick tap of F8
    was usually missed.
    """

    def __init__(self, make_inner, keys, poll_s: float = 0.02):
        import threading

        self._make_inner = make_inner
        self._keys = [k for k in keys if k]
        self._poll = poll_s
        self._seen = set()
        self._lock = threading.Lock()
        self._inner = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="key-latch", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def _run(self):
        import time

        try:
            self._inner = self._make_inner()   # X connections aren't shared across threads
        finally:
            self._ready.set()
        while True:
            for k in self._keys:
                try:
                    if self._inner.is_pressed(k):
                        with self._lock:
                            self._seen.add(k)
                except Exception:
                    pass
            time.sleep(self._poll)

    def is_pressed(self, key: str) -> bool:
        # Sticky: once the kill key was seen it stays pressed. FIX: reading it
        # cleared it, so if an inner loop saw the press and returned, the main
        # loop didn't, and the bot carried on after F8.
        with self._lock:
            return key in self._seen


def make_key_state(settings, latch_keys=None):
    """
    Key state reader. With latch_keys, those keys are watched on a background
    thread so a short press between two checks still counts.
    """
    if backend_name(settings) == "pydirectinput":
        import keyboard
        inner = lambda: keyboard
    else:
        name = x_display_name(settings)
        inner = lambda: XKeyState(name)
    if latch_keys:
        return LatchedKeyState(inner, latch_keys)
    return inner()


def capture_display(settings) -> str | None:
    """X display to capture from on Linux (None = mss default / Windows)."""
    if sys.platform == "win32":
        return None
    return x_display_name(settings)
