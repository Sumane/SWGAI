import numpy as np

from jedai.config import load_settings
from jedai.navigation.runner import NavigationRunner
from jedai.perception.hud import Waypoint
from jedai.perception.screen import safe_crop
from jedai.perception.text import assemble_digits, fuzzy_match, parse_distance


def test_text_helpers():
    assert assemble_digits([(30, "4"), (10, "-"), (20, "1")]) == "-14"
    assert assemble_digits([]) is None
    assert parse_distance("0123m") == 123 and parse_distance("99999") is None
    assert fuzzy_match("Theed Starport", "theed starport.") and not fuzzy_match("Theed", None)


def test_safe_crop():
    img = np.zeros((10, 10, 3), np.uint8)
    assert safe_crop(img, -5, -5, 4, 4).shape == (4, 4, 3)
    assert safe_crop(img, 20, 20, 30, 30) is None


class FakeHud:
    def __init__(self):
        self.coords = [(100.0, 5.0, 200.0), (400.0, 5.0, 200.0), (None, None, None)]
        self.wps = [Waypoint("Theed", 120, (0, 0), "N"), Waypoint("Theed", 900, (0, 9), "S"),
                    Waypoint("Moenia", 50, (0, 5), "E")]

    def read_coords(self, frame):
        return self.coords.pop(0)

    def waypoints(self, frame, should_stop=None):
        return iter(self.wps)


class FakeScreen:
    def grab(self):
        return np.zeros((4, 4, 3), np.uint8)


def make_runner(tmp_path):
    s = load_settings(local_file="none.yaml")
    s.raw["paths"]["nav_db"] = str(tmp_path / "nav.db")
    s.raw["paths"]["command_file"] = str(tmp_path / "cmd.json")
    return NavigationRunner(s, hud=FakeHud(), screen=FakeScreen())


def test_coordinate_jump_filter(tmp_path):
    r = make_runner(tmp_path)
    assert r.update_coords(None)[0] == 100.0
    assert r.update_coords(None)[0] == 100.0     # 300m jump rejected
    assert r.update_coords(None)[0] == 100.0     # failed read keeps last


def test_target_status_prefers_consistent_distance(tmp_path):
    r = make_runner(tmp_path)
    r.target_name, r.last_valid_dist = "Theed", 130
    arrow, dist, _ = r.target_status(None)
    assert (arrow, dist) == ("N", 120)


def test_status_line_without_coords(tmp_path):
    """The original crashed formatting Pos when coords weren't read."""
    r = make_runner(tmp_path)
    line = r.status_line("N", 120, 3.0, 0.2, None, None)
    assert "Pos:(?,?)" in line
