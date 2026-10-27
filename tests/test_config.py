import json

from jedai.config import REPO_ROOT, Settings, load_settings


def test_defaults_match_original_navigator_constants():
    s = load_settings(local_file="does-not-exist.yaml")
    n = s.nav
    # Values copied from navigation_v0_12.py (header v0.20)
    assert (n["stop_dist"], n["verify_readings"], n["final_creep_dist"]) == (50, 5, 20)
    assert (n["ocr_stale_limit"], n["clear_hz"], n["cell_size"]) == (15.0, 2.0, 5.0)
    assert (n["recal_attempt_secs"], n["explore_leg_m"], n["stop_debounce"]) == (3.0, 20.0, 3)
    assert s.planet == "naboo" and s.monitor_index == 2 and s.kill_key == "f8"
    assert s.keys["AUTORUN"] == "numlock" and s.keys["FORWARD"] == "up"
    assert s.planner["cost_unexplored"] == 3.0


def test_local_override_merges(tmp_path):
    local = tmp_path / "local.yaml"
    local.write_text("game:\n  planet: tatooine\npaths:\n  data_dir: /tmp/jedai_data\n")
    s = load_settings(local_file=local)
    assert s.planet == "tatooine"
    assert str(s.path("data_dir")) == "/tmp/jedai_data"
    assert s.monitor_index == 2          # untouched values survive


def test_model_path_resolution(tmp_path):
    s = load_settings(local_file="none.yaml")
    assert s.model_path("minimap") == REPO_ROOT / "models/mini_map_v01.00/weights/best.pt"
    assert s.model_path("wp_distance").parts[-3] == "wp_distance"
    assert s.model_path(str(tmp_path / "x.pt")) == tmp_path / "x.pt"


def test_vehicles_file_is_valid_and_has_navigator_keys():
    s = load_settings(local_file="none.yaml")
    data = json.loads(s.path("vehicles").read_text())   # original file had a trailing comment
    assert {"walking", "running", "speederbike", "swoop"} <= set(data)
    assert s.load_vehicles()["swoop"]["max"] == 16.0


def test_invalid_vehicles_json_warns_and_falls_back(tmp_path, capsys):
    bad = tmp_path / "v.json"
    bad.write_text('{"swoop": {"max": 99}}\n# trailing comment')
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["vehicles"] = str(bad)
    v = s.load_vehicles()
    assert v["swoop"]["max"] == 16.0
    assert "not valid JSON" in capsys.readouterr().out
