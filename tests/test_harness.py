"""Harness v0: world points, state, skills, log, System 2, supervisor. No game needed."""

import json
import math
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

    def chat(self, t):
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
        self.walk_to = None
        self.chats = []            # successive chat_lines() results; last one repeats
        self.mounts = 0

    def navigate(self, target_name=None, expect_dist=None, target_xz=None, stop_dist=None):
        self.navigated.append(target_name)
        self.expect, self.target_xz, self.stop = expect_dist, target_xz, stop_dist
        if self.walk_to is not None and stop_dist is not None:
            self.pos = self.walk_to
        return self.outcome

    def read_position(self):
        return self.pos

    def chat_lines(self):
        if not self.chats:
            return []
        return self.chats.pop(0) if len(self.chats) > 1 else self.chats[0]

    def chat_mark(self):
        return ("ocr", self.chat_lines())

    def chat_since(self, mark):
        from jedai.perception.chat import new_lines
        return new_lines(mark[1], self.chat_lines())

    def mount(self):
        self.mounts += 1
        self.keys.press(FakeSettings.raw["harness"]["mount_key"])
        for c in FakeSettings.raw["harness"]["mount_commands"]:
            self.keys.chat(c)
        self.apply_vehicle("swoop")

    def store_vehicle(self):
        self.keys.press("STORE")

    def apply_vehicle(self, key):
        self.current_vehicle = key
        self.mounted = key not in ("walking", "running")


class FakeSettings:
    planet = "naboo"
    raw = {"harness": {"waypoint_command": "/waypoint {x:.0f} {z:.0f} {label}",
                       "waypoint_settle_s": 0, "mount_key": "F9", "mount_settle_s": 0,
                       "mount_commands": ["/tar spe", "/mount"], "mount_command_gap_s": 0}}
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


def test_navigate_to_passes_expected_distance():
    r, s = FakeRunner(), WorldState(":memory:")
    s.set_position("naboo", 0.0, 0.0)
    NavigateTo(r, FakeSettings(), sleep=lambda _: None).run(s, x=300.0, z=400.0, label="b")
    assert r.expect == 500.0 and r.target_xz == (300.0, 400.0)


OLD = ["you have earned the theed falls badge", "you have entered theed"]
NEW = OLD + ["you have earned the amidala's beach badge"]


def _badge_run(chats, walk_to=(300.0, 395.0)):
    r, s = FakeRunner(pos=(300.0, 360.0)), WorldState(":memory:")      # stopped 40 m out
    r.walk_to, r.chats = walk_to, chats
    s.set_position("naboo", 0.0, 0.0)
    res = NavigateTo(r, FakeSettings(), sleep=lambda _: None).run(s, x=300.0, z=400.0, label="b", radius=10)
    return r, s, res


def test_badge_walk_in_and_confirm():
    r, s, res = _badge_run([OLD, NEW])
    assert (res.status, res.reason) == ("ok", "badge_confirmed") and "beach" in res.data["badge_message"]
    assert r.keys.dismounts and r.stop == 6.0 and s.get("mounted") is False
    assert r.keys.pressed == ["STORE"]                        # speeder put away after getting off
    assert res.data["dist_to_target"] == 5.0


def test_badge_old_message_is_not_confirmation():
    r, s, res = _badge_run([OLD, OLD, OLD])
    assert (res.status, res.reason) == ("failed", "no_badge_message")
    assert r.stop == 3.0                                     # went right onto the spot first


def test_badge_confirmed_on_second_look():
    r, s, res = _badge_run([OLD, OLD, NEW])
    assert res.ok and r.stop == 3.0


def test_badge_without_verification():
    st = FakeSettings()
    st.raw = {"harness": dict(FakeSettings.raw["harness"], verify_badges=False)}
    r, s = FakeRunner(pos=(300.0, 360.0)), WorldState(":memory:")
    r.walk_to = (300.0, 380.0)
    s.set_position("naboo", 0.0, 0.0)
    res = NavigateTo(r, st, sleep=lambda _: None).run(s, x=300.0, z=400.0, label="b", radius=10)
    assert (res.status, res.reason) == ("stuck", "outside_radius")


def test_choose_waypoint_by_distance():
    from jedai.navigation.runner import NavigationRunner
    r = NavigationRunner.__new__(NavigationRunner)
    r.scan_for_waypoints = lambda: [{"name": "Waupoint", "distance": 888}, {"name": "New waupoint", "distance": 446},
                                    {"name": "Theed", "distance": 8983}]
    assert r.choose_waypoint("badge_x", expect_dist=445.3)["distance"] == 446
    assert r.choose_waypoint("badge_x", expect_dist=2000) is None
    assert r.choose_waypoint("Theed")["distance"] == 8983


