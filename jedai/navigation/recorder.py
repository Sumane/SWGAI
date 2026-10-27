"""
Run recorder: a flight recorder for each navigation run.

Every run gets its own folder under paths.debug_dir:

    data/debug_logs/2026-10-27_173600_Naboo/
        ticks.jsonl     one line per main-loop tick: raw and accepted readings,
                        position, heading, state, steering, estimate, D* goal
        events.jsonl    stuck, unstick, dismount, estimate moves, cells marked,
                        rejected misreads, heading overrides
        console.log     everything printed during the run
        shots/          screenshots: every few seconds, and on every event
        report.html     open in a browser: path map, distance chart, timeline
        path.png        the map on its own

Zip the folder and send it to whoever is debugging. scripts/review_run.py
rebuilds the report and prints a summary of what went wrong.

The recorder must never stop a run: every method swallows its own errors.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from pathlib import Path


def _round(v, n=1):
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        return [_round(x, n) for x in v]
    if isinstance(v, float):
        return round(v, n)
    return v


class _Tee:
    """Copy stdout to a file as well."""

    def __init__(self, stream, path):
        self.stream = stream
        self.f = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, s):
        try:
            self.f.write(s)
        except Exception:
            pass
        return self.stream.write(s)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class RunRecorder:
    def __init__(self, base_dir, target_name="", shot_every_s=5.0, shot_width=1280,
                 enabled=True, clock=time.time):
        self.enabled = enabled
        self.clock = clock
        self.shot_every_s = shot_every_s
        self.shot_width = shot_width
        self.dir = None
        self._last_shot = -1e9
        self._t0 = clock()
        self._tick = 0
        self._tee = None
        self._ticks_f = self._events_f = None
        self._pending_shot = None
        if not enabled:
            return
        try:
            name = re.sub(r"[^A-Za-z0-9_-]+", "_", target_name or "run")[:40]
            stamp = time.strftime("%Y-%m-%d_%H%M%S")
            self.dir = Path(base_dir) / f"{stamp}_{name}"
            (self.dir / "shots").mkdir(parents=True, exist_ok=True)
            self._ticks_f = open(self.dir / "ticks.jsonl", "a", encoding="utf-8", buffering=1)
            self._events_f = open(self.dir / "events.jsonl", "a", encoding="utf-8", buffering=1)
            self._tee = _Tee(sys.stdout, self.dir / "console.log")
            sys.stdout = self._tee
            print(f"\n📼  Recording this run to {self.dir}")
        except Exception as e:
            print(f"[REC] Recording disabled: {e}")
            self.enabled = False

    # ------------------------------------------------------------------
    def t(self):
        return round(self.clock() - self._t0, 2)

    def tick(self, frame=None, **fields):
        """One main-loop tick. Takes a screenshot every shot_every_s."""
        if not self.enabled:
            return
        try:
            self._tick += 1
            rec = {"t": self.t(), "i": self._tick}
            rec.update({k: _round(v) for k, v in fields.items()})
            if frame is not None and (self.clock() - self._last_shot >= self.shot_every_s
                                      or self._pending_shot):
                rec["shot"] = self._shot(frame, self._pending_shot or "tick")
                self._pending_shot = None
            self._ticks_f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def event(self, kind, frame=None, **fields):
        """Something happened. A screenshot is taken now (if a frame is given) or on the next tick."""
        if not self.enabled:
            return
        try:
            rec = {"t": self.t(), "i": self._tick, "kind": kind}
            rec.update({k: _round(v) for k, v in fields.items()})
            if frame is not None:
                rec["shot"] = self._shot(frame, kind)
            else:
                self._pending_shot = kind
            self._events_f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def _shot(self, frame, label):
        import cv2

        self._last_shot = self.clock()
        h, w = frame.shape[:2]
        if w > self.shot_width:
            frame = cv2.resize(frame, (self.shot_width, int(h * self.shot_width / w)),
                               interpolation=cv2.INTER_AREA)
        name = f"{self._tick:05d}_{self.t():07.1f}s_{re.sub(r'[^a-z0-9_]+', '', label.lower())}.jpg"
        cv2.imwrite(str(self.dir / "shots" / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return f"shots/{name}"

    def close(self, result="", blocked_cells=(), clear_cells=()):
        if not self.enabled:
            return
        try:
            self.event("end", result=result)
            self._ticks_f.close()
            self._events_f.close()
            if self._tee is not None and sys.stdout is self._tee:
                sys.stdout = self._tee.stream
            write_report(self.dir, blocked_cells, clear_cells)
            print(f"📼  Run recorded: {self.dir / 'report.html'}")
        except Exception as e:
            print(f"[REC] Couldn't finish the report: {e}")


# ======================================================================
# Report
# ======================================================================
def load_run(run_dir):
    run_dir = Path(run_dir)

    def read(name):
        p = run_dir / name
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out

    return read("ticks.jsonl"), read("events.jsonl")


STATE_COLOURS = {"MOVING": (60, 180, 60), "RECALIBRATING": (40, 140, 255), "EXPLORING": (255, 120, 40)}
EVENT_COLOURS = {"stuck": (0, 0, 255), "unstick": (0, 160, 255), "dismount": (255, 0, 255),
                 "heading_override": (255, 255, 0), "goal": (255, 255, 255)}


def draw_path(ticks, events, blocked=(), clear=(), size=900):
    """Top-down map (X right, Z up): path coloured by state, cells, estimate, goal."""
    import cv2
    import numpy as np

    pts = [tuple(t["pos"]) for t in ticks if t.get("pos")]
    ests = [tuple(t["est"]) for t in ticks if t.get("est")]
    if not pts:
        img = np.full((200, 400, 3), 30, np.uint8)
        cv2.putText(img, "no positions recorded", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
        return img
    final_est = ests[-1] if ests else None
    xs = [p[0] for p in pts] + ([final_est[0]] if final_est else [])
    zs = [p[1] for p in pts] + ([final_est[1]] if final_est else [])
    span = max(max(xs) - min(xs), max(zs) - min(zs), 50.0) * 1.1
    cx, cz = (max(xs) + min(xs)) / 2, (max(zs) + min(zs)) / 2
    s = (size - 40) / span

    def P(x, z):
        return int(size / 2 + (x - cx) * s), int(size / 2 - (z - cz) * s)

    img = np.full((size, size, 3), 25, np.uint8)
    cell = max(1, int(5 * s))
    for (x, z) in clear:
        a = P(x, z)
        cv2.rectangle(img, a, (a[0] + cell, a[1] - cell), (45, 70, 45), -1)
    for (x, z) in blocked:
        a = P(x, z)
        cv2.rectangle(img, a, (a[0] + cell, a[1] - cell), (40, 40, 160), -1)
    # scale bar
    bar = 10 ** math.floor(math.log10(span / 4))
    cv2.line(img, (20, size - 20), (20 + int(bar * s), size - 20), (200, 200, 200), 2)
    cv2.putText(img, f"{bar:.0f} m", (20, size - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    cv2.putText(img, "X ->  Z ^", (size - 110, size - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
    # estimate track (where the bot thought the waypoint was)
    for e in ests[::max(1, len(ests) // 200)]:
        cv2.circle(img, P(*e), 2, (120, 120, 0), -1)
    # path
    prev = None
    for t in ticks:
        if not t.get("pos"):
            continue
        p = P(*t["pos"])
        if prev is not None:
            cv2.line(img, prev, p, STATE_COLOURS.get(t.get("state"), (200, 200, 200)), 2)
        prev = p
    cv2.circle(img, P(*pts[0]), 7, (255, 255, 255), 2)
    cv2.putText(img, "start", (P(*pts[0])[0] + 8, P(*pts[0])[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.circle(img, P(*pts[-1]), 6, (255, 255, 255), -1)
    if final_est:
        cv2.drawMarker(img, P(*final_est), (0, 255, 255), cv2.MARKER_STAR, 18, 2)
        cv2.putText(img, "waypoint (estimate)", (P(*final_est)[0] + 10, P(*final_est)[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    for ev in events:
        if ev.get("pos") and ev.get("kind") in EVENT_COLOURS:
            cv2.drawMarker(img, P(*ev["pos"]), EVENT_COLOURS[ev["kind"]], cv2.MARKER_TILTED_CROSS, 10, 2)
    # legend
    y = 20
    for k, c in list(STATE_COLOURS.items()) + list(EVENT_COLOURS.items()):
        cv2.rectangle(img, (size - 170, y - 9), (size - 160, y + 1), c, -1)
        cv2.putText(img, k, (size - 152, y), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 220, 220), 1)
        y += 16
    return img


def draw_distance(ticks, events, w=900, h=260):
    """Waypoint distance over time; red where it was going up."""
    import cv2
    import numpy as np

    img = np.full((h, w, 3), 25, np.uint8)
    pts = [(t["t"], t["dist"]) for t in ticks if t.get("dist") is not None]
    if len(pts) < 2:
        return img
    t0, t1 = pts[0][0], pts[-1][0] or 1
    dmax = max(d for _, d in pts) * 1.05 or 1

    def P(t, d):
        return int(40 + (t - t0) / max(t1 - t0, 1e-6) * (w - 60)), int(h - 25 - d / dmax * (h - 45))

    for (ta, da), (tb, db) in zip(pts, pts[1:]):
        cv2.line(img, P(ta, da), P(tb, db), (60, 60, 230) if db > da else (60, 200, 60), 2)
    for ev in events:
        if ev.get("kind") in EVENT_COLOURS:
            x = P(ev["t"], 0)[0]
            cv2.line(img, (x, 15), (x, h - 25), EVENT_COLOURS[ev["kind"]], 1)
    cv2.putText(img, f"{dmax:.0f} m", (2, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    cv2.putText(img, f"{t1 - t0:.0f} s", (w - 60, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    cv2.putText(img, "waypoint distance (red = getting further away)", (45, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
    return img


def analyse(ticks, events):
    """Plain-language findings: when it went the wrong way, and what it was doing."""
    out = []
    d = [(t["t"], t["dist"], t.get("state"), t.get("steer_by")) for t in ticks if t.get("dist") is not None]
    if not d:
        return ["No distance readings recorded."]
    out.append(f"Start {d[0][1]} m, end {d[-1][1]} m, closest {min(x[1] for x in d)} m, "
               f"{d[-1][0] - d[0][0]:.0f} s.")
    # Wrong-way spells: the distance climbs at least 15 m above its lowest point
    # since the last spell, until it falls 15 m from its peak again. Reports when, for how long, and what was steering.
    spells, low, cur = [], d[0], None
    for t, dist, state, by in d:
        if cur is None:
            if dist < low[1]:
                low = (t, dist, state, by)
            elif dist - low[1] >= 15:
                cur = {"t0": low[0], "low": low[1], "peak": dist, "t1": t, "modes": {}}
        if cur is not None:
            mode = f"{state or '?'} / {by or '?'}"
            cur["modes"][mode] = cur["modes"].get(mode, 0) + 1
            if dist > cur["peak"]:
                cur["peak"], cur["t1"] = dist, t
            if dist <= cur["peak"] - 15:          # heading back in again
                spells.append(cur)
                cur, low = None, (t, dist, state, by)
    if cur is not None:
        spells.append(cur)
    if spells:
        total = sum(sp["peak"] - sp["low"] for sp in spells)
        out.append(f"Went the wrong way {len(spells)} time(s), {total:.0f} m in total:")
        for sp in sorted(spells, key=lambda x: x["low"] - x["peak"])[:10]:
            mode = max(sp["modes"].items(), key=lambda kv: kv[1])[0]
            out.append(f"  {sp['t0']:6.0f}s-{sp['t1']:6.0f}s  +{sp['peak'] - sp['low']:4.0f} m "
                       f"({sp['low']} -> {sp['peak']} m), mostly {mode}")
    else:
        out.append("Never went the wrong way by more than 15 m.")
    kinds = {}
    for ev in events:
        kinds[ev["kind"]] = kinds.get(ev["kind"], 0) + 1
    if kinds:
        out.append("Events: " + ", ".join(f"{k} x{v}" for k, v in sorted(kinds.items())))
    rej = sum(1 for t in ticks if t.get("dist_raw") is not None and t.get("dist") is None)
    if rej:
        out.append(f"{rej} distance readings rejected as misreads.")
    return out


def write_report(run_dir, blocked=(), clear=()):
    import cv2

    run_dir = Path(run_dir)
    ticks, events = load_run(run_dir)
    cv2.imwrite(str(run_dir / "path.png"), draw_path(ticks, events, blocked, clear))
    cv2.imwrite(str(run_dir / "distance.png"), draw_distance(ticks, events))
    findings = analyse(ticks, events)
    (run_dir / "summary.txt").write_text("\n".join(findings) + "\n", encoding="utf-8")

    rows = []
    for ev in events:
        shot = f'<a href="{ev["shot"]}"><img src="{ev["shot"]}" width="240"></a>' if ev.get("shot") else ""
        detail = ", ".join(f"{k}={v}" for k, v in ev.items() if k not in ("t", "i", "kind", "shot"))
        rows.append(f"<tr><td>{ev['t']:.1f}s</td><td><b>{ev['kind']}</b></td><td>{detail}</td><td>{shot}</td></tr>")
    shots = sorted((run_dir / "shots").glob("*.jpg")) if (run_dir / "shots").exists() else []
    gallery = "".join(f'<a href="shots/{p.name}"><img src="shots/{p.name}" width="200" title="{p.name}"></a>'
                      for p in shots)
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>JedAI run {run_dir.name}</title>
<style>body{{background:#1b1b1b;color:#ddd;font:14px sans-serif;margin:20px}}
table{{border-collapse:collapse}}td{{border-bottom:1px solid #333;padding:4px 8px;vertical-align:top}}
img{{margin:2px}}pre{{background:#111;padding:10px}}</style></head><body>
<h2>JedAI run {run_dir.name}</h2>
<pre>{chr(10).join(findings)}</pre>
<img src="path.png"><br><img src="distance.png">
<h3>Events</h3><table>{''.join(rows)}</table>
<h3>All screenshots</h3>{gallery}
<p>Raw data: ticks.jsonl, events.jsonl, console.log</p></body></html>"""
    (run_dir / "report.html").write_text(html, encoding="utf-8")
    return findings
