"""
Drive to a waypoint. Start run_controller.py first.

    python scripts/run_navigator.py

It asks for a vehicle, scans your waypoints, and asks which one to go to.
Switch focus back to the game during the countdown. F8 stops it.
"""
import _bootstrap  # noqa: F401

from jedai.config import settings
from jedai.navigation.runner import NavigationRunner

if __name__ == "__main__":
    NavigationRunner(settings).navigate()
