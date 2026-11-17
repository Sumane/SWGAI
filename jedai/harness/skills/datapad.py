"""
clean_waypoints: delete the bot's own waypoints through the datapad, the way a
player does (Marc's walkthrough, 7 Nov):

  Ctrl+D -> datapad opens on the Waypoints tab: a grid of planet icons with
  the waypoint's name underneath -> right-click an icon -> radial menu
  "1) Activate  2) Set Name  3) Examine  4) Destroy" -> click Destroy ->
  screen darkens, box "Really destroy <name>?" [No] [Yes] -> click Yes.

Everything is found by OCR on the current screen (the window can be moved),
and the confirm box's name is checked before clicking Yes. dry_run only
reports what it would delete and saves an annotated screenshot.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..skill import Result, Skill


OCR_MAX_SIDE = 1800   # cap for *enlarged crops* only: 2460^2 at 3x OOM'd the 3060 (16 Nov).
                      # Full frames stay 2560 wide: shrinking them made 16 px UI text unreadable.


def ocr_boxes(ocr, img, x0=0, y0=0):
    """[(text_lower, (x1, y1, x2, y2))] in full-frame pixels. On CUDA OOM retries once at half size."""
    import cv2
    k = 1.0
    for attempt in range(2):
        src = img if k == 1.0 else cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        try:
            res = ocr.readtext(src, detail=1, paragraph=False)
            break
        except Exception as e:  # torch.OutOfMemoryError
            if "out of memory" not in str(e).lower() or attempt:
                raise
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
            print(f"[ocr] CUDA OOM at {src.shape[1]}x{src.shape[0]}, retrying at half size")
            k *= 0.5
    out = []
    for box, text, _conf in res:
        xs = [p[0] / k for p in box]
        ys = [p[1] / k for p in box]
        out.append((text.strip().lower(), (int(min(xs)) + x0, int(min(ys)) + y0,
                                           int(max(xs)) + x0, int(max(ys)) + y0)))
    return out


def norm(s: str) -> str:
    """Compare names despite OCR: lower-case, '_' and '-' and spaces alike."""
    return re.sub(r"[\s_\-.]+", "_", (s or "").lower()).strip("_")      # a panel row can have no name read


def find_text(boxes, *words):
    """First box whose text contains any of the words."""
    for text, b in boxes:
        if any(w in text for w in words):
            return b
    return None


def datapad_region(boxes, frame_shape):
    """The datapad window from its 'datapad' title: (x0, y0, x1, y1)."""
    t = find_text(boxes, "datapad")
    if t is None:
        return None
    h, w = frame_shape[:2]
    # the window is ~520x390 at 1280x720 -> scale with the frame
    sx, sy = w / 1280, h / 720
    return t[0] - int(5 * sx), t[1], min(w, t[0] + int(530 * sx)), min(h, t[1] + int(390 * sy))


def _letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def reads_like(label: str, name: str, thr: float = 0.8) -> bool:
    """A grid label (small text, often one line of two, badly read: "deeta", "shuttlepott")
    against a waypoint's name ("Dee'ja Peak Shuttleport"): like its first word, or its start."""
    import difflib
    a = _letters(label)
    if len(a) < 5:              # "deen" is too little to tell "Dee'ja" from another waypoint
        return False
    words = [_letters(w) for w in re.split(r"[\s_]+", name or "")]
    # the name's first word or its start only: a later word ("Shuttleport") is shared by other waypoints
    cands = [w for w in words[:1] if len(w) >= 4] + [_letters(name)[:len(a)], _letters(name)[:len(a) + 1]]
    return any(difflib.SequenceMatcher(None, a, c).ratio() >= thr for c in cands if c)


