"""
Which way is the character facing, without moving? (16 Nov: twice stuck in a Theed clone
tube, because the walk worked its facing out from 1 m shuffles and timed turns.)

Put a waypoint 3 km due north. The waypoint panel shows an arrow for it relative to where
the character faces (N = straight ahead, E = to the right...), so the arrow read backwards
is a compass, good to 45 degrees, read from the screen like everything else. 3 km away, so
walking about doesn't change its bearing.

The "/waypoint" syntax hasn't been seen working in-game: each form is tried and only one
that puts a row about 3000 m away on the panel is accepted. None accepted -> no compass,
and the walk falls back to working its facing out from movement.
"""

from __future__ import annotations

import difflib
import re

ARROW_DEG = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
FORMS = ("/waypoint {x} {z} {name}", "/waypoint {planet} {x} 0 {z} {name}", "/waypoint {planet} {x} {z} 0 {name}")


class Compass:
    NAME, DIST = "bot_compass", 3000

    def __init__(self, runner, planet: str, sleep):
        self.runner, self.planet, self.sleep = runner, planet, sleep
        self.ok, self.form = False, None

    def _row(self):
        try:
            rows = list(self.runner.hud.waypoints(self.runner.screen.grab(), self.runner.should_stop))
        except Exception as e:
            print(f"[compass] waypoint panel not read: {e}")
            return None
        want = re.sub(r"[^a-z]", "", self.NAME)
        for w in rows:
            name = re.sub(r"[^a-z]", "", (w.name or "").lower())
            if w.distance is not None and abs(w.distance - self.DIST) <= 0.1 * self.DIST:
                return w
            if name and difflib.SequenceMatcher(None, name, want).ratio() >= 0.7 and w.distance is None:
                return w
        return None

    def set(self, pos) -> bool:
        """Make the waypoint 3 km north of pos. True once the panel shows it there."""
        if self._row() is not None:            # still there from an earlier run
            self.ok = True
            return True
        for form in FORMS:
            self.runner.keys.chat(form.format(x=int(pos[0]), z=int(pos[1] + self.DIST), name=self.NAME, planet=self.planet))
            self.sleep(1.5)
            row = self._row()
            if row is not None:
                self.ok, self.form = True, form
                print(f"[compass] set with '{form}': panel shows {row.name!r} {row.distance} m, arrow {row.arrow}")
                return True
        print("[compass] no /waypoint form put a waypoint 3 km north: no compass")
        return False

    def facing(self):
        """Compass bearing the character faces (degrees clockwise from north), or None."""
        if not self.ok:
            return None
        for _ in range(2):
            row = self._row()
            deg = ARROW_DEG.get((getattr(row, "arrow", None) or "").upper()) if row is not None else None
            if deg is not None:
                return (360 - deg) % 360
            self.sleep(0.3)
        return None