def test_mount_dismount():
    r, s, st = FakeRunner(), WorldState(":memory:"), FakeSettings()
    s.set("mounted", True)
    m, d = Mount(r, st, sleep=lambda _: None), Dismount(r, st, sleep=lambda _: None)
    assert m.preconditions(s) == "already mounted" and d.preconditions(s) is None
    assert d.run(s).ok and s.get("mounted") is False and r.current_vehicle == "running"
    assert r.keys.pressed == ["STORE"]
    assert d.preconditions(s) == "not mounted"
    r.keys.typed.clear()
    r.keys.pressed.clear()
    assert m.run(s).ok and s.get("mounted") is True and r.keys.pressed == ["F9"] and r.mounts == 1
    assert r.keys.typed == ["/tar spe", "/mount"]
    st2 = FakeSettings()
    st2.raw = {"harness": dict(FakeSettings.raw["harness"], mount_key="")}
    assert Mount(r, st2, sleep=lambda _: None).run(s).reason == "no_mount_key"


def test_registry():
    reg = default_registry(FakeRunner(), WorldState(":memory:"), FakeSettings())
    assert reg.names() == ["clean_waypoints", "dismount", "mount", "navigate_to", "recover_from_death", "travel_to"]
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


def test_badge_collector_mounts_first():
    s = WorldState(":memory:")
    s.set_position("naboo", 0, 0)
    s.set("mounted", False)
    bc = BadgeCollector(WP, can_mount=True)
    assert bc.decide(s).skill == "navigate_to"            # 10 m away: just walk (Marc, 7 Nov)
    s.set_position("naboo", -1000, 0)
    assert bc.decide(s).skill == "mount"
    s.set("mounted", True)
    assert bc.decide(s).skill == "navigate_to"
    s.set("mounted", False)
    s.record_failure("mount", "mount", "x")
    s.record_failure("mount", "mount", "x")
    assert bc.decide(s).skill == "navigate_to"            # mounting keeps failing: go on foot
    assert BadgeCollector(WP).decide(s).skill == "navigate_to"


def test_badge_collector_order_and_skips():
    s = WorldState(":memory:")
    bc = BadgeCollector(WP, skip_keys={"mid"})
    assert bc.decide(s) is None                                   # no position yet
    s.set_position("naboo", 0, 0)
    d = bc.decide(s)
    assert d.args == {"x": 10, "z": 0, "label": "near", "radius": 30} and "10 m" in d.why
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


def test_new_badge_lines_tolerates_ocr_wobble():
    from jedai.perception.chat import new_badge_lines
    before = ["you have earned the theed falls badge", "chat"]
    after = ["you have earned the theed fal1s badge", "chat", "you already have the beach badge."]
    assert new_badge_lines(before, after) == ["you already have the beach badge."]


def test_supervisor_stops_on_skill_exception(tmp_path):
    sup, *_ = _sup(tmp_path, ["ok"], n=3)
    class Boom(Skill):
        name = "fake"
        def run(self, state, **a):
            raise RuntimeError("bug")
    sup.registry = SkillRegistry()
    sup.registry.register(Boom())
    assert sup.run() == "skill_exception" and sup.steps == 1


def test_navigate_to_syncs_mount_state_when_the_run_crashes():
    r, s = FakeRunner(), WorldState(":memory:")
    s.set("mounted", True)
    def crash(**kw):
        r.mounted = False                    # navigator dismounted, then crashed
        raise RuntimeError("db")
    r.navigate = crash
    with pytest.raises(RuntimeError):
        NavigateTo(r, FakeSettings(), sleep=lambda _: None).run(s, x=1.0, z=1.0, label="b")
    assert s.get("mounted") is False


def test_problem_area_registry(tmp_path):
    from jedai.navigation.grid_db import NavDatabase
    db = NavDatabase(str(tmp_path / "n.db"))
    aid = db.add_area("naboo", -4897, 4016, 120, height=10, note="boxed in\n")
    assert db.find_area("naboo", -4850, 4050)["id"] == aid
    assert db.find_area("naboo", -4700, 4016) is None and db.find_area("tatooine", -4897, 4016) is None
    db.note_area(aid, "again", visit=True)
    db.set_area_status(aid, "multi_level")
    a = db.areas("naboo")[0]
    assert a["visits"] == 2 and a["status"] == "multi_level" and "again" in a["notes"] and a["cy"] == 10
    db.set_area_status(aid, "dismissed")
    assert db.find_area("naboo", -4897, 4016) is None


