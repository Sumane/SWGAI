"""
take_mission: take a delivery mission from a mission terminal
(Marc's recording, 16 Nov 2026, Theed).

  /tar mission + /ui action defaultAction (or a double-click) -> window "MISSION BROWSER", tabs [Destroy] [Deliver],
  columns Creator | Title | Payment | Start ("196m to the SW"),
  buttons [Refresh] [Accept Mission] [Mission Details] [Exit].
  Mission Details -> a window titled with the mission's name: Item, Start,
  Destination ("6835m to the E"), Creator, Reward, Difficulty, the story,
  [Close] [Accept].

What Marc said about choosing (16 Nov): payout goes up with distance; the real
destination is only known after reaching the pickup NPC; delivery NPCs are
always inside city limits. So "short" (the default here) = the lowest payout,
most likely to stay in this city; "rich" = the highest.

Only Deliver missions: Destroy missions are combat, which the bot stays out of.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from ..skill import Result, Skill
from .datapad import find_text, ocr_boxes
from .interact import use_target

ROOT = Path(__file__).resolve().parents[3]


def _centre(b):
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def parse_distance(text: str):
    """'196m to the SW' -> (196, 'sw'); None if it isn't one."""
    m = re.search(r"(\d+)\s*m\s*to\s*the\s*([nsew]{1,2})\b", text.lower().replace("|", "l"))
    return (int(m.group(1)), m.group(2)) if m else None


def parse_pay_start(text: str):
    """The payment and start columns, which the OCR often reads as one box (16 Nov):
    '1073| 1290 to tie sw' -> (1073, 129, 'sw'); '758 | 124m1 to te w' -> (758, 124, 'w');
    '1335 217m to tie €' -> (1335, 217, 'e'); '1158' -> (1158, None, None). Missing parts are None."""
    t = text.lower().replace("€", "e").replace("|", " ").replace(",", "").strip()
    pay = dist = d = None
    m = re.search(r"\bt[o0]\s*t\w*\s*([nsew]{1,2})?\s*$", t)          # "... to the sw"
    head = t[:m.start()].strip() if m else t
    if m:
        d = m.group(1)
        # the distance: "<n>m" with the m read as m, or as 0/o, plus maybe a stray character after it
        dm = re.search(r"(\d+)\s*m\S?$", head) or re.search(r"(\d+)[0o]\S?$", head)
        if dm:
            dist = int(dm.group(1))
            head = head[:dm.start()].strip()
    pm = re.fullmatch(r"(\d+)", head.strip(" ."))
    if pm:
        pay = int(pm.group(1))
    return pay, dist, d


# Layout of the browser at 2560x1440, as offsets from the top-left of its title text
# (frame 494 of the recording: title at (370, 707)). Used when a button or tab isn't read.
_BTN = {"refresh": (136, 513), "accept": (414, 513), "details": (686, 513), "exit": (968, 513)}
_TAB = {"destroy": (35, 33), "deliver": (90, 33)}
_COL_TITLE, _COL_PAY, _COL_START = 350, 688, 750      # creator | title | payment | start
_ROWS_TOP, _ROWS_BOTTOM, _WIDTH = 72, 495, 1120


def browser_window(boxes, frame_h: int = 1440):
    """The MISSION BROWSER window, or None:
    {"title": box, "k": scale, "buttons": {name: (x, y)}, "tabs": {name: (x, y)}, "read": [names really read]}."""
    title = find_text(boxes, "mission browser", "mission brow", "mlssion brow")
    if title is None:
        return None
    k = frame_h / 1440
    tx, ty = title[0], title[1]
    buttons = {n: (tx + int(dx * k), ty + int(dy * k)) for n, (dx, dy) in _BTN.items()}
    tabs = {n: (tx + int(dx * k), ty + int(dy * k)) for n, (dx, dy) in _TAB.items()}
    read = []
    for t, b in boxes:
        if not (ty <= b[1] <= ty + 560 * k and tx - 30 * k <= b[0] <= tx + _WIDTH * k):
            continue
        s = t.strip(" .:|")
        low_part = b[1] > ty + 440 * k
        name = None
        if low_part and "accept mission" in s:
            name, group = "accept", buttons
        elif low_part and "mission detai" in s:
            name, group = "details", buttons
        elif low_part and s == "refresh":
            name, group = "refresh", buttons
        elif low_part and s in ("exit", "exlt"):
            name, group = "exit", buttons
        elif not low_part and b[1] < ty + 60 * k and s in ("deliver", "dellver", "de1iver"):
            name, group = "deliver", tabs
        elif not low_part and b[1] < ty + 60 * k and s in ("destroy", "destrov"):
            name, group = "destroy", tabs
        if name:
            group[name] = _centre(b)
            read.append(name)
    return {"title": title, "k": k, "buttons": buttons, "tabs": tabs, "read": read}


