"""
Linux X11 backend. The mapping test always runs; the rest start a throwaway
Xvfb server and skip if it isn't installed.
"""
import shutil
import subprocess
import sys
import threading
import time

import pytest

from jedai.platform_io import keysym_name


def test_keysym_names():
    assert keysym_name("left") == "Left" and keysym_name("numlock") == "Num_Lock"
    assert keysym_name("enter") == "Return" and keysym_name("f8") == "F8"
    assert keysym_name("/") == "slash" and keysym_name("d") == "d" and keysym_name("9") == "9"


@pytest.fixture(scope="module")
def xdisplay():
    if not shutil.which("Xvfb") or sys.platform != "linux":
        pytest.skip("Xvfb not installed")
    pytest.importorskip("Xlib")
    disp = ":97"
    proc = subprocess.Popen(["Xvfb", disp, "-screen", "0", "640x480x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    yield disp
    proc.terminate()


def test_key_state_and_capture(xdisplay):
    from jedai.perception.screen import Screen
    from jedai.platform_io import XKeyState, XTestInput

    inp, ks = XTestInput(xdisplay), XKeyState(xdisplay)
    inp.keyDown("f8")
    assert ks.is_pressed("f8")
    inp.keyUp("f8")
    assert not ks.is_pressed("f8")
    assert Screen(1, xdisplay).grab().shape == (480, 640, 3)


def test_controller_types_into_a_window(xdisplay, tmp_path):
    from Xlib import X, XK, display as xd

    from jedai.config import load_settings
    from jedai.control.channel import CommandChannel
    from jedai.control.controller import MasterController
    from jedai.control.keys import KeyClient
    from jedai.platform_io import XTestInput

    d = xd.Display(xdisplay)
    scr = d.screen()
    win = scr.root.create_window(0, 0, 640, 480, 0, scr.root_depth,
                                 event_mask=X.KeyPressMask | X.KeyReleaseMask)
    win.map()
    d.sync()
    time.sleep(0.2)
    win.set_input_focus(X.RevertToParent, X.CurrentTime)
    d.sync()
    got = []

    def listen():
        while True:
            e = d.next_event()
            if e.type == X.KeyPress:
                got.append(d.keycode_to_keysym(e.detail, 0))

    threading.Thread(target=listen, daemon=True).start()

    class Stopper:
        stop = False
        def is_pressed(self, k): return self.stop

    s = load_settings(local_file="none.yaml")
    path = tmp_path / "cmd.json"
    stopper = Stopper()
    mc = MasterController(CommandChannel(path), s.keys, "f8", XTestInput(xdisplay), stopper)
    t = threading.Thread(target=mc.run, daemon=True)
    t.start()
    kc = KeyClient(CommandChannel(path), ack_timeout=2)
    kc.key_down("FORWARD")
    kc.key_up("FORWARD")
    kc.dismount("command", "/dismount")
    time.sleep(0.4)
    stopper.stop = True          # shut the controller down so no thread outlives the test
    t.join(2)
    assert not t.is_alive()

    up, ret = XK.string_to_keysym("Up"), XK.string_to_keysym("Return")
    typed = "".join(chr(k) for k in got if 32 <= k < 127)
    assert got[0] == up                       # FORWARD -> up arrow
    assert got.count(ret) == 2                # Enter, text, Enter
    assert typed == "/dismount"