def confirm_ok_xy(boxes, frame_shape, *words):
    """Where to click OK on an "Are you sure ...?" [Cancel] [OK] box, or None if the box isn't up.
    16 Nov: the question was read as two boxes ("are you sure" | "want to accept this mission?");
    measuring from the first half put the click on Cancel. So: the whole line of the question,
    and Cancel as a second landmark (OK's middle is 156 px right of Cancel's at 2560 wide)."""
    ask = find_text(boxes, *words)
    if ask is None:
        return None
    sx, sy = frame_shape[1] / 2560, frame_shape[0] / 1440
    line = [b for t, b in boxes if abs(b[1] - ask[1]) <= 8 * sy and abs(b[0] - ask[0]) <= 420 * sx and len(t) >= 3]
    qx0, qx1 = min(b[0] for b in line), max(b[2] for b in line)
    qcx = (qx0 + qx1) // 2
    below = lambda b: 0 < b[1] - ask[1] < 60 * sy
    ok = next((b for t, b in boxes if t.strip(" .[]|") in ("ok", "0k") and below(b) and b[0] > qcx - 20 * sx), None)
    if ok is not None:
        return (ok[0] + ok[2]) // 2, (ok[1] + ok[3]) // 2
    cancel = next((b for t, b in boxes if t.strip(" .[]|").startswith("cancel") and below(b)), None)
    if cancel is not None:
        return (cancel[0] + cancel[2]) // 2 + int(156 * sx), (cancel[1] + cancel[3]) // 2
    return qcx + int(71 * sx), ask[1] + int(24 * sy)


