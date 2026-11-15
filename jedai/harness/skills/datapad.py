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


def ocr_boxes(ocr, img, x0=0, y0=0):
    """[(text_lower, (x1, y1, x2, y2))] in full-frame pixels."""
    out = []
    for box, text, _conf in ocr.readtext(img, detail=1, paragraph=False):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append((text.strip().lower(), (int(min(xs)) + x0, int(min(ys)) + y0,
                                           int(max(xs)) + x0, int(max(ys)) + y0)))
    return out


def norm(s: str) -> str:
    """Compare names despite OCR: lower-case, '_' and '-' and spaces alike."""
    return re.sub(r"[\s_\-.]+", "_", s.lower()).strip("_")


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


def waypoint_labels(boxes, region, prefixes, keep=()):
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
        if not any(n.startswith(p) for p in pre):
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

    def _zoom_ocr(self, frame, cx, cy, half=260, scale=2.0):
        """OCR a crop round (cx, cy), enlarged; boxes in full-frame pixels."""
        import cv2
        h, w = frame.shape[:2]
        x0, y0 = max(0, cx - half), max(0, cy - half)
        x1, y1 = min(w, cx + half), min(h, cy + half)
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

    def run(self, state, keep=(), dry_run: bool = False, max_delete: int = 30, **_) -> Result:
        h = self.settings.raw.get("harness", {})
        prefixes = h.get("bot_waypoint_prefixes", ["badge_", "route_", "bot_"])
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
            for _ in range(max_delete):
                frame, boxes = self._look("grid")
                region = datapad_region(boxes, frame.shape)
                if region is None:
                    self._anomaly("datapad_not_found", deleted=deleted)
                    return Result("failed", "datapad_not_found", {"deleted": deleted})
                labels = [lb for lb in waypoint_labels(boxes, region, prefixes, keep)
                          if norm(lb[0]) not in skipped]
                if dry_run:
                    self._annotate(frame, region, labels, out_dir / "dry_run.jpg")
                    return Result("ok", "dry_run", {"would_delete": [lb[0] for lb in labels],
                                                    "picture": str(out_dir / "dry_run.jpg")})
                if not labels:
                    break
                label, (ix, iy) = labels[0]
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
                destroy = find_text(self._zoom_ocr(frame, ix, iy), "destroy", "4)")
                if destroy is not None:
                    keys.click((destroy[0] + destroy[2]) // 2, (destroy[1] + destroy[3]) // 2)
                else:
                    # fall back: Destroy is option 4, bottom-right of the ring
                    print(f"[datapad] 'Destroy' not read for {label}: clicking option 4's spot")
                    keys.click(ix + int(88 * frame.shape[1] / 2560), iy + int(56 * frame.shape[0] / 1440))
                # the box can take a moment (7 Nov: not there after 0.8 s)
                for _wait in range(4):
                    self.sleep(0.8)
                    frame, boxes = self._look("confirm")
                    if find_text(boxes, "really destroy") is not None:
                        break
                ask = find_text(boxes, "really destroy")
                asked = next((t for t, b in boxes if "really destroy" in t), "")
                if ask is None or not norm(asked.replace("really destroy", "")).startswith(norm(label)[:8]):
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
                still = region is not None and any(
                    norm(t) == norm(label) for t, _ in waypoint_labels(boxes, region, prefixes, keep))
                if still:
                    print(f"[datapad] {label} still listed after Yes: skipping it")
                    self._anomaly("still_listed", label=label)
                    skipped.append(norm(label))
                    continue
                print(f"[datapad] deleted {label}")
                deleted.append(label)
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
