"""
Find the correct navigation.right_turn_sign for your game (10 seconds).

    1. Start the controller:   python scripts/run_controller.py
    2. Stand somewhere open (on foot or on a vehicle), game focused.
    3. Run:                    python scripts/check_turn_direction.py

It drives forward to measure the heading from your coordinates, holds RIGHT
while still moving, measures again, and works out which way RIGHT rotates the
X/Z heading. Repeats 3 times and prints the value to put in
config/settings.local.yaml.
"""
import _bootstrap  # noqa: F401

import math
import time

from jedai.config import settings
from jedai.control.channel import CommandChannel
from jedai.control.keys import KeyClient
from jedai.perception.hud import CoordinateReader
from jedai.perception.screen import Screen


def heading(coords, screen, keys, seconds=1.5):
    """Drive forward for `seconds` and return the unit heading from coordinates."""
    x1, _, z1 = coords.read(screen.grab())
    time.sleep(seconds)
    x2, _, z2 = coords.read(screen.grab())
    if None in (x1, z1, x2, z2):
        return None
    d = math.hypot(x2 - x1, z2 - z1)
    return ((x2 - x1) / d, (z2 - z1) / d) if d > 1.0 else None


def main():
    coords = CoordinateReader.from_settings(settings)
    screen = Screen.from_settings(settings)
    keys = KeyClient(CommandChannel(settings.path("command_file")))
    print("Switch to the game... starting in 4s")
    time.sleep(4)

    votes = []
    for trial in range(3):
        keys.key_down("FORWARD")
        h1 = heading(coords, screen, keys)
        keys.key_down("RIGHT")
        time.sleep(0.4)
        keys.key_up("RIGHT")
        h2 = heading(coords, screen, keys)
        keys.key_up("FORWARD")
        if h1 is None or h2 is None:
            print(f"  trial {trial + 1}: couldn't read coordinates / not moving")
            continue
        cross = h1[0] * h2[1] - h1[1] * h2[0]
        turned = math.degrees(math.asin(max(-1.0, min(1.0, cross))))
        votes.append(1 if cross > 0 else -1)
        print(f"  trial {trial + 1}: heading {h1[0]:+.2f},{h1[1]:+.2f} -> {h2[0]:+.2f},{h2[1]:+.2f} "
              f"({turned:+.0f}°)")
        time.sleep(0.5)
    keys.release_all()

    if not votes:
        print("\nNo usable trials. Is the controller running and are coordinates visible?")
        return
    sign = 1 if sum(votes) > 0 else -1
    agree = votes.count(sign)
    print(f"\nResult: right_turn_sign = {sign}   ({agree}/{len(votes)} trials agree)")
    print("Put this in config/settings.local.yaml:\n\nnavigation:\n"
          f"  right_turn_sign: {sign}\n")
    if sign != settings.nav.get("right_turn_sign", 1):
        print("⚠️  This differs from your current setting — D* steering has been turning the wrong way.")


if __name__ == "__main__":
    main()
