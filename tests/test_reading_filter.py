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
