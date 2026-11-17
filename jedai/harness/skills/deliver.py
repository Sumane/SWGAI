"""
mission_npc: talk to the NPC at a delivery mission's waypoint (Marc's recording, 16 Nov,
data/captures/2026-11-16_131000_mission_terminal):

  accept -> a waypoint named after the mission ("Important Policy Speech 206m")
  pickup NPC: talk -> chat "<NPC> says to <you>", the same waypoint jumps to the
              destination (0 m -> 10152 m)
  destination NPC: talk -> chat "Mission complete! Your reward of 1096 has been placed
              in your bank account." and the waypoint goes

Talking: the NPC's name isn't known in advance (16 Nov: "/ui action cycleTargetOutward"
targeted nothing). So: turn to face the waypoint (its panel arrow), OCR the name tags
floating over the people in view, "/tar <name>" each one and keep the one the target bar
puts at the waypoint's distance; then "/ui action defaultAction" (= double-click = converse).
If no tag reads, click just under each tag (on the body) and check the target bar the same
way. Whether the talk worked is read from chat and the waypoint panel, not assumed.
"""

from __future__ import annotations

import difflib
import math
import re
import time

from ..skill import Result
from .datapad import norm
from .find import FindNearest
from .interact import ui_action


def title_match(a: str | None, b: str | None) -> bool:
    """Waypoint names against the mission title, despite OCR."""
    a, b = norm(a or ""), norm(b or "")
    if not a or not b:
        return False
    return a == b or a[:10] == b[:10] or difflib.SequenceMatcher(None, a, b).ratio() >= 0.7


