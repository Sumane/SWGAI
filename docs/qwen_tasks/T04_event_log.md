# T04: skill event log

## GOAL
Every skill call is logged the same way. This log is the training data for
System 2 later (state → choice → outcome), so it must be complete and boring.

Needs T02, T03.

## FILES TO WRITE
- `jedai/harness/log.py`
- `tests/test_harness_log.py`

## INTERFACE
```python
class SkillLog:
    def __init__(self, folder: str | Path, clock=time.time):
        """Creates folder/<YYYY-mm-dd_HHMMSS>/ and opens skills.jsonl in it."""
    @property
    def session_dir(self) -> Path

    def run_logged(self, skill: Skill, state: WorldState, decided_by: str, **args) -> Result:
        """
        1. Check skill.preconditions(state, **args). If it returns a reason, log
           and return Result("failed", "precondition: <reason>") without running.
        2. Snapshot state before, run the skill, snapshot state after.
        3. If the skill raises, catch it, log it, return Result("failed", "exception")
           with the traceback text in data["traceback"]. The harness must survive.
        4. On failed/stuck, also state.record_failure(skill.name, <target>, reason),
           where target = args.get("label") or the skill name.
        5. Append one JSON line (below) and flush. Return the Result.
        """
    def note(self, kind: str, **fields) -> None
        """Free-form line, e.g. note("session_start", planet="naboo")."""
    def close(self) -> None
```

One line per skill call:
```json
{"t": 1730390400.1, "type": "skill", "skill": "navigate_to", "args": {...},
 "decided_by": "system2_badges", "state_before": {...}, "state_after": {...},
 "status": "ok", "reason": "", "data": {...}, "duration_s": 93.4}
```

Real use: `SkillLog(settings.data_path("harness_logs"))`.

## DONE WHEN
`python -m pytest -q tests` passes, with tests (fake skills in the test file):
- an ok skill writes one line with all the fields above; duration from a fake clock
- a failing precondition writes a line and doesn't call `run`
- a skill that raises → Result failed/"exception", line written, `record_failure` called
- stuck/failed results call `record_failure` with the label as target
- every line is valid JSON; the file is flushed after each call (read it back mid-session)

## DO NOT
Touch any file not listed above.

## NOTE (not part of this task)
First-person frames for the vision model are NOT captured here yet: the
navigator needs the HUD visible to read coordinates, and the vision frames need
it hidden. Claude will decide how to interleave them.
