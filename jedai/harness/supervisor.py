"""The loop: read position -> System 2 decides -> run the skill through the log -> repeat."""

from __future__ import annotations

import time


class Supervisor:
    def __init__(self, registry, state, log, system2, *, planet: str, read_position=None,
                 stop_check=lambda: False, max_steps: int | None = None,
                 max_minutes: float | None = None, max_consecutive_failures: int = 3,
                 clock=time.time):
        self.registry, self.state, self.log, self.system2 = registry, state, log, system2
        self.planet = planet
        self.read_position = read_position
        self.stop_check = stop_check
        self.max_steps = max_steps
        self.max_minutes = max_minutes
        self.max_fail = max_consecutive_failures
        self.clock = clock
        self.steps = 0

    def _limit(self, t0: float) -> str | None:
        if self.stop_check():
            return "killed"
        if self.max_steps is not None and self.steps >= self.max_steps:
            return "max_steps"
        if self.max_minutes is not None and self.clock() - t0 >= self.max_minutes * 60:
            return "max_minutes"
        return None

    def run(self) -> str:
        t0 = self.clock()
        fails = 0
        reason = "done"
        self.log.note("session_start", planet=self.planet, system2=self.system2.name)
        try:
            while True:
                stop = self._limit(t0)
                if stop:
                    reason = stop
                    break
                if self.read_position is not None:
                    pos = self.read_position()
                    if pos is not None:
                        self.state.set_position(self.planet, pos[0], pos[1])
                decision = self.system2.decide(self.state)
                if decision is None:
                    reason = "done"
                    break
                self.log.note("decision", skill=decision.skill, args=decision.args, why=decision.why)
                print(f"[harness] {decision.why}")
                result = self.log.run_logged(self.registry.get(decision.skill), self.state,
                                             self.system2.name, **decision.args)
                self.system2.on_result(decision, result, self.state)
                print(f"[harness]   -> {result.status} {result.reason}")
                self.steps += 1
                fails = 0 if result.ok else fails + 1
                if fails >= self.max_fail:
                    reason = "too_many_failures"
                    break
        except BaseException as e:
            reason = f"crashed: {type(e).__name__}: {e}"
            raise
        finally:
            self.log.note("session_end", reason=reason, steps=self.steps)
        return reason
