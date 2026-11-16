"""
travel_to: take a shuttle or transport to another travel point on this planet.

Two commands do it, the same ones the game's own travel window and ticket
send (Core3: PurchaseTicketCommand, BoardShuttleCommand), so no window
clicking and nothing to misread:

  /purchaseTicket <planet> <From_Point> <planet> <To_Point> single
        within 8 m of a ticket terminal. A box "Ticket purchase complete"
        [OK] confirms it. "single" = one ticket (the default is a round trip).
  /boardShuttle
        within 25 m of the shuttle, on foot. With exactly one ticket for this
        point it goes at once; the shuttle must have landed (except at Theed
        Spaceport), so it is retried until the position jumps.

Where to stand comes from jedai/data/travel_ports.json (recorded), the travel
points themselves from world_points.json (wiki-level knowledge).
Seen 16 Nov 2026 in Marc's recording: Theed Shuttle B -> The Lake Retreat ->
Keren Starport -> Theed Spaceport -> The Lake Retreat.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from pathlib import Path

from ...data import load_travel_ports, load_world_points
from ...navigation.route import RouteFollower
from ..skill import Result, Skill
from .datapad import find_text, ocr_boxes

ROOT = Path(__file__).resolve().parents[3]


def cmd_name(name: str) -> str:
    """Travel point name as the command wants it: spaces become underscores."""
    return name.strip().replace(" ", "_")


def match_point(points: list[dict], name: str) -> dict | None:
    """The travel point called `name` (case and punctuation forgiving, unique prefix allowed)."""
    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum())
    n = norm(name)
    exact = [p for p in points if norm(p["name"]) == n]
    if exact:
        return exact[0]
    part = [p for p in points if n and n in norm(p["name"])]
    return part[0] if len(part) == 1 else None


def nearest_point(points: list[dict], x: float, z: float, within: float = 150.0) -> dict | None:
    best = min(points, key=lambda p: math.hypot(p["x"] - x, p["z"] - z), default=None)
    if best is None or math.hypot(best["x"] - x, best["z"] - z) > within:
        return None
    return best


class TravelTo(Skill):
    name = "travel_to"

    def __init__(self, runner, settings, sleep=time.sleep, clock=time.time, world_points=None, ports=None):
        self.runner, self.settings, self.sleep, self.clock = runner, settings, sleep, clock
        self.wp = world_points if world_points is not None else load_world_points()
        self.ports = ports if ports is not None else load_travel_ports()
        self._step, self.anomalies = 0, []

    # -- seeing / logging -------------------------------------------------------
    def _ocr(self):
        return getattr(getattr(self.runner.hud, "waypoint_reader", None), "ocr", None)

    @staticmethod
    def _dir():
        return ROOT / "data" / "live" / "travel"

    def _look(self, label):
        import cv2
        frame = self.runner.screen.grab()
        boxes = ocr_boxes(self._ocr(), frame) if self._ocr() is not None else []
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        self._step += 1
        name = f"{self._step:02d}_{label}"
        cv2.imwrite(str(d / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        (d / f"{name}.json").write_text(json.dumps([[t, b] for t, b in boxes], indent=0))
        return frame, boxes

    def _anomaly(self, reason, **info):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = ROOT / "data" / "live" / "anomalies"
        d = root / f"{stamp}_travel_{reason}"
        d.mkdir(parents=True, exist_ok=True)
        if self._dir().exists():
            for f in sorted(self._dir().glob("[0-9][0-9]_*")):
                shutil.copy2(f, d / f.name)
        rec = {"time": stamp, "skill": self.name, "reason": reason, "dir": str(d), **info}
        (d / "anomaly.json").write_text(json.dumps(rec, indent=1, default=str))
        with open(root / "index.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"[travel] anomaly '{reason}' logged: {d}")
        self.anomalies.append(reason)

    def _raw_pos(self):
        """Position straight from the HUD (the runner's filter would reject the jump of a journey)."""
        for _ in range(4):
            x, _, z = self.runner.hud.read_coords(self.runner.screen.grab())
            if x is not None and z is not None and not (x == z and abs(x) > 50):
                return float(x), float(z)
            self.sleep(0.4)
        return None

    # -- moving about the port ----------------------------------------------------
    def _walk(self, points, why):
        c = self.settings.nav
        gate = getattr(self.runner, "coord_gate", None)
        if gate is not None and hasattr(gate, "reset"):
            gate.reset()
        f = RouteFollower(self.runner.read_position, self.runner.keys,
                          turn_rate_deg_s=float(c.get("turn_rate_deg_s", 100.0)),
                          right_turn_sign=int(c.get("right_turn_sign", -1)),
                          reach_m=2.5, final_reach_m=3.0, timeout_s=120.0,
                          should_stop=self.runner.should_stop, sleep=self.sleep, clock=self.clock,
                          on_event=lambda kind, **kw: print(f"[travel] {kind} {kw}"))
        outcome, info = f.follow(points)
        self.runner.keys.release_all()
        print(f"[travel] walk to {why}: {outcome} at {info.get('pos')}")
        return outcome, info

    def _go_to(self, pos, spot, route, why, limit_m):
        """Stand within limit_m of `spot`: already there, a recorded walk, or a short straight line."""
        if spot is None:
            return pos, None
        d = math.hypot(spot[0] - pos[0], spot[1] - pos[1])
        if d <= limit_m:
            return pos, None
        if route:
            outcome, info = self._walk(list(route) + [spot], why)
        elif d <= 40:
            outcome, info = self._walk([spot], why)
        else:
            return pos, f"{why}_too_far"         # no recorded walk and too far to walk blind
        new = info.get("pos") or pos
        if outcome != "arrived" and math.hypot(spot[0] - new[0], spot[1] - new[1]) > limit_m:
            return new, f"{why}_{outcome}"
        return new, None

    def _on_foot(self, state):
        if not getattr(self.runner, "mounted", False):
            return
        c = self.settings.nav
        self.runner.keys.dismount(c.get("dismount_method", "command"), c.get("dismount_command", "/dismount"),
                                  c.get("dismount_key", ""))
        self.sleep(float(c.get("dismount_settle_secs", 2.0)))
        self.runner.store_vehicle()
        self.runner.apply_vehicle(c.get("foot_vehicle", "running"))
        state.set("mounted", False)

    # -- the two commands ------------------------------------------------------------
    def _purchase(self, planet, src, dst, tries=3):
        """Type /purchaseTicket until the 'Ticket purchase complete' box shows; click its OK."""
        cmd = f"/purchaseTicket {planet} {cmd_name(src)} {planet} {cmd_name(dst)} single"
        for n in range(tries):
            self.runner.keys.chat(cmd)
            for _ in range(4):
                self.sleep(1.0)
                frame, boxes = self._look("purchase")
                msg = find_text(boxes, "purchase complete", "ticket purchase")
                if msg is not None:
                    self.runner.keys.click(*self._ok_under(boxes, msg, frame.shape))
                    self.sleep(0.6)
                    return True, None
                bad = find_text(boxes, "too far from the terminal", "no location was found", "not enough money",
                                "departure point was not found", "invalid travel route", "inventory is full")
                if bad is not None:
                    text = next(t for t, b in boxes if b == bad)
                    return False, text
            print(f"[travel] no 'Ticket purchase complete' box after try {n + 1}")
        return False, "no_confirmation"

    @staticmethod
    def _ok_under(boxes, msg, shape):
        sx, sy = shape[1] / 2560, shape[0] / 1440
        best = None
        for t, b in boxes:
            if t.strip(" .") in ("ok", "0k", "dk"):
                dy, dx = b[1] - msg[3], (b[0] + b[2]) / 2 - msg[0]
                if 30 * sy < dy < 220 * sy and 0 < dx < 420 * sx and (best is None or dy < best[0]):
                    best = (dy, ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
        if best:
            return best[1]
        # 16 Nov screenshot: OK is 214 px right of and 94 px below the message's top-left corner
        return int(msg[0] + 214 * sx), int(msg[1] + 94 * sy)

    def _board(self, start, dest, wait_s):
        """Type /boardShuttle until the position jumps to the destination. Returns (pos, error)."""
        t0, n = self.clock(), 0
        while self.clock() - t0 < wait_s:
            n += 1
            self.runner.keys.chat("/boardShuttle")
            # loading screen, then the HUD comes back somewhere else
            t1 = self.clock()
            while self.clock() - t1 < 25:
                self.sleep(2.0)
                pos = self._raw_pos()
                if pos is not None and math.hypot(pos[0] - start[0], pos[1] - start[1]) > 300:
                    if math.hypot(pos[0] - dest["x"], pos[1] - dest["z"]) < 200:
                        return pos, None
                    return pos, "arrived_elsewhere"
                if pos is not None and self.clock() - t1 > 6:
                    break                       # still here: not boarded
            frame, boxes = self._look(f"board_try{n}")
            sel = find_text(boxes, "select destination")
            if sel is not None:
                # more than one ticket for this point: pick ours
                row = find_text(boxes, dest["name"].lower())
                if row is not None:
                    self.runner.keys.click((row[0] + row[2]) // 2, (row[1] + row[3]) // 2)
                    self.sleep(0.4)
                    self.runner.keys.click(*self._ok_under(boxes, row, frame.shape))
                    continue
            for bad in ("no ticket", "too far from the shuttle", "no shuttle nearby", "riding on a pet"):
                if find_text(boxes, bad) is not None:
                    return None, bad.replace(" ", "_")
            print(f"[travel] not boarded yet (try {n}): waiting for the shuttle")
            self.sleep(15.0)
        return None, "shuttle_wait_timeout"

    # -- the skill ------------------------------------------------------------------
    def run(self, state, to: str, dry_run: bool = False, **_) -> Result:
        h = self.settings.raw.get("harness", {})
        planet = self.settings.planet
        points = self.wp.get(planet, {}).get("travel", [])
        if self._dir().exists():
            for f in self._dir().glob("[0-9][0-9]_*"):
                f.unlink()
        self._step, self.anomalies = 0, []
        dest = match_point(points, to)
        if dest is None:
            return Result("failed", "unknown_destination", {"to": to, "known": [p["name"] for p in points]})
        pos = self._raw_pos()
        if pos is None:
            return Result("failed", "no_position")
        src = nearest_point(points, pos[0], pos[1])
        if src is None:
            return Result("failed", "not_at_a_travel_point", {"pos": pos})
        if src["name"] == dest["name"]:
            return Result("ok", "already_there", {"at": src["name"]})
        port = self.ports.get(planet, {}).get(src["name"], {})
        plan = {"from": src["name"], "to": dest["name"], "pos": pos, "port_known": bool(port),
                "command": f"/purchaseTicket {planet} {cmd_name(src['name'])} {planet} {cmd_name(dest['name'])} single"}
        print(f"[travel] {src['name']} -> {dest['name']} (standing at {pos}, port {'known' if port else 'NOT recorded'})")
        if dry_run:
            return Result("ok", "dry_run", plan)
        self._on_foot(state)
        self.runner.keys.release_all()

        # 1. to the ticket terminal (8 m)
        pos, err = self._go_to(pos, port.get("terminal"), port.get("to_terminal"), "terminal", 5.0)
        if err:
            self._look(err)
            self._anomaly(err, **plan)
            return Result("failed", err, {**plan, "anomalies": self.anomalies})
        # 2. buy
        ok, why = self._purchase(planet, src["name"], dest["name"])
        if not ok:
            self._anomaly("purchase_failed", why=why, **plan)
            return Result("failed", "purchase_failed", {**plan, "why": why, "anomalies": self.anomalies})
        # 3. to the shuttle (25 m)
        pos, err = self._go_to(pos, port.get("board"), port.get("to_board"), "board", 12.0)
        if err:
            self._look(err)
            self._anomaly(err, **plan)
            return Result("failed", err, {**plan, "ticket_bought": True, "anomalies": self.anomalies})
        # 4. board
        new, err = self._board(pos, dest, float(h.get("shuttle_wait_s", 420)))
        if err:
            self._anomaly("board_" + err, **plan)
            return Result("failed", "board_" + err, {**plan, "ticket_bought": True, "anomalies": self.anomalies})
        gate = getattr(self.runner, "coord_gate", None)
        if gate is not None and hasattr(gate, "reset"):
            gate.reset()
        state.set_position(planet, new[0], new[1])
        state.set("travel_point", dest["name"])
        print(f"[travel] arrived at {dest['name']} {new}")
        return Result("ok", "", {**plan, "arrived": new})
