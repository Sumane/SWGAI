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