def test_chat_classify_combat_lines():
    from jedai.perception.chat import classify, new_lines
    before = ["you stand up.", "you have entered theed"]
    after = before + ["you have been poisoned!", "you have been incapacitated by a nightspider aggressor."]
    assert classify(new_lines(before, after)) == "down"
    assert classify(["you were slain by a nightspider aggressor. requesting clone activation..."]) == "dead"
    assert classify(["you're stunned!"]) == "attacked"
    assert classify(["you have entered theed"]) is None


def test_supervisor_stops_when_dead(tmp_path):
    sup, *_ = _sup(tmp_path, ["failed"], n=3)
    class Dies(Skill):
        name = "fake"
        def run(self, state, **a):
            return Result("failed", "dead")
    sup.registry = SkillRegistry()
    sup.registry.register(Dies())
    assert sup.run() == "dead" and sup.steps == 1



def test_chatlog_reads_new_messages(tmp_path):
    from jedai.perception.chatlog import ChatLog, find_chatlog
    p = tmp_path / "profiles" / "Sumane" / "Core3" / "1_chatlog.txt"
    p.parent.mkdir(parents=True)
    p.write_text("\ufeffLogging In [Sat Nov 07 09:26:05 2026] \n")
    assert find_chatlog(str(tmp_path / "profiles/*/Core3/*_chatlog.txt")) == str(p)
    log = ChatLog(str(p))
    assert not log.logging_active()
    mark = log.mark()
    with open(p, "a") as f:
        f.write("[Spatial]  Chat logging ON\n[Combat]  Chat logging ON\n")
        f.write("[Spatial]  You have been incapacitated by a nightspider aggressor.\n")
        f.write("[Combat]  You have been incapacitated by a nightspider aggressor.\n[Spatial]  You st")
    assert log.logging_active()
    assert log.since(mark)[:2] == ["chat logging on", "you have been incapacitated by a nightspider aggressor."]
    new = log.read_new()
    assert new == ["chat logging on", "you have been incapacitated by a nightspider aggressor."]
    with open(p, "a") as f:
        f.write("and up.\n")
    assert log.read_new() == ["you stand up."]           # the half-written line, completed
    with open(p, "a") as f:
        f.write("Logging In [Sun Nov 08 10:00:00 2026] \n")
    assert not log.logging_active()                        # a new session: off again


def test_datapad_waypoint_selection():
    from jedai.harness.skills.datapad import datapad_region, waypoint_labels
    boxes = [("datapad", (570, 230, 640, 246)),
             ("badge_amidal", (850, 330, 910, 344)), ("3563", (840, 330, 870, 344)),
             ("badge-theed f", (920, 330, 985, 344)), ("route_1", (990, 400, 1030, 414)),
             ("badge_amidalas_sandy_beach", (580, 270, 700, 284)),       # details pane: not an icon
             ("badge_amidalas_sandy_beach", (1000, 500, 1120, 514)),     # hover tooltip...
             ("waypoint", (1000, 517, 1050, 531)),                        # ...with "Waypoint" under it
             ("only show waypoints on current planet", (700, 1000, 900, 1014)),
             ("badge_far_away", (2400, 330, 2480, 344))]               # outside the window
    region = datapad_region(boxes, (1440, 2560, 3))
    got = waypoint_labels(boxes, region, ["badge_", "route_"], keep=["badge_theed_falls_bottom"])
    assert [g[0] for g in got] == ["badge_amidal", "route_1"]
    assert got[0][1] == (880, 330 - int(1.6 * 14))                    # icon just above the label


def test_badge_collector_cleans_datapad_after_trips():
    s = WorldState(":memory:")
    s.set("planet", "naboo")
    s.set_position("naboo", 0, 0)
    bc = BadgeCollector(WP, clean_every=2)
    nav = bc.decide(s)
    assert nav.skill == "navigate_to"
    bc.on_result(nav, Result("failed", "stuck"), s)
    assert bc.decide(s).skill == "navigate_to"          # 1 trip: not yet
    bc.on_result(nav, Result("ok"), s)
    clean = bc.decide(s)
    assert clean.skill == "clean_waypoints"
    bc.on_result(clean, Result("failed", "anomaly"), s)  # failure doesn't block the badges
    assert bc.decide(s).skill == "navigate_to"
    # two clean-up failures recently: stop trying for a while
    s.record_failure("clean_waypoints", "clean_waypoints", "anomaly")
    s.record_failure("clean_waypoints", "clean_waypoints", "anomaly")
    s.set("waypoint_trips", 5)
    assert bc.decide(s).skill == "navigate_to"
    assert BadgeCollector(WP, clean_every=0).decide(s).skill == "navigate_to"


