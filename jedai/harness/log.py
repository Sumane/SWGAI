"""
One JSON line per skill call: state before/after, who decided, outcome, time.
This is the future training data for a learned System 2, so it records
everything, including precondition refusals and exceptions.
Real use: SkillLog(settings.data_path("harness_logs")).
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from .skill import Result, Skill
from .state import WorldState


class SkillLog:
    def __init__(self, folder: str | Path, clock=time.time):
        self.clock = clock
        stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(clock()))
        self._dir = Path(folder) / stamp
        n = 1
        while self._dir.exists():                 # two sessions in the same second
            self._dir = Path(folder) / f"{stamp}_{n}"
            n += 1
        self._dir.mkdir(parents=True)
        self._f = open(self._dir / "skills.jsonl", "a")

    @property
    def session_dir(self) -> Path:
        return self._dir

    def _write(self, rec: dict) -> None:
        self._f.write(json.dumps(rec, default=str) + "\n")
        self._f.flush()

    def note(self, kind: str, **fields) -> None:
        self._write({"t": self.clock(), "type": kind, **fields})

    def run_logged(self, skill: Skill, state: WorldState, decided_by: str, **args) -> Result:
        t0 = self.clock()
        before = state.snapshot()
        why_not = skill.preconditions(state, **args)
        if why_not is not None:
            result = Result("failed", f"precondition: {why_not}")
        else:
            try:
                result = skill.run(state, **args)
            except Exception:  # noqa: BLE001 - the harness must survive a broken skill
                result = Result("failed", "exception", {"traceback": traceback.format_exc()})
        if not result.ok:
            state.record_failure(skill.name, str(args.get("label") or skill.name), result.reason)
        self._write({"t": t0, "type": "skill", "skill": skill.name, "args": args,
                     "decided_by": decided_by, "state_before": before,
                     "state_after": state.snapshot(), "status": result.status,
                     "reason": result.reason, "data": result.data,
                     "duration_s": round(self.clock() - t0, 3)})
        return result

    def close(self) -> None:
        self._f.close()