def waypoint_labels(boxes, region, prefixes, keep=(), names=()):
    """
    Labels under waypoint icons that start with one of `prefixes` (the bot's
    own waypoints) and aren't in `keep`. Returns [(label, icon_xy)].
    The icon sits right above its label.
    """
    x0, y0, x1, y1 = region
    # 7 Nov: the details pane on the left repeats the selected waypoint's
    # name, and the hover tooltip does too; both were picked as "icons".
    # Only the grid (right of the pane, ~22% of the window) has icons, and a
    # name with "Waypoint" right under it is a tooltip or the pane.
    gx0 = x0 + int(0.225 * (x1 - x0))
    out = []
    pre = [norm(p) for p in prefixes]
    keep_n = {norm(k) for k in keep}
    for text, (bx1, by1, bx2, by2) in boxes:
        if not (gx0 <= bx1 and bx2 <= x1 and y0 <= by1 and by2 <= y1):
            continue
        hh = by2 - by1
        if any(t.strip(" .") == "waypoint" and 0 <= b[1] - by2 <= 1.5 * hh + 2 and abs(b[0] - bx1) < 6 * hh
               for t, b in boxes):
            continue
        n = norm(text)
        if not any(n.startswith(p) for p in pre) and not any(reads_like(text, nm) for nm in names):
            continue
        if any(n.startswith(k) or k.startswith(n) for k in keep_n):
            continue
        h = by2 - by1
        out.append((text, ((bx1 + bx2) // 2, by1 - int(1.6 * h))))
    return out


class CleanWaypoints(Skill):
    name = "clean_waypoints"

    def __init__(self, runner, settings, sleep=time.sleep):
        self.runner, self.settings, self.sleep = runner, settings, sleep

    def _ocr(self):
        return getattr(getattr(self.runner.hud, "waypoint_reader", None), "ocr", None)

    _step = 0
    esc_presses = 3

    @staticmethod
    def _dir():
        return Path(__file__).resolve().parents[3] / "data" / "live" / "datapad"

    def _escape(self):
        """Out of whatever is open: ESC a few times (Marc, 7 Nov), checking
        between presses so we don't go past the datapad into the game menu."""
        for _ in range(self.esc_presses):
            _, boxes = self._look("escape")
            if find_text(boxes, "really destroy", "datapad", "activate", "destroy") is None:
                return True
            self.runner.keys.press("ESC")
            self.sleep(0.5)
        _, boxes = self._look("escape")
        return find_text(boxes, "really destroy", "datapad") is None

    def _anomaly(self, reason: str, **info):
        """
        Something didn't go as the walkthrough says. Keep the evidence for a
        reviewer (Qwen or us): this run's step screenshots + OCR and a note,
        in data/live/anomalies/<time>_clean_waypoints_<reason>/, and one line
        in data/live/anomalies/index.jsonl.
        """
        import json
        import shutil
        stamp = time.strftime("%Y%m%d_%H%M%S")
        root = self._dir().parent / "anomalies"
        d = root / f"{stamp}_clean_waypoints_{reason}"
        d.mkdir(parents=True, exist_ok=True)
        for f in sorted(self._dir().glob("[0-9][0-9]_*")):
            shutil.copy2(f, d / f.name)
        rec = {"time": stamp, "skill": self.name, "reason": reason, "dir": str(d), **info}
        (d / "anomaly.json").write_text(json.dumps(rec, indent=1, default=str))
        with open(root / "index.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"[datapad] anomaly '{reason}' logged: {d}")
        self.anomalies.append(reason)

    def _look(self, label="look"):
        """Screenshot + OCR; both saved to data/live/datapad/ for review."""
        import json
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

    def _waypoints_tab(self, frame, boxes, region):
        """16 Nov: the datapad can open on its Data tab (vehicles). Click Waypoints, look again."""
        x0, y0, x1, y1 = region
        gx0 = x0 + int(0.225 * (x1 - x0))
        k = frame.shape[0] / 1440
        tab = next((b for t, b in boxes if t.startswith("waypoint") and b[1] < y0 + 70 * k and b[0] >= gx0 - 20 * k), None)
        self.runner.keys.click(*(((tab[0] + tab[2]) // 2, (tab[1] + tab[3]) // 2) if tab
                                 else (x0 + int(271 * k), y0 + int(38 * k))))
        self.sleep(0.8)
        return self._look("waypoints_tab")

    def _zoom_ocr(self, frame, cx, cy, half=260, scale=2.0, rect=None):
        """OCR a crop round (cx, cy) (or rect=(x0, y0, x1, y1)), enlarged; boxes in full-frame pixels."""
        import cv2
        h, w = frame.shape[:2]
        if rect is not None:
            x0, y0 = max(0, rect[0]), max(0, rect[1])
            x1, y1 = min(w, rect[2]), min(h, rect[3])
        else:
            x0, y0 = max(0, cx - half), max(0, cy - half)
            x1, y1 = min(w, cx + half), min(h, cy + half)
        scale = min(scale, OCR_MAX_SIDE / max(1, x1 - x0, y1 - y0))
        crop = cv2.resize(frame[y0:y1, x0:x1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        out = []
        for text, (a, b, c, d) in ocr_boxes(self._ocr(), crop):
            out.append((text, (x0 + int(a / scale), y0 + int(b / scale), x0 + int(c / scale), y0 + int(d / scale))))
        return out

    def _dialog_buttons(self, frame, boxes, ask):
        """
        (yes_xy, no_xy) of the confirm box whose question is `ask`.
        Only buttons just under the question count: 7 Nov the first 'yes' on
        screen was the datapad's 'Active: Yes' detail, top-left, so the click
        missed the box and the loop retried forever. Falls back to the
        buttons' place relative to the question (Yes right, No left, ~22 px
        below at 2560x1440).
        """
        h, w = frame.shape[:2]
        sx, sy = w / 2560, h / 1440
        qcx, qbot = (ask[0] + ask[2]) // 2, ask[3]

        def near(b):
            cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
            return 0 < cy - qbot < 60 * sy and abs(cx - qcx) < 200 * sx

        cands = boxes + self._zoom_ocr(frame, qcx, qbot + int(20 * sy), half=int(220 * sx))
        def pick(word, default):
            for t, b in cands:
                if t.strip(" .") == word and near(b):
                    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
            return default
        y = qbot + int(15 * sy)
        return (pick("yes", (qcx + int(60 * sx), y)), pick("no", (qcx - int(41 * sx), y)))

    def run(self, state, keep=(), dry_run: bool = False, max_delete: int = 30, extra=(), **_) -> Result:
        h = self.settings.raw.get("harness", {})
        prefixes = list(h.get("bot_waypoint_prefixes", ["badge_", "route_", "bot_"]))
        # 16 Nov (Marc): the HUD panel must be empty at the start of a run. `extra` = names showing
        # on the panel (active waypoints, whoever made them): those go too. Waypoints that are only
        # in the datapad, switched off, are left alone.
        names = [nm for nm in (extra or ()) if len(_letters(nm)) >= 4]
        keys = self.runner.keys
        if self._ocr() is None:
            return Result("failed", "no_ocr")
        # this run's step files only (an anomaly copies them)
        out_dir = self._dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in out_dir.glob("[0-9][0-9]_*"):
            f.unlink()
        self._step = 0
        self.anomalies = []
        keys.combo(*h.get("datapad_keys", ["CTRL", "d"]))
        self.sleep(1.5)
        deleted, skipped, tried = [], [], []
        try:
            for i in range(max_delete):
                frame, boxes = self._look("grid")
                region = datapad_region(boxes, frame.shape)
                if region is None:
                    self._anomaly("datapad_not_found", deleted=deleted)
                    return Result("failed", "datapad_not_found", {"deleted": deleted})
                if i == 0:
                    frame, boxes = self._waypoints_tab(frame, boxes, region)
                labels = [lb for lb in waypoint_labels(boxes, region, prefixes, keep, names)
                          if norm(lb[0]) not in skipped]
                if dry_run:
                    self._annotate(frame, region, labels, out_dir / "dry_run.jpg")
                    return Result("ok", "dry_run", {"would_delete": [lb[0] for lb in labels],
                                                    "picture": str(out_dir / "dry_run.jpg")})
                if not labels:
                    break
                label, (ix, iy) = labels[0]
                # how many icons carry this name now (16 Nov: two "route_theed_shuttle_b",
                # both read as "route": deleting one was reported as a failure)
                n_before = sum(1 for t, _ in labels if norm(t) == norm(label))
                if norm(label) in tried:
                    # it said deleted but here it is again: don't go round
                    self._anomaly("label_came_back", label=label)
                    self._escape()
                    skipped.append(norm(label))
                    break
                tried.append(norm(label))
                keys.click(ix, iy, "right")
                self.sleep(0.8)
                frame, boxes = self._look("radial")
                # The radial labels are small and sit next to icons: read a
                # 2x-enlarged crop round the click. 7 Nov: the full-frame OCR
                # read 1-3 but missed '4) Destroy' next to its tick icon.
                # 16 Nov: the box it found for "Destroy" was up by the icon's own
                # label, so the click missed and no confirm box came. Only accept
                # a "destroy" that sits where option 4 does (below-right of the
                # icon); otherwise click option 4's usual spot, which has matched
                # the screenshots every time (+77..88, +56..61 px at 2560x1440).
                sx, sy = frame.shape[1] / 2560, frame.shape[0] / 1440
                zoom = self._zoom_ocr(frame, ix, iy)
                import json as _json
                (self._dir() / f"{self._step:02d}_radial_zoom.json").write_text(_json.dumps([[t, b] for t, b in zoom]))
                destroy = None
                for t, b in zoom:
                    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    if ("destr" in t or "estroy" in t) and 25 * sx < cx - ix < 170 * sx and 20 * sy < cy - iy < 120 * sy:
                        destroy = b
                        break
                if destroy is not None:
                    keys.click((destroy[0] + destroy[2]) // 2, (destroy[1] + destroy[3]) // 2)
                else:
                    print(f"[datapad] 'Destroy' not read for {label}: clicking option 4's spot")
                    keys.click(ix + int(82 * sx), iy + int(58 * sy))
                # the box can take a moment (7 Nov: not there after 0.8 s)
                for _wait in range(4):
                    self.sleep(0.8)
                    frame, boxes = self._look("confirm")
                    if find_text(boxes, "really destroy") is not None:
                        break
                ask = find_text(boxes, "really destroy")
                asked = next((t for t, b in boxes if "really destroy" in t), "")
                # 16 Nov: "Really destroy bot_compass?" was read as two boxes, "really destroy bot_" and
                # the rest, so the check failed and the waypoint stayed. Either starting like the other will do.
                said, want = norm(asked.replace("really destroy", "")), norm(label)
                import difflib
                named = any(difflib.SequenceMatcher(None, _letters(said), _letters(nm)).ratio() >= 0.7 and reads_like(label, nm)
                            for nm in names)       # the box names a waypoint from the panel, and that's the one we clicked
                if ask is None or len(said) < 3 or not (named or said.startswith(want[:8]) or want.startswith(said[:8])):
                    print(f"[datapad] confirm box didn't check out for {label}: asked={asked!r}")
                    # not the box we expected, or it names another waypoint: back out
                    self._anomaly("confirm_mismatch", label=label, asked=asked)
                    self._escape()
                    skipped.append(norm(label))
                    break
                yes, no = self._dialog_buttons(frame, boxes, ask)
                keys.click(*yes)
                self.sleep(1.0)
                # did it work? the box must be gone and the label with it
                frame, boxes = self._look("after_yes")
                if find_text(boxes, "really destroy") is not None:
                    print(f"[datapad] confirm box still open after Yes for {label}: backing out")
                    self._anomaly("confirm_still_open", label=label, yes=yes)
                    keys.click(*no)
                    self.sleep(0.5)
                    self._escape()
                    skipped.append(norm(label))
                    break
                region = datapad_region(boxes, frame.shape)
                n_after = 0 if region is None else sum(
                    1 for t, _ in waypoint_labels(boxes, region, prefixes, keep, names) if norm(t) == norm(label))
                if n_after >= n_before:
                    print(f"[datapad] {label} still listed after Yes: skipping it")
                    self._anomaly("still_listed", label=label)
                    skipped.append(norm(label))
                    continue
                print(f"[datapad] deleted {label}" + (f" ({n_after} more with that name)" if n_after else ""))
                deleted.append(label)
                if n_after:
                    tried.remove(norm(label))        # same name, another icon: go again
        except Exception as e:
            self._anomaly("exception", error=f"{type(e).__name__}: {e}")
            self._escape()
            raise
        finally:
            self._close(h)
        data = {"deleted": deleted, "skipped": skipped, "anomalies": list(self.anomalies)}
        if skipped or self.anomalies:
            return Result("failed", "anomaly", data)
        return Result("ok", "", data)

    def _close(self, h):
        """Close the datapad if it's still up; ESC if the toggle didn't do it."""
        try:
            _, boxes = self._look("close")
            if find_text(boxes, "datapad", "really destroy") is not None:
                self.runner.keys.combo(*h.get("datapad_keys", ["CTRL", "d"]))
                self.sleep(0.8)
                if not self._escape():
                    self._anomaly("could_not_close")
        except Exception as e:      # never let closing hide the real result
            print(f"[datapad] close check failed: {e}")

    @staticmethod
    def _annotate(frame, region, labels, path):
        import cv2
        img = frame.copy()
        cv2.rectangle(img, region[:2], region[2:], (0, 255, 255), 2)
        for text, (x, y) in labels:
            cv2.circle(img, (x, y), 18, (0, 0, 255), 3)
            cv2.putText(img, text[:20], (x - 40, y - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.imwrite(str(path), img)


class AbortMissions(CleanWaypoints):
    """
    Drop every mission we hold, the way Marc did it (recording 16 Nov,
    data/captures/2026-11-16_164723_cancel_mission):

      Ctrl+D -> Data tab: missions sit beside the vehicles, named after the mission
      ("Grand Work of Mineral...") -> right-click the icon -> radial "1) Details
      2) Deactivate 3) Abort Mission" -> Abort Mission -> box "Are you sure you want
      to remove this mission?" [Cancel] [OK] -> OK.

    Which icons are missions isn't known from the label alone, so each icon is
    right-clicked and only one whose radial offers "Abort Mission" is touched.
    """
    name = "abort_missions"
    _SKIP = ("speeder", "swoop", "landspeeder", "unit", "droid", "x31", "x34", "helper", "bike")

    def _icons(self, boxes, region, k):
        x0, y0, x1, y1 = region
        gx0 = x0 + int(0.225 * (x1 - x0))
        rows = [(t, b) for t, b in boxes if gx0 <= b[0] and b[2] <= x1 and y0 + 60 * k <= b[1] <= y1 - 90 * k and len(t.strip()) >= 3]
        out = []
        for t, b in sorted(rows, key=lambda tb: (tb[1][1], tb[1][0])):
            cx = (b[0] + b[2]) // 2
            # a label has two lines: keep the top one of each column
            if any(abs(cx - ox) < 45 * k and 0 < b[1] - oy < 45 * k for _, (ox, oy), _ in out):
                continue
            if any(w in t for w in self._SKIP):
                continue
            out.append((t, (cx, b[1]), (cx, b[1] - int(1.6 * (b[3] - b[1])))))
        return out

    def _selected(self, frame, region, k):
        """The pane on the left after an icon is selected: (says "Mission Object", the name above it)."""
        x0, y0, x1, y1 = region
        rect = (x0, y0 + int(20 * k), x0 + int(0.24 * (x1 - x0)), y0 + int(95 * k))
        lines = [t.strip() for t, b in sorted(self._zoom_ocr(frame, 0, 0, scale=2.5, rect=rect), key=lambda tb: tb[1][1])
                 if t.strip() and "datapad" not in t]
        text = _letters(" ".join(lines))
        is_mission = "missionobject" in text or ("mission" in text and "object" in text) \
            or any(reads_like(ln, "mission object") for ln in lines)
        name = " ".join(ln for ln in lines if "object" not in ln and not reads_like(ln, "mission object")).strip()
        return is_mission, name

    def run(self, state, dry_run: bool = False, max_abort: int = 6, titles=(), probe: bool = False, **_) -> Result:
        """titles / probe only say there's something to look for (neither: the datapad isn't opened).
        Every icon that isn't plainly a vehicle or droid is selected (left click, harmless); one
        whose pane says "Mission Object" is a mission and is aborted. Nothing else is right-clicked
        (16 Nov: it was right-clicking the R2 unit)."""
        h = self.settings.raw.get("harness", {})
        keys = self.runner.keys
        want = [norm(t) for t in titles if t]
        if not want and not probe:
            return Result("ok", "nothing_held", {"aborted": []})
        if self._ocr() is None:
            return Result("failed", "no_ocr")
        out_dir = self._dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in out_dir.glob("[0-9][0-9]_*"):
            f.unlink()
        self._step, self.anomalies = 0, []
        keys.combo(*h.get("datapad_keys", ["CTRL", "d"]))
        self.sleep(1.5)
        aborted, checked, seen = [], [], []
        try:
            frame, boxes = self._look("pad")
            region = datapad_region(boxes, frame.shape)
            if region is None:
                self._anomaly("datapad_not_found")
                return Result("failed", "datapad_not_found", {"aborted": aborted})
            x0, y0, x1, y1 = region
            k = frame.shape[0] / 1440
            sx, sy = frame.shape[1] / 2560, frame.shape[0] / 1440
            tab = next((b for t, b in boxes if t.strip(" .|/") == "data" and b[1] < y0 + 70 * k), None)
            keys.click(*(((tab[0] + tab[2]) // 2, (tab[1] + tab[3]) // 2) if tab else (x0 + int(328 * k), y0 + int(34 * k))))
            self.sleep(0.8)
            for _ in range(max_abort + 6):
                frame, boxes = self._look("data_tab")
                # Marc, 16 Nov: the label under an icon is cut short and reads badly ("Solo Kloo- /
                # Horn Reco..."), so matching it to the mission's name failed four times running.
                # Select the icon instead: the pane on the left then shows the full name and,
                # under it, "Mission Object". That line is what makes it a mission.
                todo = [ic for ic in self._icons(boxes, region, k) if norm(ic[0]) not in checked]
                if not todo or len(aborted) >= max_abort:
                    break
                label, _, (ix, iy) = todo[0]
                checked.append(norm(label))
                keys.click(ix, iy)
                self.sleep(0.7)
                frame, _ = self._look("selected")
                is_mission, name = self._selected(frame, region, k)
                seen.append((label, name, is_mission))
                if not is_mission:
                    continue
                label = name or label
                keys.click(ix, iy, "right")
                self.sleep(0.8)
                frame, _ = self._look("radial")
                zoom = self._zoom_ocr(frame, ix, iy)
                abort = next((b for t, b in zoom if "abort" in t), None)
                if abort is None:
                    self._anomaly("abort_not_on_radial", label=label)
                    keys.press("ESC")                      # not a mission: close the radial, next icon
                    self.sleep(0.4)
                    continue
                if dry_run:
                    aborted.append(label)
                    keys.press("ESC")
                    self.sleep(0.4)
                    continue
                keys.click((abort[0] + abort[2]) // 2, (abort[1] + abort[3]) // 2)
                ask = None
                for _w in range(4):
                    self.sleep(0.8)
                    frame, boxes = self._look("confirm")
                    ask = find_text(boxes, "remove this mission", "want to remove", "are you sure")
                    if ask is not None:
                        break
                if ask is None:
                    self._anomaly("abort_no_confirm", label=label)
                    self._escape()
                    break
                keys.click(*confirm_ok_xy(boxes, frame.shape, "remove this mission", "want to remove", "are you sure"))
                self.sleep(1.2)
                aborted.append(label)
                checked.pop()                              # the grid shifts left: look at this spot again
            if aborted and not dry_run:
                state.set("missions", [])
                print(f"[datapad] aborted {len(aborted)} mission(s): {aborted}")
            if not aborted:
                print(f"[datapad] no mission found on the Data tab; selected: {seen}")
            return Result("ok", "dry_run" if dry_run else "", {"aborted": aborted, "checked": checked, "seen": seen})
        finally:
            self._close(h)
