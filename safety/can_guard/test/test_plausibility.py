"""범위 클램프·변화율 제한 단위테스트."""
from plausibility import (ACC_CMD_MAX, ACC_CMD_MIN, EPS_CMD_MAX, EPS_CMD_MIN, RateLimiter, Violation,
                          clamp_range)
from protocol import Command


def test_in_range_untouched():
    cmd = Command(eps_cmd=10.0, acc_cmd=0.5, eps_speed=100, aeb_decel_value=0.2)
    out = clamp_range(cmd)
    assert out == cmd


def test_eps_cmd_clamped_both_directions():
    assert clamp_range(Command(eps_cmd=EPS_CMD_MAX + 100)).eps_cmd == EPS_CMD_MAX
    assert clamp_range(Command(eps_cmd=EPS_CMD_MIN - 100)).eps_cmd == EPS_CMD_MIN


def test_acc_cmd_clamped():
    assert clamp_range(Command(acc_cmd=ACC_CMD_MAX + 5)).acc_cmd == ACC_CMD_MAX
    assert clamp_range(Command(acc_cmd=ACC_CMD_MIN - 5)).acc_cmd == ACC_CMD_MIN


def test_violations_recorded_only_when_clamped():
    v = []
    clamp_range(Command(eps_cmd=10.0, acc_cmd=0.5), violations_out=v)
    assert v == []
    v = []
    clamp_range(Command(eps_cmd=999.0), violations_out=v)
    assert len(v) == 1
    assert isinstance(v[0], Violation)
    assert v[0].field == 'eps_cmd' and v[0].requested == 999.0 and v[0].clamped == EPS_CMD_MAX


def test_original_command_not_mutated():
    cmd = Command(eps_cmd=999.0)
    clamp_range(cmd)
    assert cmd.eps_cmd == 999.0   # clamp_range 는 새 객체를 돌려준다 — 원본은 그대로


def test_rate_limiter_unlimited_by_default():
    rl = RateLimiter()
    assert rl.step(1000.0, dt_s=0.01) == 1000.0
    assert rl.step(-1000.0, dt_s=0.01) == -1000.0   # 다음 주기에 아무리 크게 바뀌어도 무제한이면 그대로


def test_rate_limiter_first_call_passes_through():
    rl = RateLimiter(max_delta_per_s=10.0)
    assert rl.step(500.0, dt_s=0.01) == 500.0   # 기준값이 없어서 첫 호출은 클램프 대상이 아님


def test_rate_limiter_clamps_large_step():
    rl = RateLimiter(max_delta_per_s=10.0)   # 초당 10 만큼만 허용
    rl.step(0.0, dt_s=0.01)
    out = rl.step(100.0, dt_s=0.1)            # 0.1s 동안 최대 1.0 만 허용
    assert out == 1.0


def test_rate_limiter_clamps_negative_step():
    rl = RateLimiter(max_delta_per_s=10.0)
    rl.step(0.0, dt_s=0.01)
    out = rl.step(-100.0, dt_s=0.1)
    assert out == -1.0


def test_rate_limiter_reset():
    rl = RateLimiter(max_delta_per_s=10.0)
    rl.step(0.0, dt_s=0.01)
    rl.reset(500.0)
    assert rl.step(500.0, dt_s=0.01) == 500.0   # reset 직후는 다시 "첫 호출" 취급
