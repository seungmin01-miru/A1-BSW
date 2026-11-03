"""범위 클램프·운용 한계·가속/브레이크 인터록·RateLimiter — 2026-10-06 실차 0x210 기준."""
import pytest

from plausibility import DBC_LIMITS, Limits, RateLimiter, clamp_range
from protocol import Command


def test_inside_range_untouched():
    v = []
    cmd = Command(steer_auto=True, steer_cmd_deg=-20.0, brake_cmd_pct=0.0, acc_cmd_pct=5.0)
    assert clamp_range(cmd, violations_out=v) == cmd
    assert v == []


def test_dbc_range():
    v = []
    out = clamp_range(Command(steer_cmd_deg=-999.0, brake_cmd_pct=150.0), violations_out=v)
    assert out.steer_cmd_deg == -150.0 and out.brake_cmd_pct == 100.0
    assert {x.field for x in v} == {'steer_cmd_deg', 'brake_cmd_pct'}


def test_operating_limits_lift_test():
    lim = Limits(steer_abs_deg=30, brake_max_pct=60, acc_max_pct=10)
    v = []
    out = clamp_range(Command(steer_cmd_deg=45.0, acc_cmd_pct=50.0), limits=lim, violations_out=v)
    assert out.steer_cmd_deg == 30.0 and out.acc_cmd_pct == 10.0
    out = clamp_range(Command(steer_cmd_deg=-45.0, brake_cmd_pct=90.0), limits=lim)
    assert out.steer_cmd_deg == -30.0 and out.brake_cmd_pct == 60.0


def test_limits_cannot_exceed_dbc():
    with pytest.raises(ValueError):
        Limits(steer_abs_deg=151)
    with pytest.raises(ValueError):
        Limits(acc_max_pct=101)
    assert DBC_LIMITS == Limits(150, 100, 100)


def test_acc_and_brake_together_brake_wins():
    v = []
    out = clamp_range(Command(brake_cmd_pct=20.0, acc_cmd_pct=8.0), violations_out=v)
    assert out.brake_cmd_pct == 20.0 and out.acc_cmd_pct == 0.0
    assert [x.field for x in v] == ['acc_with_brake']


def test_negative_pct_clamped_to_zero():
    out = clamp_range(Command(brake_cmd_pct=-3.0, acc_cmd_pct=-1.0))
    assert out.brake_cmd_pct == 0.0 and out.acc_cmd_pct == 0.0


def test_rate_limiter_default_unlimited():
    rl = RateLimiter()
    assert rl.step(0.0, 0.02) == 0.0
    assert rl.step(150.0, 0.02) == 150.0


def test_rate_limiter_limits():
    rl = RateLimiter(max_delta_per_s=30.0)
    rl.reset(0.0)
    assert rl.step(30.0, 0.1) == pytest.approx(3.0)
    assert rl.step(30.0, 0.1) == pytest.approx(6.0)
    assert rl.step(-100.0, 0.1) == pytest.approx(3.0)
