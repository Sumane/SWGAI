import json, time, pydirectinput, keyboard, os

# =============================================================================
# MASTER CONTROLLER - Dumb key executor. Reads nav_state.json and fires keys.
# Run this FIRST, then run navigation_v0_01.py in a second terminal.
# Kill key: F8 (shared with nav script)
# =============================================================================

KEYS = {
    "LEFT":    "left",
    "RIGHT":   "right",
    "AUTORUN": "numlock",
    "FORWARD": "up",
    "BACK":    "down",
}

NAV_STATE_FILE = "nav_state.json"
KILL_KEY       = "f8"

class MasterController:
    def __init__(self):
        self.autorun_on = False
        # Ensure a clean state file on startup
        self._write_state({"cmd": "IDLE"})
        print("=== Master Controller Online ===")
        print(f"Watching: {os.path.abspath(NAV_STATE_FILE)}")
        print(f"Press {KILL_KEY.upper()} to stop.\n")

    def _write_state(self, data):
        try:
            with open(NAV_STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[MC] Write error: {e}")

    def _read_state(self):
        try:
            with open(NAV_STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return None

    def _key(self, logical_key):
        """Resolve logical key name to physical key string."""
        return KEYS.get(logical_key, logical_key.lower())

    def run(self):
        while not keyboard.is_pressed(KILL_KEY):
            if not os.path.exists(NAV_STATE_FILE):
                time.sleep(0.01)
                continue

            instruction = self._read_state()
            if not instruction:
                time.sleep(0.01)
                continue

            cmd = instruction.get("cmd")

            if cmd == "IDLE":
                time.sleep(0.01)
                continue

            elif cmd == "PRESS":
                # Single key press (e.g. toggle autorun)
                logical = instruction.get("key")
                physical = self._key(logical)
                if physical:
                    pydirectinput.press(physical)
                    if logical == "AUTORUN":
                        self.autorun_on = instruction.get("state", not self.autorun_on)

            elif cmd == "KEY_DOWN":
                # Hold a key down
                physical = self._key(instruction.get("key", ""))
                if physical:
                    pydirectinput.keyDown(physical)

            elif cmd == "KEY_UP":
                # Release a held key
                physical = self._key(instruction.get("key", ""))
                if physical:
                    pydirectinput.keyUp(physical)

            elif cmd == "TAP":
                # Press and hold for duration, then release
                physical = self._key(instruction.get("key", ""))
                duration = instruction.get("duration", 0.0)
                if physical:
                    pydirectinput.keyDown(physical)
                    time.sleep(duration)
                    pydirectinput.keyUp(physical)

            elif cmd == "TAP_MULTI":
                # Hold multiple keys simultaneously for duration (e.g. back+left)
                keys_logical = instruction.get("keys", [])
                duration = instruction.get("duration", 0.0)
                physicals = [self._key(k) for k in keys_logical if self._key(k)]
                for p in physicals:
                    pydirectinput.keyDown(p)
                time.sleep(duration)
                for p in reversed(physicals):
                    pydirectinput.keyUp(p)

            elif cmd == "RELEASE_ALL":
                # Emergency release - let go of everything
                for physical in KEYS.values():
                    pydirectinput.keyUp(physical)
                if self.autorun_on:
                    pydirectinput.press(KEYS["AUTORUN"])
                    self.autorun_on = False

            elif cmd == "STOP_AUTORUN":
                # Stop autorun if it's on
                if self.autorun_on:
                    pydirectinput.press(KEYS["AUTORUN"])
                    self.autorun_on = False

            # Acknowledge by returning to IDLE
            self._write_state({"cmd": "IDLE"})
            time.sleep(0.005)

        print("\n[MC] Kill key detected. Releasing all keys and exiting.")
        for physical in KEYS.values():
            pydirectinput.keyUp(physical)

if __name__ == "__main__":
    MasterController().run()

# run: python "E:\SWGEMU_bot\jedai\Scripts\movement\master_controller.py"