def test_clean_waypoints_escapes_and_logs_when_label_returns(tmp_path, monkeypatch):
    import numpy as np
    from jedai.harness.skills import datapad as dp

    monkeypatch.setattr(dp.CleanWaypoints, "_dir", staticmethod(lambda: tmp_path / "datapad"))
    grid = [("datapad", (570, 230, 640, 246)), ("bot_test", (900, 400, 960, 412))]
    confirm = [("really destroy bot_test ?", (1191, 701, 1349, 715))]
    gone = []                                   # Yes "works" but the label stays: a loop

    class Keys:
        def __init__(self):
            self.pressed, self.clicks = [], []
        def combo(self, *k): pass
        def click(self, x, y, b="left"): self.clicks.append((x, y, b))
        def press(self, k): self.pressed.append(k)

    class OCR:
        pass

    seq = iter([grid, [], confirm, grid,            # 1st try: after Yes, still listed
                grid])                              # then the "came back" check
    class Runner:
        keys = Keys()
        class hud:
            class waypoint_reader:
                ocr = OCR()
        class screen:
            @staticmethod
            def grab(): return np.zeros((1440, 2560, 3), np.uint8)

    c = dp.CleanWaypoints(Runner, FakeSettings(), sleep=lambda _: None)
    looks = {"n": 0}
    def fake_look(label="look"):
        looks["n"] += 1
        if label in ("escape", "close"):
            return Runner.screen.grab(), (grid if Runner.keys.pressed == [] else [])
        return Runner.screen.grab(), next(seq, [])
    c._look = fake_look
    c._zoom_ocr = lambda *a, **k: [("4) destroy", (680, 440, 740, 452))]
    res = c.run(WorldState(":memory:"))
    assert not res.ok and res.reason == "anomaly"
    assert "still_listed" in res.data["anomalies"]
    assert (tmp_path / "anomalies" / "index.jsonl").exists()
    # never more than one Yes for the same waypoint
    yes_clicks = [cl for cl in Runner.keys.clicks if cl == (1330, 730, "left")]
    assert len(yes_clicks) == 1


def test_ensure_chatlog_turns_it_back_on(tmp_path):
    """/logChat answered OFF (it was on, the toggle switched it off): type it again."""
    from jedai.navigation.runner import NavigationRunner
    r = NavigationRunner.__new__(NavigationRunner)
    r.s = FakeSettings()
    r.s.raw = {"harness": {"chatlog_watch": True}}
    r._chatlog_state_path = lambda: tmp_path / "state.json"
    r.chatlog = object()
    answers = iter(["off", "on"])
    typed = []
    r._toggle_logchat = lambda: typed.append(1) or next(answers)
    assert r.ensure_chatlog(force=True) is True and len(typed) == 2
    # fresh check: trusted, nothing typed
    assert r.ensure_chatlog() is True and len(typed) == 2


