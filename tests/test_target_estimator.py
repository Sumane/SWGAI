"""Trilateration of the waypoint from (position, distance, arrow) readings."""
import math

from jedai.navigation.target_estimator import TargetEstimator, arrow_bearing

ARROWS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def arrow_for(pos, yaw, target):
    """HUD arrow, clockwise from facing (measured SWG convention)."""
    b = math.atan2(target[1] - pos[1], target[0] - pos[0])
    rel = (yaw - b) % (2 * math.pi)
    return ARROWS[int((math.degrees(rel) + 22.5) // 45) % 8]


def drive(target, yaw_deg, metres, turn_deg_per_m=0.0, lag_m=8.0):
    """Drive with integer distances and coordinates lagging lag_m behind (like a swoop)."""
    est = TargetEstimator()
    x = z = 0.0
    yaw = math.radians(yaw_deg)
    hist = [(0.0, 0.0)]
    for _ in range(int(metres / 2)):
        yaw += math.radians(turn_deg_per_m) * 2
        x, z = x + 2 * math.cos(yaw), z + 2 * math.sin(yaw)
        hist.append((x, z))
        shown = hist[max(0, len(hist) - 1 - int(lag_m / 2))]
        est.add(shown, round(math.hypot(target[0] - x, target[1] - z)),
                arrow_for((x, z), yaw, target), (math.cos(yaw), math.sin(yaw)))
    e = est.solve()
    return est, math.hypot(e[0] - target[0], e[1] - target[1])


def test_arrow_bearing_is_clockwise_from_heading():
    east = (1.0, 0.0)
    assert arrow_bearing(east, "N") == 0
    assert math.degrees(arrow_bearing(east, "E")) == -90        # clockwise: east -> south
    assert arrow_bearing(None, "N") is None


def test_close_target_is_pinned_down():
    est, err = drive((300.0, 100.0), 0, 150)
    assert err < 15 and est.confident()


def test_far_target_on_a_curve_within_100m():
    est, err = drive((2900.0, 0.0), 40, 600, turn_deg_per_m=-0.1)
    assert err < 100


def test_target_20_degrees_off_found_after_a_straight_drive():
    est, err = drive((-1500.0, 2600.0), 80, 300)
    assert err < 20 and est.confident()


def test_confidence_is_honest():
    """Never confident while actually wrong (the first version said True at 756 m out)."""
    for target, yaw, metres, turn in [((2900.0, 0.0), 15, 300, 0.0), ((2900.0, 0.0), 0, 600, 0.0),
                                      ((2000.0, 2000.0), 20, 150, 0.05), ((-1500.0, 2600.0), 100, 150, 0.0)]:
        est, err = drive(target, yaw, metres, turn)
        assert not est.confident() or err < 4 * 25, (target, yaw, err, est.sigma_m)


def test_readings_need_spacing():
    est = TargetEstimator(min_spacing_m=5)
    assert est.add((0, 0), 100)
    assert not est.add((1, 0), 99)
    assert est.add((6, 0), 94)
