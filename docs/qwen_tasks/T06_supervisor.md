# T06: supervisor + run_harness.py

## GOAL
The loop that ties it together: read state → System 2 decides → run the skill
through the log → System 2 sees the result → repeat, with safe stop conditions.

Needs T02–T05.

## FILES TO WRITE
- `jedai/harness/supervisor.py`
- `scripts/run_harness.py`
- `tests/test_harness_supervisor.py`

## INTERFACE
```python
class Supervisor:
    def __init__(self, registry, state, log, system2, *,
                 read_position=None,          # callable -> (x, z) | None
                 planet: str,
                 stop_check=lambda: False,    # True = stop now (kill key)
                 max_steps: int | None = None,
                 max_minutes: float | None = None,
                 max_consecutive_failures: int = 3,
                 clock=time.time):
    def run(self) -> str:
        """Returns why it stopped: "done", "killed", "max_steps", "max_minutes",
        "too_many_failures"."""
```

Loop:
1. `stop_check()` → stop "killed". Time/step limits → stop.
2. Refresh position: `read_position()` → `state.set_position(planet, x, z)`.
3. `decision = system2.decide(state)`; None → stop "done".
4. `log.note("decision", skill=..., args=..., why=...)`.
5. `result = log.run_logged(registry.get(decision.skill), state, system2.name, **decision.args)`
6. `system2.on_result(decision, result, state)`
7. Count consecutive non-ok results; reset on ok; too many → stop "too_many_failures".
8. `log.note("session_end", reason=...)` before returning, always (try/finally).

`scripts/run_harness.py`:
```
python scripts/run_harness.py [--max-steps N] [--minutes M] [--skip KEY ...]
```
Builds: `NavigationRunner(settings)`, `WorldState(settings.data_path("harness","world_state.db"))`,
`SkillLog(settings.data_path("harness_logs"))`, `default_registry(...)`,
`BadgeCollector(load_world_points())`, `stop_check = lambda: kill_pressed(settings.kill_key, settings)`,
`read_position = runner.read_position`. Prints the stop reason and the session folder.

## DONE WHEN
`python -m pytest -q tests` passes, with tests using fakes (fake skills, fake
system2 returning a scripted list of decisions, `WorldState(":memory:")`, a real
`SkillLog` in `tmp_path`, fake clock):
- runs decisions until system2 returns None → "done"
- stop_check True on step 2 → "killed", only 1 skill ran
- max_steps and max_minutes stop correctly
- 3 failures in a row → "too_many_failures"; fail, fail, ok, fail does not stop
- position from read_position is written to state before each decision
- session_end is logged even when a skill raises
- `scripts/run_harness.py --help` runs (subprocess) without touching the game

## DO NOT
Touch any file not listed above.
