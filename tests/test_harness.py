"""Harness v0: world points, state, skills, log, System 2, supervisor. No game needed."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from jedai.data import load_world_points
from jedai.harness.log import SkillLog
from jedai.harness.skill import Result, Skill, SkillRegistry
from jedai.harness.skills import default_registry
from jedai.harness.skills.navigation import Dismount, Mount, NavigateTo
from jedai.harness.state import WorldState
from jedai.harness.supervisor import Supervisor
from jedai.harness.system2 import BadgeCollector, Decision

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# --- world points ------------------------------------------------------------

def test_world_points_content():
    wp = load_world_points()
    for p in ["corellia", "dantooine", "dathomir", "endor", "lok", "naboo", "rori", "talus", "tatooine", "yavin4"]:
        assert any(t["kind"] == "starport" for t in wp[p]["travel"]), p
    cor = {t["name"]: t for t in wp["corellia"]["travel"]}
    assert cor["Coronet Starport"] == {"name": "Coronet Starport", "x": -66.76, "z": -4711.33,
                                       "height": 28.0, "kind": "starport", "interplanetary": True}
    assert cor["Coronet Shuttle A"]["kind"] == "shuttleport"
    bv = {b["key"]: b for b in wp["corellia"]["badges"]}["badge_bela_vistal_fountain"]
    assert (bv["x"], bv["z"], bv["badge_id"], bv["radius"]) == (6767, -5617, 81, 30)
    assert all(b["radius"] > 0 and isinstance(b["badge_id"], int) for p in wp.values() for b in p["badges"])
    assert "tutorial" not in wp


def test_extract_is_reproducible(tmp_path):
    src = ROOT / "data/core3/planet_manager.lua"
    if not src.exists():
        pytest.skip("planet_manager.lua not downloaded")
    out = tmp_path / "wp.json"
    subprocess.run([sys.executable, "scripts/extract_world_points.py", "--src", str(src), "--out", str(out)],
                   cwd=ROOT, check=True, capture_output=True)
    assert json.loads(out.read_text()) == load_world_points()


# --- state -------------------------------------------------------------------

def test_state_roundtrip_and_persistence(tmp_path):
    db = tmp_path / "s.db"
    s = WorldState(db)
    for k, v in {"a": "x", "b": 3, "c": 1.5, "d": [1, 2], "e": {"k": 1}}.items():
        s.set(k, v)
        assert s.get(k) == v
    assert s.get("missing", 7) == 7
    assert s.position() is None
    s.set_position("naboo", 1.0, 2.0)
    s.add_badge("b1")
    s.add_badge("b1")
    s.close()
    s = WorldState(db)
    assert s.position() == ("naboo", 1.0, 2.0)
    assert s.badges() == {"b1"} and s.has_badge("b1") and not s.has_badge("b2")
    assert s.snapshot()["d"] == [1, 2]


def test_recent_failures_window():
    clk = Clock()
    s = WorldState(":memory:", clock=clk)
    s.record_failure("navigate_to", "b1", "stuck")
    s.record_failure("navigate_to", "b2", "stuck")
    clk.t += 4000
    s.record_failure("navigate_to", "b1", "stuck")
    assert s.recent_failures("navigate_to", "b1") == 1
    assert s.recent_failures("navigate_to", "b1", within_s=10000) == 2
    assert s.recent_failures("mount", "b1") == 0


# --- skills ------------------------------------------------------------------

class FakeKeys:
    def __init__(self):
        self.typed, self.pressed, self.dismounts = [], [], []

    def type_text(self, t):
        self.typed.append(t)

    def press(self, k):
        self.pressed.append(k)

    def dismount(self, *a):
        self.dismounts.append(a)


class FakeRunner:
    def __init__(self, outcome="arrived", pos=(10.0, 20.0)):
        self.keys = FakeKeys()
        self.outcome, self.pos = outcome, pos
        self.navigated = []
        self.mounted, self.current_vehicle = True, "swoop"

    def navigate(self, target_name=None):
        self.navigated.append(target_name)
        return self.outcome

    def read_position(self):
        return self.pos

    def apply_vehicle(self, key):
        self.current_vehicle = key
        self.mounted = key not in ("walking", "running")


class FakeSettings:
    planet = "naboo"
    raw = {"harness": {"waypoint_command": "/waypoint {x:.0f} {z:.0f} {label}",
                       "waypoint_settle_s": 0, "mount_key": "F9", "mount_settle_s": 0}}
    nav = {"default_vehicle": "swoop", "foot_vehicle": "running", "dismount_method": "command",
           "dismount_command": "/dismount", "dismount_settle_secs": 0}


@pytest.mark.parametrize("outcome,status,reason", [
    ("arrived", "ok", ""), ("no_waypoint", "failed", "no_waypoint"),
    ("stopped", "stuck", "stopped"), ("crashed: ValueError: x", "failed", "crashed")])
def test_navigate_to_outcomes(outcome, status, reason):
    r = FakeRunner(outcome)
    s = WorldState(":memory:")
    res = NavigateTo(r, FakeSettings(), sleep=lambda _: None).run(s, x=-5000.4, z=4200.6, label="badge_a")
    assert r.keys.typed == ["/waypoint -5000 4201 badge_a"]
    assert r.navigated == ["badge_a"]
    assert (res.status, res.reason) == (status, reason)
    assert s.position() == ("naboo", 10.0, 20.0)
    assert s.get("mounted") is True


def test_mount_dismount():
    r, s, st = FakeRunner(), WorldState(":memory:"), FakeSettings()
    s.set("mounted", True)
    m, d = Mount(r, st, sleep=lambda _: None), Dismount(r, st, sleep=lambda _: None)
    assert m.preconditions(s) == "already mounted" and d.preconditions(s) is None
    assert d.run(s).ok and s.get("mounted") is False and r.current_vehicle == "running"
    assert d.preconditions(s) == "not mounted"
    assert m.run(s).ok and s.get("mounted") is True and r.keys.pressed == ["F9"]
    st2 = FakeSettings()
    st2.raw = {"harness": dict(FakeSettings.raw["harness"], mount_key="")}
    assert Mount(r, st2, sleep=lambda _: None).run(s).reason == "no_mount_key"


def test_registry():
    reg = default_registry(FakeRunner(), WorldState(":memory:"), FakeSettings())
    assert reg.names() == ["dismount", "mount", "navigate_to"]
    with pytest.raises(ValueError):
        reg.register(Mount(None, None))
    with pytest.raises(KeyError):
        reg.get("nope")


# --- log ---------------------------------------------------------------------

class OkSkill(Skill):
    name = "ok_skill"

    def run(self, state, **a):
        state.set("ran", True)
        return Result("ok", data={"n": 1})


class BoomSkill(Skill):
    name = "boom"

    def run(self, state, **a):
        raise RuntimeError("bang")


class Picky(Skill):
    name = "picky"
    ran = False

    def preconditions(self, state, **a):
        return "not today"

    def run(self, state, **a):
        self.ran = True
        return Result("ok")


def _lines(log):
    return [json.loads(x) for x in (log.session_dir / "skills.jsonl").read_text().splitlines()]


def test_log_records_everything(tmp_path):
    s = WorldState(":memory:")
    log = SkillLog(tmp_path, clock=Clock())
    assert log.run_logged(OkSkill(), s, "test", label="L").ok
    assert _lines(log)[0]["state_after"]["ran"] is True          # flushed mid-session
    p = Picky()
    r = log.run_logged(p, s, "test")
    assert r.reason == "precondition: not today" and not p.ran
    r = log.run_logged(BoomSkill(), s, "test", label="X")
    assert r.reason == "exception" and "bang" in r.data["traceback"]
    lines = _lines(log)
    assert [l["skill"] for l in lines] == ["ok_skill", "picky", "boom"]
    assert {"t", "args", "decided_by", "state_before", "state_after", "status", "reason", "data",
            "duration_s"} <= set(lines[0])
    assert s.recent_failures("boom", "X") == 1 and s.recent_failures("picky", "picky") == 1


# --- System 2 ----------------------------------------------------------------

WP = {"naboo": {"travel": [], "badges": [
    {"key": "near", "badge_id": 1, "x": 10, "z": 0, "radius": 30},
    {"key": "mid", "badge_id": 2, "x": 100, "z": 0, "radius": 30},
    {"key": "far", "badge_id": 3, "x": 1000, "z": 0, "radius": 30}]},
    "tatooine": {"travel": [], "badges": [{"key": "tat", "badge_id": 4, "x": 1, "z": 1, "radius": 30}]}}


def test_badge_collector_order_and_skips():
    s = WorldState(":memory:")
    bc = BadgeCollector(WP, skip_keys={"mid"})
    assert bc.decide(s) is None                                   # no position yet
    s.set_position("naboo", 0, 0)
    d = bc.decide(s)
    assert d.args == {"x": 10, "z": 0, "label": "near"} and "10 m" in d.why
    bc.on_result(d, Result("ok"), s)
    assert bc.decide(s).args["label"] == "far"                    # mid skipped
    s.record_failure("navigate_to", "far", "stuck")
    assert bc.decide(s).args["label"] == "far"                    # 1 failure: still tried
    s.record_failure("navigate_to", "far", "stuck")
    assert bc.decide(s) is None                                   # 2 failures: given up
    bc.on_result(Decision("navigate_to", {"label": "x"}, ""), Result("stuck"), s)
    assert not s.has_badge("x")


# --- supervisor --------------------------------------------------------------

class Scripted:
    name = "scripted"

    def __init__(self, n):
        self.n, self.seen = n, []

    def decide(self, state):
        if self.n == 0:
            return None
        self.n -= 1
        return Decision("fake", {"label": f"d{self.n}"}, "test")

    def on_result(self, d, r, s):
        self.seen.append(r.status)


class Fake(Skill):
    name = "fake"

    def __init__(self, statuses, clock=None):
        self.statuses, self.clock = list(statuses), clock

    def run(self, state, **a):
        if self.clock:
            self.clock.t += 120
        st = self.statuses.pop(0) if self.statuses else "ok"
        if st == "raise":
            raise KeyboardInterrupt
        return Result(st)


def _sup(tmp_path, statuses, n=5, clock=None, **kw):
    reg = SkillRegistry()
    clock = clock or Clock()
    reg.register(Fake(statuses, clock))
    s = WorldState(":memory:")
    log = SkillLog(tmp_path, clock=clock)
    return Supervisor(reg, s, log, Scripted(n), planet="naboo", clock=clock, **kw), s, log


def test_supervisor_done_and_position(tmp_path):
    sup, s, log = _sup(tmp_path, [], n=3, read_position=lambda: (5.0, 6.0))
    assert sup.run() == "done" and sup.steps == 3
    assert s.position() == ("naboo", 5.0, 6.0)
    assert _lines(log)[-1] == {**_lines(log)[-1], "type": "session_end", "reason": "done"}


def test_supervisor_limits(tmp_path):
    calls = iter([False, True])
    sup, *_ = _sup(tmp_path / "a", [], stop_check=lambda: next(calls))
    assert sup.run() == "killed" and sup.steps == 1
    sup, *_ = _sup(tmp_path / "b", [], max_steps=2)
    assert sup.run() == "max_steps" and sup.steps == 2
    sup, *_ = _sup(tmp_path / "c", [], max_minutes=5)            # each step takes 2 min
    assert sup.run() == "max_minutes" and sup.steps == 3


def test_supervisor_failures(tmp_path):
    sup, *_ = _sup(tmp_path / "a", ["stuck", "failed", "stuck"])
    assert sup.run() == "too_many_failures"
    sup, *_ = _sup(tmp_path / "b", ["stuck", "stuck", "ok", "stuck"], n=4)
    assert sup.run() == "done"


def test_supervisor_logs_end_on_crash(tmp_path):
    sup, s, log = _sup(tmp_path, ["raise"])
    with pytest.raises(KeyboardInterrupt):
        sup.run()
    assert _lines(log)[-1]["type"] == "session_end"


def test_run_harness_help():
    r = subprocess.run([sys.executable, "scripts/run_harness.py", "--help"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "--max-steps" in r.stdout
