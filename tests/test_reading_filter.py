from jedai.navigation.reading_filter import MotionGate


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_extra_digit_misread_is_dropped():
    c = Clock()
    g = MotionGate(10.0, 16.0, clock=c)
    g.reset(887)
    c.t = 2.0
    assert g.check(9887) is None           # would need 4,500 m/s
    assert g.check(8989) is None
    c.t = 4.0
    assert g.check(870) == 870


def test_position_does_not_freeze_after_a_long_gap():
    """In-game: 2 s ticks on a swoop moved >50 m between reads and the old filter froze for good."""
    c = Clock()
    g = MotionGate(10.0, 16.0, clock=c)
    assert g.check((0.0, 0.0)) == (0.0, 0.0)
    c.t = 4.0
    assert g.check((60.0, 0.0)) == (60.0, 0.0)  # 60 m in 4 s on a 16 m/s swoop is fine
    c.t = 8.0
    assert g.check((120.0, 0.0)) == (120.0, 0.0)


def test_recovers_when_the_old_reading_was_the_wrong_one():
    c = Clock()
    g = MotionGate(10.0, 5.0, clock=c)
    g.reset(5000)                               # bad starting value
    for i, v in enumerate((800, 798, 796)):
        c.t = 0.5 * (i + 1)
        out = g.check(v)
    assert out == 796 and g.last == 796


def test_systematic_misreads_need_to_hold_before_they_are_believed():
    """In-game: 887 read as 8987/8989/8985 several reads in a row, between good reads."""
    c = Clock()
    g = MotionGate(10.0, 10.5, recover_secs=8.0, clock=c)
    g.reset(880)
    for i, v in enumerate((8989, 8985, 8987, 878, 8986, 8984, 8983, 876)):
        c.t = 1.0 * (i + 1)
        out = g.check(v)
        assert (out is None) == (v > 5000), (v, out)


def test_extra_digit_from_the_arrow_is_repaired():
    """In-game: 298 m read as 2988 for 8 s; it got accepted and the watchdog stopped the run."""
    from jedai.perception.text import repair_extra_digit
    assert repair_extra_digit(2988, 299, 15) == 298
    assert repair_extra_digit(8987, 887, 15) == 887
    assert repair_extra_digit(8989, 898, 15) == 898
    assert repair_extra_digit(1105, 980, 15) == 1105     # a real change from 3 to 4 digits stays
    assert repair_extra_digit(305, 299, 15) == 305       # same length: untouched


def test_distance_cant_jump_more_than_the_bot_moved():
    """In-game: 898 m -> 78 m (another waypoint's row) accepted after a minute of misreads."""
    from jedai.navigation.reading_filter import DistanceGate
    c = Clock()
    g = DistanceGate(clock=c)
    assert g.check(898, (0, 0)) == 898
    for _ in range(5):
        g.check(900, (0, 0))
    c.t = 60.0
    assert g.check(78, (2, 0)) is None          # moved 2 m: can't be 820 m closer
    for i in range(40):                          # however long it persists
        c.t += 1
        assert g.check(8986, (2, 0)) is None
    assert g.check(880, (15, 0)) == 880          # moved 15 m, 20 m closer: fine


def test_distance_gate_settles_on_a_bad_starting_value():
    from jedai.navigation.reading_filter import DistanceGate
    c = Clock()
    g = DistanceGate(clock=c)
    g.check(8987, (0, 0))                       # the waypoint list itself was misread
    for i in range(10):
        c.t += 1
        out = g.check(887, (0, 0))
    assert out == 887