class MissionNpc(FindNearest):
    name = "mission_npc"

    def panel_dist(self, title: str, reads: int = 3):
        """Distance on the mission's waypoint row: int, None (row there, distance not read)
        or "gone" (no row with that title)."""
        seen, wps = False, []
        for i in range(reads):
            wps = self._hud_waypoints()
            for n, d in wps:
                if title_match(n, title) or (not self._ours(n) and sum(not self._ours(x) for x, _ in wps) == 1):   # the only row that isn't ours is the mission's (see _row)
                    seen = True
                    if d is not None:
                        return d
            self.sleep(0.5)
        self._evidence("mission_panel", [], wps, title)          # screenshot + what was read, for review
        return None if seen else "gone"

    _ARROW_DEG = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}

    def _row(self, title):
        """The mission's waypoint row from the panel (a hud Waypoint), or None."""
        try:
            rows = list(self.runner.hud.waypoints(self.runner.screen.grab(), self.runner.should_stop))
        except Exception as e:
            print(f"[mission] waypoint panel not read: {e}")
            return None
        self._last_rows = [(getattr(w, "name", None), getattr(w, "distance", None), getattr(w, "arrow", None)) for w in rows]
        for w in rows:
            if title_match(w.name, title):
                return w
        # Every run starts by deleting all waypoints, so a single row on the panel IS the
        # mission's, however its name reads this time (16 Nov: the close-in gave up 10 m out
        # because one read of the name didn't match).
        rows = [w for w in rows if not self._ours(w.name)]
        if len(rows) == 1:
            return rows[0]
        return None

    @staticmethod
    def _ours(name):
        """A waypoint the bot made itself (route_mission, bot_compass), however it reads ("Foute")."""
        n = norm(name or "")[:5]
        return n.startswith("bot") or difflib.SequenceMatcher(None, n, "route").ratio() >= 0.6

    def _face(self, title):
        """Turn until the waypoint is ahead (its arrow says N), at most twice."""
        for _ in range(2):
            w = self._row(title)
            deg = self._ARROW_DEG.get((getattr(w, "arrow", None) or "").upper())
            if deg is None or deg == 0:
                return
            self._turn(deg)

    _NOT_NAMES = {"target", "spatial", "combat", "planet", "quest", "auction", "system", "message",
                  "business", "access", "fees", "show", "help"}

    def _name_tags(self):
        """Text over the people in view: [(name, (x, y) of the tag's middle-bottom)], nearest the middle first."""
        frame = self.runner.screen.grab()
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = int(0.12 * w), int(0.08 * h), int(0.86 * w), int(0.72 * h)   # no HUD, no chat
        boxes = []
        try:
            from .datapad import ocr_boxes
            boxes = ocr_boxes(self._ocr(), frame[y0:y1, x0:x1], x0, y0)
        except Exception as e:
            print(f"[mission] name tags not read: {e}")
        tags = []
        for t, b in boxes:
            words = [wd for wd in re.findall(r"[a-z']{3,}", t.lower()) if wd not in self._NOT_NAMES]
            if words and (b[3] - b[1]) < 40 * h / 1440:
                tags.append((words[0], ((b[0] + b[2]) // 2, b[3])))
        cx = w // 2
        tags.sort(key=lambda nb: abs(nb[1][0] - cx))
        self._evidence("name_tags", [], [t for t, _ in tags], None)
        return tags

    def _pick(self, label, want, reach_m):
        """Read the target bar; the target if it's about where the waypoint is."""
        got = self._target(label)
        if not got:
            return None
        if got[1] <= reach_m or (isinstance(want, int) and abs(got[1] - want) <= 5):
            return got
        return None

    def run(self, state, title: str = "", reach_m: float = 8.0, tries: int = 6, **_) -> Result:
        if self._ocr() is None:
            return Result("failed", "no_ocr")
        self._step, self.anomalies = 0, []
        keys = self.runner.keys
        keys.release_all()
        d0 = self.panel_dist(title)
        mark = self.runner.chat_mark() if hasattr(self.runner, "chat_mark") else None
        data = {"title": title, "waypoint_before": d0, "targets": []}
        self._face(title)
        # Marc, 16 Nov: "walked around the NPC but never targeted him": it only looked the way it
        # happened to face, and he wasn't in view. Look all the way round, 60 degrees at a time,
        # reading the name tags in each view, until one of them is the mission's NPC.
        data["tags"], tried = [], set()
        for look in range(6):
            if look:
                self._turn(60.0)
                self.sleep(0.4)
            tags = self._name_tags()
            data["tags"] += [t for t, _ in tags]
            # by name first ("/tar <name>"), then by clicking the body under each tag
            attempts = [("tar", t, xy) for t, xy in tags[:tries]] + [("click", t, xy) for t, xy in tags[:tries]]
            k = self.runner.screen.grab().shape[0] / 1440
            for j, (how, name, (x, y)) in enumerate(attempts):
                i = f"{look}{j}"
                if how == "tar":
                    if name in tried:
                        continue
                    tried.add(name)
                    keys.chat(f"/tar {name}")
                else:
                    keys.click(x, y + int(70 * k))
                self.sleep(0.7)
                got = self._pick(f"npc{i}_{how}", d0, reach_m)
                data["targets"].append((how, name, got))
                if not got:
                    continue
                # Marc, 16 Nov: it had the right NPC targeted at 6 m and the talk did nothing: too far.
                # "/follow" walks us up to the target as close as the game allows; then talk. Give
                # it a few seconds, and a second go at the talk if the first came too early.
                keys.chat("/follow")
                data["followed_to"] = self._follow_in(f"npc{i}_follow", got[1])
                out = {"result": None}
                for _go in range(2):
                    ui_action(keys, "defaultAction")
                    self.sleep(2.5)
                    out = self._outcome(title, d0, mark)
                    if out["result"]:
                        break
                    self.sleep(2.0)
                keys.chat("/stopFollow")
                data.update(out, talked_to=got[0])
                if out["result"]:
                    print(f"[mission] {got[0]}: {out['result']}"
                          + (f", reward {out['reward']} cr" if out.get("reward") else "")
                          + (f", waypoint now {out['waypoint_after']} m" if isinstance(out.get('waypoint_after'), int) else ""))
                    return Result("ok", out["result"], data)
        self._look("npc_not_talked")
        self._anomaly("npc_not_talked", **data)
        return Result("failed", "npc_not_talked", {**data, "anomalies": self.anomalies})

    def _follow_in(self, label, start_m, wait_s=6.0):
        """After "/follow": wait until the target bar says we're beside it, or it stops closing."""
        last, t = start_m, 0.0
        while t < wait_s:
            self.sleep(1.5)
            t += 1.5
            got = self._target(f"{label}_{int(t)}")
            if not got:
                continue
            if got[1] <= 2 or (t >= 3.0 and got[1] >= last):
                return got[1]
            last = got[1]
        return last

    def _outcome(self, title, d0, mark):
        lines = [ln.lower() for ln in (self.runner.chat_since(mark) if mark is not None else [])]
        done = next((ln for ln in lines if "mission complete" in ln), None)
        said = next((ln for ln in lines if "says to" in ln), None)
        d1 = self.panel_dist(title, reads=2)
        out = {"chat": lines[-4:], "waypoint_after": d1, "result": None}
        if done:
            m = re.search(r"reward of\s*([\d,]+)", done)
            out.update(result="completed", reward=int(m.group(1).replace(",", "")) if m else None)
        elif isinstance(d0, int) and isinstance(d1, int) and abs(d1 - d0) > 50:
            out["result"] = "picked_up"            # the waypoint moved to the destination
        elif said and d1 == "gone":
            out["result"] = "completed"
        elif said:
            out["result"] = "picked_up"
        return out


class CloseIn(MissionNpc):
    """
    The last 10 metres or so to a waypoint, as its own skill (Marc, 16 Nov: the navigator's
    ending "was so broken it was painful to watch": it ran at the spot, overshot, bumped walls,
    marked cells and circled, on distances the panel misread - "11m" came out as "1m").

    No map, no planner, no running, and nothing that depends on the panel's digits:
      1. the waypoint's arrow on the panel says where the target is relative to the way we
         face (N = ahead): turn on the spot until it's ahead (turn, look, turn again);
      2. walk a short step (3 m; half the distance left when that's known for sure);
      3. look again. Arrow still ahead: carry on. Arrow now beside or behind us: we've just
         walked up to or past it, so it's within a step: there.
      4. a step that doesn't move us = something in the way. Go round it: back off, turn 90
         degrees, walk 4 m along it, face the target again and try; still blocked, the same
         way along again, until it's clear. Only a dead end turns it round to go the other way.
    The panel's distance only helps: it's believed when it has two digits or fits what we've
    walked, and then "within stop_m" also counts as there.
    """
    name = "close_in"
    _TURN = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": -135, "W": -90, "NW": -45}
    _AHEAD = ("N", "NE", "NW")

    def _turn_by(self, deg):
        """deg > 0 = to the right. Standing turn, timed; the caller looks again afterwards."""
        rate = float(self.settings.nav.get("standing_turn_deg_s", 42.0))
        self.runner.keys.tap("RIGHT" if deg * self._side > 0 else "LEFT", abs(deg) / rate)
        self.sleep(0.3)

    def _read(self, title):
        """(arrow, distance or None) for the waypoint's row, or None if the row isn't read."""
        for _ in range(3):
            w = self._row(title)
            if w is not None and (w.arrow or w.distance is not None):
                return (w.arrow or "").upper() or None, w.distance
            self.sleep(0.3)
        return None

    def _face_target(self, title):
        """Turn until the arrow is ahead. Returns the last (arrow, distance) read, or None."""
        got = None
        for n in range(6):
            got = self._read(title)
            if got is None or got[0] is None:
                return got
            deg = self._TURN.get(got[0], 0)
            if deg == 0:
                return got
            before = abs(deg)
            self._turn_by(deg * (0.8 if abs(deg) <= 45 else 1.0))      # a little short: 45-degree arrows
            after = self._read(title)
            if after and after[0] and abs(self._TURN.get(after[0], 0)) > before + 40:
                self._side = -self._side                               # LEFT/RIGHT are the other way round
        return got

    def _step_fwd(self, metres, pos):
        speed = float(self.settings.nav.get("walk_step_speed_m_s", 5.0))
        self.runner.keys.tap("FORWARD", metres / speed)
        self.sleep(0.4)
        new = self.runner.read_position()
        moved = math.hypot(new[0] - pos[0], new[1] - pos[1]) if pos and new else metres
        return (new or pos), moved

    def run(self, state, title: str = "", stop_m: float = 5.0, max_steps: int = 40, start_m: float | None = None, **_) -> Result:
        keys = self.runner.keys
        keys.release_all()
        self._step, self.anomalies, self._side = 0, [], 1
        believed, since = (float(start_m) if start_m else None), 0.0      # a distance we trust, metres walked since
        blocked, was_ahead, log = 0, False, []
        way = 1                                       # which way we go along an obstacle: +1 right, -1 left
        pos = self.runner.read_position()
        for step in range(max_steps):
            if self.runner.should_stop():
                return Result("failed", "stopped", {"log": log})
            first = self._read(title)
            for _again in range(4):
                if first is not None:
                    break
                # not read: look again, from a different angle each time (the panel's text is
                # read over whatever is behind it), before calling it lost
                self.sleep(1.0)
                self._turn_by(45)
                first = self._read(title)
            if first is None:
                self._look("row_lost")
                self._anomaly("close_in_row_lost", title=title, log=log[-6:], panel=getattr(self, "_last_rows", None))
                return Result("failed", "waypoint_row_lost", {"log": log, "anomalies": self.anomalies})
            # 3. we stepped at it and it's no longer ahead: we're on it
            if was_ahead and first[0] and first[0] not in self._AHEAD:
                print(f"[close] there: '{title}' is beside us (arrow {first[0]}) after {step} steps")
                self._face_target(title)              # don't finish with our back to it (Marc, 16 Nov)
                return Result("ok", "beside_it", {"steps": step, "pos": pos, "log": log})
            got = self._face_target(title) or first
            arrow, raw = got
            # the panel's figure: two digits, or fitting what we've walked, is believed
            if raw is not None:
                if believed is not None and abs(raw - (believed - since)) <= max(3.0, 0.3 * believed):
                    believed, since = float(raw), 0.0
                elif raw >= 10 and (believed is None or raw > believed - since):
                    believed, since = float(raw), 0.0
            est = None if believed is None else max(0.0, believed - since)
            log.append({"step": step, "arrow": arrow, "raw": raw, "est": est, "pos": pos, "blocked": blocked})
            if est is not None and est <= stop_m:
                print(f"[close] there: {est:.0f} m from '{title}' after {step} steps")
                return Result("ok", "", {"dist": est, "steps": step, "pos": pos, "log": log})
            want = 3.0 if est is None else min(6.0, max(1.5, 0.5 * est))
            pos, moved = self._step_fwd(want, pos)
            since += moved
            was_ahead = arrow in self._AHEAD and moved >= 0.8
            if moved >= 0.8:
                continue
            # 4. something in the way: go along it, the same way each time, until the way to the
            # target is clear (16 Nov: trying left, then right, then left again just shuffles on
            # the spot). Only if that way is shut too does it turn round and go along the other way.
            blocked += 1
            if blocked > 12:
                self._anomaly("close_in_blocked", title=title, log=log[-8:])
                return Result("failed", "blocked", {"log": log, "dist": est, "pos": pos, "anomalies": self.anomalies})
            keys.tap("BACK", 0.25)
            self._turn_by(90 * way)
            pos, m1 = self._step_fwd(4.0, pos)
            if m1 < 0.8:
                way = -way                            # shut that way: along the other
                self._turn_by(180)
                pos, m1 = self._step_fwd(4.0, pos)
            print(f"[close] blocked ({blocked}): went {m1:.0f} m along it to the {'right' if way > 0 else 'left'}")
        self._anomaly("close_in_too_many_steps", title=title, log=log[-8:])
        return Result("failed", "too_many_steps", {"log": log, "pos": pos, "anomalies": self.anomalies})