def test_vehicle_wait_reads_countdown(monkeypatch):
    from jedai.navigation import runner as rmod
    r = rmod.NavigationRunner.__new__(rmod.NavigationRunner)
    r.chatlog = None
    r.chat_lines = lambda: ["hello"]
    r.chat_since = lambda mark: ["calling vehicle in 15 seconds. combat will terminate the call."]
    events = []
    r.rec = type("R", (), {"event": lambda self, *a, **k: events.append(k)})()
    slept = []
    clock = {"t": 0.0}
    monkeypatch.setattr(rmod.time, "sleep", lambda s: slept.append(s) or clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(rmod.time, "time", lambda: clock["t"])
    r._wait_for_vehicle(None, {"mount_settle_s": 3.0})
    assert 16 <= sum(slept) <= 17.5 and events[0]["line"].startswith("calling vehicle")


def test_toggle_logchat_reads_state_from_file_growth(tmp_path):
    """Switching ON writes a header to the file at once: growth = now on (8 Nov screenshot)."""
    from jedai.navigation.runner import NavigationRunner
    log = tmp_path / "1_chatlog.txt"
    log.write_text("Logging In [x]\n")
    r = NavigationRunner.__new__(NavigationRunner)
    r.chatlog = type("L", (), {"path": str(log)})()
    r._logchat_answer_on_screen = lambda: None

    class Keys:
        def __init__(self, flush):
            self.flush = flush
        def chat(self, text):
            if self.flush:
                with open(log, "a") as f:
                    f.write("[Spatial]  Chat logging ON\n")
    r.keys = Keys(flush=True)
    assert r._toggle_logchat() == "on"
    r.keys = Keys(flush=False)
    assert r._toggle_logchat() == "off"
    r._logchat_answer_on_screen = lambda: "on"          # the chat window wins when it can be read
    r._chat_anomaly = lambda reason: None
    assert r._toggle_logchat() == "on"


def test_classify_survives_ocr_garble():
    from jedai.perception.chat import classify
    assert classify(["been incapacitated by mauler", "bleeding stopped;"]) == "down"
    assert classify(["you were shatm mauler requesting cone artiuatiog"]) == "dead"
    assert classify(["dizzy", "stunned!"]) == "attacked"
    assert classify(["you have entered theed."]) is None


def test_dstar_walled_in_start_gives_up_fast(tmp_path):
    """No route out: the bounded search must end quickly, not spin to the cap."""
    import time as _t
    from jedai.navigation.dstar_lite import DStarLite
    from jedai.navigation.grid_db import NavDatabase as GridDB
    db = GridDB(str(tmp_path / "g.db"))
    for dx in range(-3, 4):
        for dz in range(-3, 4):
            if max(abs(dx), abs(dz)) == 3:
                db.mark_blocked("t", dx * 5.0, dz * 5.0, 5)
    p = DStarLite(db, "t", search_radius_m=300.0, goal_reach_m=5.0)
    p.set_goal(0.0, 300.0)
    p.set_start(0.0, 0.0)
    t0 = _t.time()
    p.initialise()
    p.update_start(5.0, 0.0)
    assert not p.has_path() and _t.time() - t0 < 5.0
    assert _t.time() - t0 < 2.0
    db.clear_layer("t")
    p = DStarLite(db, "t", search_radius_m=300.0, goal_reach_m=5.0)
    p.set_goal(0.0, 300.0)
    p.set_start(5.0, 0.0)
    p.initialise()
    assert p.has_path()


def test_unseal_forgets_walls_nearby_only(tmp_path):
    from jedai.navigation.dstar_lite import DStarLite
    from jedai.navigation.grid_db import NavDatabase
    db = NavDatabase(str(tmp_path / "g.db"))
    for dx in range(-3, 4):
        for dz in range(-3, 4):
            if max(abs(dx), abs(dz)) == 3:
                db.mark_blocked("t", dx * 5.0, dz * 5.0, 5)
    db.mark_blocked("t", 500.0, 500.0, 5)                 # far away: stays
    n = db.clear_blocked_near("t", 0.0, 0.0, 50.0)
    assert n >= 24
    assert db.clear_blocked_near("t", 500.0, 500.0, 5.0) >= 1
    p = DStarLite(db, "t", search_radius_m=300.0, goal_reach_m=5.0)
    p.set_goal(0.0, 300.0)
    p.set_start(0.0, 0.0)
    p.initialise()
    assert p.has_path()


# --- routes and recovering from death (16 Nov) --------------------------------

class WalkWorld:
    """A character on open ground: FORWARD/BACK move it, LEFT/RIGHT turn it. Whole-metre position reads."""
    def __init__(self, pos, facing_deg, speed=5.0, turn_rate=100.0, sign=-1, walls=()):
        self.x, self.z = pos
        self.a = math.radians(facing_deg)
        self.speed, self.rate, self.sign, self.walls = speed, turn_rate, sign, walls
        self.taps = []

    def tap(self, key, duration=0.0):
        self.taps.append((key, round(duration, 2)))
        if key in ("FORWARD", "BACK"):
            d = self.speed * duration * (1 if key == "FORWARD" else -1)
            nx, nz = self.x + d * math.cos(self.a), self.z + d * math.sin(self.a)
            if not any(x0 <= nx <= x1 and z0 <= nz <= z1 for x0, z0, x1, z1 in self.walls):
                self.x, self.z = nx, nz
        elif key in ("LEFT", "RIGHT"):
            turn = math.radians(self.rate * duration) * (1 if key == "RIGHT" else -1) * self.sign
            self.a += turn

    def release_all(self): pass
    def click(self, x, y, button="left"): self.taps.append(("CLICK", x, y))
    def read(self): return (round(self.x), round(self.z))


THEED_ROUTE = None


def _theed():
    from jedai.data import load_clone_routes
    return load_clone_routes()["naboo"]["theed"]


@pytest.mark.parametrize("facing", [0, 90, 200, 315])
def test_route_follower_walks_the_theed_exit_from_any_facing(facing):
    from jedai.navigation.route import RouteFollower
    r = _theed()
    w = WalkWorld(tuple(r["spawn"]), facing)
    t = {"t": 0.0}
    f = RouteFollower(w.read, w, sleep=lambda s: t.__setitem__("t", t["t"] + s), clock=lambda: t["t"] + 0.5 * len(w.taps))
    outcome, info = f.follow(r["waypoints"])
    assert outcome == "arrived", info
    assert math.hypot(w.x - r["exit"][0], w.z - r["exit"][1]) <= 3.5
    # it went round the corner at the far end, not straight through it
    assert min(p[0] for p in info["track"]) <= -4899


def test_route_follower_gives_up_when_walled_in():
    from jedai.navigation.route import RouteFollower
    w = WalkWorld((0, 0), 0, walls=((-50, 3, 50, 50), (-50, -50, 50, -3), (3, -50, 50, 50), (-50, -50, -3, 50)))
    f = RouteFollower(w.read, w, sleep=lambda s: None, clock=lambda: 0.0, max_stuck=6)
    assert f.follow([(20, 0)])[0] == "stuck"


CLONE_BOXES = [("clone activated", (74, 219, 191, 233)), ("closest:", (88, 246, 130, 258)),
               ("theed", (172, 246, 204, 258)), ("pre-designated: theed", (88, 258, 204, 270)),
               ("cash balance: 9708", (88, 270, 196, 282)), ("closest cloning facility", (88, 353, 203, 366)),
               ("pre-designated facility", (88, 374, 205, 388)), ("ok", (270, 474, 284, 488))]
DECAY_BOXES = [("decay report", (74, 219, 168, 233)), ("decayed items", (88, 353, 170, 366)),
               ("ok", (270, 474, 284, 488))]


def test_clone_window_is_read():
    from jedai.harness.skills.recover import clone_window, ok_button
    w = clone_window(CLONE_BOXES)
    assert (w["closest"], w["pre"]) == ("theed", "theed") and set(w["rows"]) == {"closest", "pre"}
    assert ok_button(CLONE_BOXES, w["title"], (1440, 2560, 3)) == (277, 481)
    assert ok_button([], w["title"], (1440, 2560, 3)) == (277, 481)        # not read: its usual place
    assert clone_window([("datapad", (1, 1, 9, 9))]) is None


def _recover_rig(tmp_path, monkeypatch, screens, start=(-4853, 4033)):
    import numpy as np
    from jedai.harness.skills import recover as rmod
    monkeypatch.setattr(rmod, "ROOT", tmp_path)
    monkeypatch.setattr(rmod, "ocr_boxes", lambda ocr, frame: next(screens_it, []))
    screens_it = iter(screens)
    world = WalkWorld(start, 135)

    class Runner:
        keys = world
        mounted = True
        class hud:
            class waypoint_reader:
                ocr = object()
            @staticmethod
            def read_coords(frame): return (-4853, 1, 4033)
        class screen:
            @staticmethod
            def grab(): return np.zeros((1440, 2560, 3), np.uint8)
        class coord_gate:
            was_reset = False
            @classmethod
            def reset(cls, value=None): cls.was_reset = True
        @staticmethod
        def read_position(): return world.read()
        @staticmethod
        def should_stop(): return False
        @classmethod
        def apply_vehicle(cls, key): cls.mounted = key not in ("walking", "running")
    t = {"t": 0.0}
    skill = rmod.RecoverFromDeath(Runner, FakeSettings(), sleep=lambda s: t.__setitem__("t", t["t"] + s),
                                  clock=lambda: t["t"] + 0.5 * len(world.taps))
    return skill, world, Runner


def test_recover_from_death_clones_and_walks_out(tmp_path, monkeypatch):
    skill, world, runner = _recover_rig(tmp_path, monkeypatch, [CLONE_BOXES, DECAY_BOXES, DECAY_BOXES, []])
    s = WorldState(":memory:")
    s.set_position("naboo", -5000, 3000)
    s.set("mounted", True)
    res = skill.run(s)
    assert res.ok and res.data["city"] == "theed", res
    clicks = [t for t in world.taps if t[0] == "CLICK"]
    assert clicks[0] == ("CLICK", 146, 381)            # the Pre-Designated row
    assert clicks[1] == ("CLICK", 277, 481)            # OK
    assert clicks[2] == ("CLICK", 277, 481)            # the decay report's OK
    assert s.get("mounted") is False and runner.coord_gate.was_reset
    assert s.get("deaths") == 1 and s.get("death_spots")[0]["x"] == -5000
    assert math.hypot(world.x + 4892, world.z - 4045) <= 3.5


def test_recover_dry_run_and_unknown_facility(tmp_path, monkeypatch):
    skill, world, _ = _recover_rig(tmp_path, monkeypatch, [CLONE_BOXES])
    res = skill.run(WorldState(":memory:"), dry_run=True)
    assert res.reason == "dry_run" and res.data["route_known"] and not world.taps
    other = [(t.replace("theed", "keren"), b) for t, b in CLONE_BOXES]
    skill, world, _ = _recover_rig(tmp_path, monkeypatch, [other, [], []])
    res = skill.run(WorldState(":memory:"))
    assert not res.ok and res.reason == "no_route" and res.data["city"] == "keren"
    assert (tmp_path / "data" / "live" / "anomalies" / "index.jsonl").exists()


def test_death_leads_to_recovery_in_the_harness():
    s = WorldState(":memory:")
    s.set("planet", "naboo")
    s.set_position("naboo", 0, 0)
    bc = BadgeCollector(WP)
    s.set("dead", True)
    d = bc.decide(s)
    assert d.skill == "recover_from_death"
    bc.on_result(d, Result("failed", "no_route"), s)
    assert s.get("dead") is True
    bc.on_result(d, Result("ok"), s)
    assert s.get("dead") is False and bc.decide(s).skill == "navigate_to"


# --- travel (16 Nov) -------------------------------------------------------------

def _travel_rig(tmp_path, monkeypatch, start, shuttle_after=1, buy_ok=True, mounted=False):
    import numpy as np
    from jedai.harness.skills import travel as tmod
    monkeypatch.setattr(tmod, "ROOT", tmp_path)
    world = WalkWorld(start, 45)
    world.typed, world.box, world.board_tries = [], [], 0

    def chat(text):
        world.typed.append(text)
        if text.startswith("/purchaseTicket") and buy_ok:
            world.box = [("ticket purchase complete", (392, 340, 518, 353)), ("ok", (599, 427, 613, 441))]
        if text == "/boardShuttle":
            world.board_tries += 1
            if world.board_tries >= shuttle_after:
                dest = text_dest["p"]
                world.x, world.z = dest["x"] + 3, dest["z"] - 2
    world.chat = chat
    orig_click = world.click
    def click(x, y, button="left"):
        orig_click(x, y, button)
        world.box = []
    world.click = click
    world.dismount = lambda *a, **k: world.typed.append("/dismount")
    monkeypatch.setattr(tmod, "ocr_boxes", lambda ocr, frame: list(world.box))
    text_dest = {"p": None}

    class Runner:
        keys = world
        class hud:
            class waypoint_reader:
                ocr = object()
            @staticmethod
            def read_coords(frame): return (round(world.x), 6, round(world.z))
        class screen:
            @staticmethod
            def grab(): return np.zeros((1440, 2560, 3), np.uint8)
        class coord_gate:
            @staticmethod
            def reset(value=None): pass
        @staticmethod
        def read_position(): return world.read()
        @staticmethod
        def should_stop(): return False
        @classmethod
        def apply_vehicle(cls, key): cls.mounted = key not in ("walking", "running")
        @staticmethod
        def store_vehicle(): world.typed.append("STORE")
    Runner.mounted = mounted
    t = {"t": 0.0}
    skill = tmod.TravelTo(Runner, FakeSettings(), sleep=lambda s: t.__setitem__("t", t["t"] + s),
                          clock=lambda: t["t"] + 0.5 * len(world.taps))
    text_dest["p"] = None
    def run(state, to, **kw):
        text_dest["p"] = tmod.match_point(skill.wp["naboo"]["travel"], to)
        return skill.run(state, to=to, **kw)
    return run, world, Runner


def test_travel_buys_and_boards_from_lake_retreat(tmp_path, monkeypatch):
    run, world, _ = _travel_rig(tmp_path, monkeypatch, start=(-5497, -20), shuttle_after=2, mounted=True)
    s = WorldState(":memory:")
    res = run(s, "keren starport")
    assert res.ok, res
    assert world.typed[:2] == ["/dismount", "STORE"]                       # on foot first
    assert "/purchaseTicket naboo The_Lake_Retreat naboo Keren_Starport single" in world.typed
    assert world.typed.count("/boardShuttle") == 2                          # the shuttle wasn't in the first time
    assert ("CLICK", 606, 434) in world.taps                                # OK on "Ticket purchase complete"
    assert s.get("travel_point") == "Keren Starport" and s.position()[1] > 1300
    # it walked to the terminal spot before buying (8 m rule)
    assert any(t[0] == "FORWARD" for t in world.taps)


def test_travel_dry_run_unknown_and_failures(tmp_path, monkeypatch):
    run, world, _ = _travel_rig(tmp_path, monkeypatch, start=(-4991, 4072))
    res = run(WorldState(":memory:"), "The Lake Retreat", dry_run=True)
    assert res.reason == "dry_run" and res.data["from"] == "Theed Shuttle B" and not world.typed
    assert res.data["command"] == "/purchaseTicket naboo Theed_Shuttle_B naboo The_Lake_Retreat single"
    assert run(WorldState(":memory:"), "Mos Eisley").reason == "unknown_destination"
    run, world, _ = _travel_rig(tmp_path, monkeypatch, start=(0, 0))
    assert run(WorldState(":memory:"), "The Lake Retreat").reason == "not_at_a_travel_point"
    run, world, _ = _travel_rig(tmp_path, monkeypatch, start=(-4991, 4072), buy_ok=False)
    res = run(WorldState(":memory:"), "The Lake Retreat")
    assert res.reason == "purchase_failed" and "/boardShuttle" not in world.typed
    assert (tmp_path / "data" / "live" / "anomalies" / "index.jsonl").exists()
    # a starport whose indoor walk isn't recorded yet: buys nothing blind, says what's missing
    run, world, _ = _travel_rig(tmp_path, monkeypatch, start=(1369, 2714))
    res = run(WorldState(":memory:"), "Theed Spaceport")
    assert res.reason == "terminal_too_far" and not world.typed


def test_route_follower_finds_the_way_out_of_a_tube():
    """Boxed in on three sides (a clone tube): sweep round until a step gets somewhere, then go."""
    from jedai.navigation.route import RouteFollower
    w = WalkWorld((0, 0), 90, walls=((-50, -50, -1.5, 50), (1.5, -50, 50, 50), (-50, 1.5, 50, 50)))
    t = {"t": 0.0}
    f = RouteFollower(w.read, w, sleep=lambda s: t.__setitem__("t", t["t"] + s), clock=lambda: t["t"])
    outcome, info = f.follow([(0, -10), (0, -30)])
    assert outcome == "arrived", info
    assert info["actions"] and not any(k == "BACK" for k, *_ in w.taps[:6])   # no backing off while sweeping


def test_clone_window_values_survive_ocr_wobble():
    from jedai.harness.skills.recover import clone_window
    base = [("clone activated", (74, 219, 191, 233)), ("closest cloning facility", (88, 353, 203, 366)),
            ("pre-designated facility", (88, 374, 205, 388))]
    for pre in ("pre-designated: theed", "pre-designated; theed", "predesignated theed", "pre-des gnated: theed",
                "pre designated:theed"):
        w = clone_window(base + [("closest: keren", (88, 246, 204, 258)), (pre, (88, 258, 204, 270))])
        assert (w["closest"], w["pre"]) == ("keren", "theed"), pre
    w = clone_window(base + [("closest:", (88, 246, 130, 258)), ("theed", (172, 246, 204, 258)),
                             ("pre-designated:", (88, 259, 168, 271)), ("keren", (172, 259, 204, 271))])
    assert (w["closest"], w["pre"]) == ("theed", "keren")


def test_clone_window_16_nov_real_read_and_stuck_window(tmp_path, monkeypatch):
    """The real OCR from 16 Nov: title misread, and a chat line that says 'clone activation'."""
    from jedai.harness.skills.recover import clone_ok, clone_window
    real = [("requesting clone activation", (699, 1377, 849, 1391)), ("clone actiyated", (481, 295, 603, 309)),
            ("closest:", (495, 329, 539, 343)), ("theed", (579, 329, 613, 343)),
            ("pre-designated: theed", (495, 341, 611, 353)), ("llosest", (496, 438, 534, 446)),
            ("cloning facility", (532, 435, 613, 451)), ("designated facility", (514, 456, 615, 471))]
    w = clone_window(real)
    assert w["title"] == (481, 295, 603, 309) and (w["closest"], w["pre"]) == ("theed", "theed")
    assert clone_ok(real, w, (1440, 2560, 3)) == (684, 557)
    assert clone_window([("requesting clone activation", (699, 1377, 849, 1391))]) is None   # chat alone: not dead
    # the window never closes: say so, don't walk off
    skill, world, _ = _recover_rig(tmp_path, monkeypatch, [real] * 30)
    res = skill.run(WorldState(":memory:"))
    assert res.reason == "clone_window_wont_close" and not any(t[0] == "FORWARD" for t in world.taps)
    assert sum(1 for t in world.taps if t == ("CLICK", 684, 557)) == 3
