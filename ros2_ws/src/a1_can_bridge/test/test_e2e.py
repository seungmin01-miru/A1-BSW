"""Unit-test AliveCounter — Alive_Cnt continuity/rollover/skip detection (§2 E2E 체크)."""
from a1_can_bridge.e2e import AliveCounter


def test_first_frame_is_ok():
    """First frame has no prior counter to compare against, so it is trivially ok."""
    c = AliveCounter()
    ok, gap = c.update(5)
    assert ok is True
    assert gap == 0
    assert c.frames == 1


def test_normal_sequence():
    """A strictly +1 sequence never reports a skip."""
    c = AliveCounter()
    for v in range(10):
        ok, gap = c.update(v)
        assert ok is True and gap == 0
    assert c.total_skips == 0


def test_rollover_255_to_0_is_ok():
    """255 -> 0 is the expected rollover, not a skip."""
    c = AliveCounter()
    c.update(254)
    c.update(255)
    ok, gap = c.update(0)
    assert ok is True and gap == 0


def test_single_skip_detected():
    """A gap of one missing frame is reported with gap == 1."""
    c = AliveCounter()
    c.update(10)
    ok, gap = c.update(12)   # 11 빠짐
    assert ok is False
    assert gap == 1
    assert c.total_skips == 1


def test_multi_skip_and_interval_pop():
    """Multiple skipped frames accumulate into the interval counter until popped."""
    c = AliveCounter()
    c.update(0)
    c.update(5)    # 1~4 빠짐, gap=4
    c.update(6)    # 정상
    c.update(9)    # 7~8 빠짐, gap=2
    assert c.total_skips == 2
    assert c.pop_interval_skips() == 6   # 4 + 2
    assert c.pop_interval_skips() == 0   # 리셋 확인