def mission_rows(boxes, win):
    """Rows of the list: [{"creator", "title", "payment", "start_m", "start_dir", "click": (x, y)}], top to bottom."""
    k = win["k"]
    tx, ty = win["title"][0], win["title"][1]
    top, bottom = ty + _ROWS_TOP * k, ty + _ROWS_BOTTOM * k
    x_title, x_pay = tx + _COL_TITLE * k, tx + _COL_PAY * k
    lines = {}
    for t, b in boxes:
        cx, cy = _centre(b)
        if not (top < cy < bottom and tx - 10 * k < cx < tx + _WIDTH * k) or not t.strip():
            continue
        key = next((q for q in lines if abs(q - cy) <= 8 * k), cy)
        lines.setdefault(key, []).append((t, b))
    rows = []
    for cy in sorted(lines):
        row = {"creator": "", "title": "", "payment": None, "start_m": None, "start_dir": None}
        for t, b in sorted(lines[cy], key=lambda tb: tb[1][0]):
            if b[0] >= x_pay - 40 * k:
                # payment and/or start, often one box: "1073| 1290 to tie sw"
                pay, dist, d = parse_pay_start(t)
                if pay is not None and row["payment"] is None:
                    row["payment"] = pay
                if dist is not None:
                    row["start_m"], row["start_dir"] = dist, d
            elif b[0] < x_title - 10 * k:
                row["creator"] = (row["creator"] + " " + t).strip()
            elif b[0] < x_pay:
                row["title"] = (row["title"] + " " + t).strip()
        if row["title"] and row["payment"] is not None:
            row["click"] = (int(tx + (_COL_TITLE + 60) * k), int(cy))
            rows.append(row)
    return rows


def detail_window(boxes, frame_h: int = 1440):
    """The Mission Details window -> {"fields": {...}, "accept": (x, y), "close": (x, y), "anchor": box} or None.
    Labels run down the left ("Item:", "Start:", "Destination:", ...), each value on the line below."""
    k = frame_h / 1440
    # 16 Nov: the labels read as "destna", "creatora", "remane", "ttena": match on how they start
    def label(t):
        s = t.strip(" .:;,").lower()
        if len(s) > 13:
            return None
        for name, starts in (("destination", ("dest",)), ("start", ("start", "stan")), ("creator", ("creat",)),
                             ("reward", ("rew", "rema")), ("difficulty", ("diff",)), ("item", ("item", "ttem", "ltem"))):
            if s.startswith(starts):
                return name
        return None
    found = [(label(t), b) for t, b in boxes if label(t)]
    anchor = next((b for n, b in found if n in ("start", "difficulty", "destination")), None)
    if anchor is None:
        return None
    labels = {}
    for n, b in found:
        if abs(b[0] - anchor[0]) < 40 * k and n not in labels:
            labels[n] = b
    if len(labels) < 3:
        return None
    dest = labels.get("destination") or (anchor[0], labels["start"][1] + int(56 * k), anchor[2],
                                          labels["start"][3] + int(56 * k)) if "start" in labels else anchor
    fields = {}
    for name, lb in labels.items():
        best = None
        for t, b in boxes:
            if b == lb or not t.strip():
                continue
            dy = b[1] - lb[3]
            if -4 * k <= dy <= 34 * k and b[2] > lb[2] and b[0] < lb[0] + 420 * k:
                if best is None or dy < best[0]:
                    best = (dy, t.strip())
        if best:
            fields[name] = best[1]
    # buttons: 604 px under "Destination:" - 112 (it is the third label), read if possible
    out = {"fields": fields, "anchor": dest,
           "close": (dest[0] + int(160 * k), dest[1] + int(492 * k)),
           "accept": (dest[0] + int(1037 * k), dest[1] + int(492 * k))}
    # 17 Nov: the window opened taller than in the recording, the buttons were 643 px under
    # "Destination:" not 492, and the read "Accept" (y 1061) fell outside the band looked in, so
    # it clicked empty window. The buttons' own text wins, anywhere below the labels; the fixed
    # offset is only for when they aren't read.
    for t, b in boxes:
        s = t.strip(" ._-|").lower()
        if dest[1] + 300 * k < b[1] < dest[1] + 900 * k and dest[0] - 20 * k < b[0] < dest[0] + 1250 * k:
            if s == "accept":
                out["accept"] = _centre(b)
            elif s == "close":
                out["close"] = _centre(b)
    for key in ("start", "destination"):
        _, dist, d = parse_pay_start(fields.get(key, ""))      # "153m to the" (direction lost) still gives 153
        if dist is not None:
            out[key + "_m"], out[key + "_dir"] = dist, d
    m = re.search(r"(\d[\d,]*)", fields.get("reward", ""))
    if m:
        out["reward"] = int(m.group(1).replace(",", ""))
    return out


