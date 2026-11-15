"""
Read the chat window and spot new system messages (e.g. a badge being granted).

Core3 sends badge results as system messages (badge_n:prose_grant "you have
earned ... badge", prose_hasbadge "you already have ... badge"). The chat keeps
old lines, so "is there a badge line?" isn't enough: compare against a baseline
read before the action and look for badge lines that weren't there.
"""

from __future__ import annotations

import difflib

from .tiles import region_box


def read_chat_lines(frame, ocr, region_frac) -> list[str]:
    """OCR the chat region into lines, top to bottom (lower-case)."""
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = region_box(w, h, region_frac)
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    try:
        res = ocr.readtext(crop, detail=1, paragraph=False)
    except Exception:  # noqa: BLE001
        return []
    # group boxes into lines by their vertical centre
    items = sorted(((sum(p[1] for p in box) / 4, min(p[0] for p in box), text) for box, text, _ in res))
    lines, cur, cur_y = [], [], None
    for y, x, text in items:
        if cur_y is not None and abs(y - cur_y) > 8:
            lines.append(" ".join(t for _, t in sorted(cur)))
            cur = []
        cur.append((x, text))
        cur_y = y if cur_y is None else (cur_y + y) / 2
    if cur:
        lines.append(" ".join(t for _, t in sorted(cur)))
    return [ln.lower() for ln in lines]


# Combat / death lines Core3 sends (7 Nov nightspiders, 8 Nov maulers).
# Short stems, because the OCR mangles the small chat font: on 8 Nov it read
# "been incapacitated by mauler" and "you were shatm mauler requesting cone
# artiuatiog" while the character died, and the full phrases matched nothing.
DEAD = ("slain", "clone activ", "requesting c", "you have died")
DOWN = ("incapacitated", "forced to kneel", "knocked down")
ATTACKED = ("poisoned", "more poison", "stunned", "attacks you", "diseased", "on fire", "hits you",
            "dizzied", "feel dizzy", "start to bleed", "bleeding")


def classify(lines: list[str]) -> str | None:
    """'dead' > 'down' > 'attacked' > None for a batch of NEW chat lines."""
    text = " | ".join(lines)
    for kind, keys in (("dead", DEAD), ("down", DOWN), ("attacked", ATTACKED)):
        if any(k in text for k in keys):
            return kind
    return None


def new_lines(before: list[str], after: list[str]) -> list[str]:
    """Lines in `after` not in `before` (OCR wobble tolerated)."""
    return [ln for ln in after if not _seen(ln, before)]


def _seen(line: str, baseline: list[str], ratio: float = 0.85) -> bool:
    return any(difflib.SequenceMatcher(None, line, b).ratio() >= ratio for b in baseline)


def new_badge_lines(before: list[str], after: list[str]) -> list[str]:
    """Badge lines in `after` that weren't in `before` (OCR wobble tolerated)."""
    return [ln for ln in after if "badge" in ln and not _seen(ln, before)]
