"""
Start the master controller (the key executor). Run this FIRST, in its own
terminal, then run_navigator.py in a second one. F8 stops it.

    python scripts/run_controller.py
"""
import _bootstrap  # noqa: F401

from jedai.config import settings
from jedai.control.channel import CommandChannel
from jedai.control.controller import MasterController

if __name__ == "__main__":
    MasterController(CommandChannel(settings.path("command_file")),
                     settings.keys, settings.kill_key).run()