def choose(rows, prefer="short", max_payment=None, min_payment=0):
    ok = [r for r in rows if r["payment"] >= min_payment and (max_payment is None or r["payment"] <= max_payment)]
    if not ok:
        return None
    return min(ok, key=lambda r: r["payment"]) if prefer == "short" else max(ok, key=lambda r: r["payment"])


class TakeMission(Skill):
    name = "take_mission"

    def __init__(self, runner, settings, sleep=time.sleep, clock=time.time):
        self.runner, self.settings, self.sleep, self.clock = runner, settings, sleep, clock
        self._run, self._step, self.anomalies = None, 0, []

    # -- seeing / logging (same pattern as recover and travel) ------------------------
    def _ocr(self):
        return getattr(getattr(self.runner.hud, "waypoint_reader", None), "ocr", None)

    def _dir(self):
        if self._run is None:
            self._run = time.strftime("%Y%m%d_%H%M%S")
        return ROOT / "data" / "live" / "mission" / self._run

    def _look(self, label):
        import cv2
        frame = self.runner.screen.grab()
        boxes = ocr_boxes(self._ocr(), frame)
        d = self._dir()
        d.mkdir(parents=True, exist_ok=True)
        self._step += 1
        name = f"{self._step:02d}_{label}"
        cv2.imwrite(str(d / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        (d / f"{name}.json").write_text(json.dumps([[t, b] for t, b in boxes], indent=0))
        return frame, boxes

    def _zoom(self, frame, boxes, region, label):
        """Read a window again at 2x (its text is ~16 px high). region = (x0, y0, x1, y1).
        Boxes come back in full-frame pixels; what's outside the region is kept from the first read."""
        import cv2
        fh, fw = frame.shape[:2]
        x0, y0 = max(0, int(region[0])), max(0, int(region[1]))
        x1, y1 = min(fw, int(region[2])), min(fh, int(region[3]))
        big = cv2.resize(frame[y0:y1, x0:x1], None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        zoomed = [(t, (x0 + b[0] // 2, y0 + b[1] // 2, x0 + b[2] // 2, y0 + b[3] // 2))
                  for t, b in ocr_boxes(self._ocr(), big)]
        outside = [(t, b) for t, b in boxes if not (x0 <= (b[0] + b[2]) // 2 <= x1 and y0 <= (b[1] + b[3]) // 2 <= y1)]
        out = zoomed + outside
        (self._dir() / f"{self._step:02d}_{label}_zoom.json").write_text(json.dumps([[t, b] for t, b in out], indent=0))
        return out

    def _read_list(self, frame, boxes, win):
        k = win["k"]
        tx, ty = win["title"][0], win["title"][1]
        plain = mission_rows(boxes, win)
        zboxes = self._zoom(frame, boxes, (tx - 30 * k, ty - 20 * k, tx + 1150 * k, ty + 560 * k), "list")
        zwin = browser_window(zboxes, frame.shape[0])
        zrows = mission_rows(zboxes, zwin) if zwin else []
        if len(zrows) >= len(plain) and zrows:     # keep whichever read saw more of the list
            return zboxes, zwin, zrows
        return boxes, win, plain

    def _anomaly(self, reason, **info):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = ROOT / "data" / "live" / "anomalies"
        d = root / f"{stamp}_mission_{reason}"
        d.mkdir(parents=True, exist_ok=True)
        if self._dir().exists():
            for f in sorted(self._dir().glob("[0-9][0-9]_*")):
                shutil.copy2(f, d / f.name)
        rec = {"time": stamp, "skill": self.name, "reason": reason, "dir": str(d), **info}
        (d / "anomaly.json").write_text(json.dumps(rec, indent=1, default=str))
        with open(root / "index.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"[mission] anomaly '{reason}' logged: {d}")
        self.anomalies.append(reason)

    def _fail(self, reason, **data):
        self._anomaly(reason, **data)
        for _ in range(2):                        # leave nothing open
            self.runner.keys.press("ESC")
            self.sleep(0.4)
        return Result("failed", reason, {**data, "anomalies": self.anomalies})

    def _double_click(self, x, y):
        self.runner.keys.click(x, y)
        self.sleep(0.12)
        self.runner.keys.click(x, y)

    # -- opening the terminal ----------------------------------------------------------
    def _open_browser(self, h):
        """Get the MISSION BROWSER window up. Returns (frame, boxes, win) or None."""
        keys = self.runner.keys
        frame, boxes = self._look("start")
        win = browser_window(boxes, frame.shape[0])
        if win:
            return frame, boxes, win
        # 1) no mouse: /tar mission + /ui action defaultAction (the same as a double-click)
        name = h.get("mission_target_name", "mission")
        for _ in range(2 if name else 0):
            use_target(keys, name, sleep=self.sleep)
            for _ in range(3):
                self.sleep(1.0)
                frame, boxes = self._look("after_use")
                win = browser_window(boxes, frame.shape[0])
                if win:
                    return frame, boxes, win
        # 2) fallback: double-click under the terminal's name label
        print("[mission] /tar + defaultAction didn't open the browser: trying a double-click")
        for turn in range(6):
            label = find_text(boxes, "mission terminal", "mission termina", "mlssion terminal")
            if label is None:
                keys.tap("RIGHT", 0.5)
                self.sleep(0.6)
                frame, boxes = self._look("look_round")
                continue
            cx = (label[0] + label[2]) // 2
            sy = frame.shape[0] / 1440
            for drop in (110, 60, 170):           # how far under its label depends on how close we are
                self._double_click(cx, label[3] + int(drop * sy))
                self.sleep(1.5)
                frame, boxes = self._look("after_click")
                win = browser_window(boxes, frame.shape[0])
                if win:
                    return frame, boxes, win
        return None

    def _wait_list(self, tries=8):
        """The list after a tab click / refresh ('Downloading available missions...' goes away)."""
        for _ in range(tries):
            self.sleep(1.0)
            frame, boxes = self._look("list")
            win = browser_window(boxes, frame.shape[0])
            if find_text(boxes, "too far away") is not None:
                self._too_far = True               # the game's own words: the list will stay empty
                return frame, boxes, win, []
            if win and find_text(boxes, "downloading available") is None:
                boxes, win, rows = self._read_list(frame, boxes, win)
                if rows:
                    return frame, boxes, win, rows
        return frame, boxes, win, []

    # -- the skill -------------------------------------------------------------------------
    def run(self, state, dry_run: bool = False, prefer: str | None = None, **_) -> Result:
        h = self.settings.raw.get("harness", {})
        keys = self.runner.keys
        if self._ocr() is None:
            return Result("failed", "no_ocr")
        self._run, self._step, self.anomalies = None, 0, []
        self._too_far = False
        prefer = prefer or h.get("mission_prefer", "short")
        keys.release_all()
        opened = self._open_browser(h)
        if opened is None:
            return self._fail("no_mission_browser")
        frame, boxes, win = opened
        keys.click(*win["tabs"]["deliver"])
        frame, boxes, win, rows = self._wait_list()
        if not rows:
            return self._fail("terminal_too_far" if getattr(self, "_too_far", False) else "no_missions_read")
        pick = choose(rows, prefer, h.get("mission_max_payment"), int(h.get("mission_min_payment", 0)))
        listing = [{k: r[k] for k in ("creator", "title", "payment", "start_m", "start_dir")} for r in rows]
        for r in listing:
            mark = "->" if pick and r["title"] == pick["title"] and r["payment"] == pick["payment"] else "  "
            print(f"[mission] {mark} {r['payment']:>6} cr  {str(r['start_m']) + 'm ' + str(r['start_dir']):>10}  {r['title']}  ({r['creator']})")
        if pick is None:
            return self._fail("no_suitable_mission", missions=listing)
        # details: the destination is only shown there
        keys.click(*pick["click"])
        self.sleep(0.5)
        detail = None
        keys.click(*win["buttons"]["details"])
        for _ in range(4):
            self.sleep(1.0)
            frame, boxes = self._look("details")
            detail = detail_window(boxes, frame.shape[0])
            if detail:
                k, an = frame.shape[0] / 1440, detail["anchor"]
                again = detail_window(self._zoom(frame, boxes, (an[0] - 40 * k, an[1] - 200 * k, an[0] + 1260 * k,
                                                               an[1] + 540 * k), "details"), frame.shape[0])
                detail = again or detail
                break
        mission = {k: pick[k] for k in ("creator", "title", "payment", "start_m", "start_dir")}
        if detail:
            mission.update({k: detail[k] for k in ("start_m", "start_dir", "destination_m", "destination_dir", "reward")
                            if k in detail})
            mission["fields"] = detail["fields"]
            print(f"[mission] details: {detail['fields']}")
        pos = state.position()
        if pos:
            mission["taken_at"] = {"planet": pos[0], "x": pos[1], "z": pos[2]}
        if dry_run:
            for _ in range(2):
                keys.press("ESC")
                self.sleep(0.4)
            return Result("ok", "dry_run", {"missions": listing, "would_take": mission})
        # accept: in the details window if it opened, else from the list
        if detail and detail["accept"]:
            keys.click(*detail["accept"])
        else:
            if detail is None:                    # details never opened: the row is still selected
                keys.click(*pick["click"])
                self.sleep(0.4)
            keys.click(*win["buttons"]["accept"])
        self.sleep(1.0)
        # 16 Nov: "Are you sure you want to accept this mission?" [Cancel] [OK] - it wasn't
        # answered, so nothing was taken. Marc's recording: OK ~70 px right of the question's middle.
        frame, boxes = self._look("confirm")
        ask = find_text(boxes, "are you sure", "accept this mission", "want to accept")
        for again in range(2):
            if ask is not None:
                break
            # 17 Nov: no question = the click missed. Look for the button itself and try again
            # (the details window's "Accept", not the list's "Accept Mission").
            btn = [b for t, b in boxes if t.strip(" ._-|").lower() == "accept"]
            if btn:
                keys.click(*_centre(max(btn, key=lambda b: b[1])))
            elif detail and detail.get("accept"):
                keys.click(*detail["accept"])
            else:
                keys.click(*win["buttons"]["accept"])
            self.sleep(1.0)
            frame, boxes = self._look(f"confirm_again{again}")
            ask = find_text(boxes, "are you sure", "accept this mission", "want to accept")
        if ask is None:
            return self._fail("no_accept_confirm", mission=mission)
        from .datapad import confirm_ok_xy
        keys.click(*confirm_ok_xy(boxes, frame.shape, "are you sure", "accept this mission", "want to accept"))
        self.sleep(1.5)
        frame, boxes = self._look("after_accept")
        if find_text(boxes, "are you sure", "accept this mission") is not None:
            return self._fail("accept_confirm_still_open", mission=mission)
        if detail_window(boxes, frame.shape[0]) is not None:
            return self._fail("details_still_open_after_accept", mission=mission)
        # proof: the mission we took is no longer offered
        win2 = browser_window(boxes, frame.shape[0])
        mission["verified"] = None                # None = couldn't check (the browser closed)
        if win2:
            _, win2, left = self._read_list(frame, boxes, win2)
            same = [r for r in left if r["payment"] == pick["payment"] and r["title"] == pick["title"]]
            before = [r for r in rows if r["payment"] == pick["payment"] and r["title"] == pick["title"]]
            mission["verified"] = len(same) < len(before)
            keys.click(*win2["buttons"]["exit"])
            self.sleep(0.6)
            _, boxes = self._look("closed")
            if browser_window(boxes) is not None:
                keys.press("ESC")
                self.sleep(0.4)
            if mission["verified"] is False:
                # not known yet whether the list always drops an accepted mission: log it, carry on
                self._anomaly("mission_still_listed_after_accept", mission=mission)
        missions = state.get("missions", [])
        mission["t"] = time.time()
        missions.append(mission)
        state.set("missions", missions[-20:])
        print(f"[mission] accepted: {mission['title']} ({mission['payment']} cr)")
        return Result("ok", "", {"mission": mission, "missions": listing})
