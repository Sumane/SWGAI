"""Small text helpers shared by the HUD readers."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def fuzzy_match(target: str | None, detected: str | None, threshold: float = 0.5) -> bool:
    """True if two waypoint names are probably the same (OCR is noisy)."""
    if not target or not detected:
        return False
    t, d = target.lower().strip(), detected.lower().strip()
    if t in d or d in t:
        return True
    return SequenceMatcher(None, t, d).ratio() >= threshold


def assemble_digits(detections: list[tuple[float, str]]) -> str | None:
    """
    Turn per-character detections [(x_left, label), ...] into a string,
    left to right, keeping only digits and '-'.
    """
    if not detections:
        return None
    raw = "".join(label for _, label in sorted(detections, key=lambda d: d[0]))
    raw = re.sub(r"[^0-9\-]", "", raw)
    return raw or None


def parse_distance(raw: str | None) -> int | None:
    """Waypoint distance in metres, or None if implausible."""
    if not raw:
        return None
    numeric = re.sub(r"[^0-9]", "", raw)
    if numeric:
        val = int(numeric)
        if 0 < val < 9999:
            return val
    return None


def repair_extra_digit(value: int | None, last: int | None, tol: float) -> int | None:
    """
    The distance reader sometimes sees one digit too many: in-game, 298 m read
    as 2988 (part of the waypoint arrow next to it read as an 8), 887 as 8987.
    If removing one digit gives a number within `tol` of the last good reading,
    that's what the HUD really said. Returns the repaired value, or the value
    unchanged if no repair fits.
    """
    if value is None or last is None:
        return value
    s = str(value)
    if len(s) != len(str(last)) + 1:
        return value
    best = None
    for i in range(len(s)):
        cand = int(s[:i] + s[i + 1:])
        if abs(cand - last) <= tol and (best is None or abs(cand - last) < abs(best - last)):
            best = cand
    return best if best is not None else value
