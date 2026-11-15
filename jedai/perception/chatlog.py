"""
Read the SWG client's own chat log instead of OCR'ing the chat window.

`/logChat` makes the client append every chat-window message to
profiles/<account>/<server>/<character id>_chatlog.txt, one line per tab
that shows it:

    [Spatial]  Chat logging ON
    [Combat]  You have been incapacitated by a nightspider aggressor.

It's a per-session toggle (off again after each login), so the bot turns it
on at the start of a session and checks the reply.

Exact text, no OCR misreads, nothing missed between screenshots.
"""

from __future__ import annotations

import glob
import os
import re
import time

_TAB = re.compile(r"^﻿?\[([^\]]*)\]\s+(.*)$")


def find_chatlog(pattern: str) -> str | None:
    """Newest file matching the glob (the character played most recently)."""
    files = glob.glob(os.path.expanduser(pattern))
    return max(files, key=os.path.getmtime) if files else None


class ChatLog:
    def __init__(self, path: str, clock=time.time):
        self.path = path
        self.clock = clock
        self._pos = os.path.getsize(path) if os.path.exists(path) else 0
        self._partial = ""

    def logging_active(self) -> bool:
        """Has /logChat been switched on since the last login (and not off again)?"""
        if not os.path.exists(self.path):
            return False
        with open(self.path, "rb") as f:
            f.seek(max(0, os.path.getsize(self.path) - 200_000))
            text = f.read().decode("utf-8", errors="replace").lower()
        session = text[text.rfind("logging in ["):] if "logging in [" in text else text
        on, off = session.rfind("chat logging on"), session.rfind("chat logging off")
        return on > off

    def mark(self) -> int:
        """Current end of the file: pass to since() later."""
        return os.path.getsize(self.path) if os.path.exists(self.path) else 0

    def since(self, mark: int) -> list[str]:
        """Messages written after `mark` (lower-case, tab prefix stripped, de-duplicated across tabs)."""
        return self._read(mark)[0]

    def read_new(self) -> list[str]:
        """Messages written since the last read_new()."""
        lines, end = self._read(self._pos, keep_partial=True)
        self._pos = end
        return lines

    def _read(self, start: int, keep_partial: bool = False):
        if not os.path.exists(self.path):
            return [], start
        size = os.path.getsize(self.path)
        if size < start:                     # file was replaced/truncated
            start = 0
        with open(self.path, "rb") as f:
            f.seek(start)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if keep_partial:
            text = self._partial + text
            text, sep, rest = text.rpartition("\n")
            self._partial = rest if sep else text
            text = text if sep else ""
        out, last = [], None
        for raw in text.splitlines():
            m = _TAB.match(raw.strip())
            msg = (m.group(2) if m else raw.strip()).lower()
            # the same message is written once per tab that shows it, back to back
            if msg and msg != last:
                out.append(msg)
            last = msg
        return out, start + len(data)
