"""상태별 명령 생성 로직 — 실제 클램프·인코딩 없이 "무엇을 만들어내는가"만 확인. (2026-10-06 실차 0x210 기준)"""
import pytest

from command_policy import command_for_state
from plausibility import RateLimiter
from protocol import Command
from state_machine import State

AUTO = dict(steer_auto=True, brake_auto=True, acc_auto=True)


def _run(state, last, held=0.0, steer=None, acc=None, brake=None, dt=0.02, **kw):
    return command_for_state(state, last, held, steer or RateLimiter(), acc or RateLimiter(),
                             brake or RateLimiter(30.0), dt, **kw)


def test_init_is_manual_and_zero():
    out = _run(State.INIT, Command(steer_cmd_deg=99, acc_cmd_pct=50, **AUTO))
    assert out == Command()   # auto 전부 0, 명령 0


def test_active_passes_through_last_valid_cmd():
    last = Command(steer_cmd_deg=10.0, brake_cmd_pct=0.0, acc_cmd_pct=5.0, **AUTO)
    out = _run(State.ACTIVE, last)
    assert out == last


def test_active_applies_steer_rate_limit():
    rl = RateLimiter(max_delta_per_s=10.0)
    rl.step(0.0, dt_s=0.02)
    out = _run(State.ACTIVE, Command(steer_cmd_deg=100.0, **AUTO), steer=rl, dt=0.1)
    assert out.steer_cmd_deg == 1.0   # 0.1 s × 10 deg/s


def test_holding_freezes_steer_zeroes_acc_ramps_brake():
    brake = RateLimiter(30.0)
    last = Command(steer_cmd_deg=20.0, brake_cmd_pct=0.0, acc_cmd_pct=8.0, **AUTO)
    _run(State.ACTIVE, last, brake=brake)                       # ACTIVE 에서 브레이크 기준 0 으로 리셋됨
    out = _run(State.HOLDING, Command(steer_cmd_deg=999.0, acc_cmd_pct=8.0, **AUTO), held=20.0,
               brake=brake, dt=0.1)
    assert out.steer_cmd_deg == 20.0       # 진입 순간 값에 고정(last 가 이상해도 held 기준)
    assert out.acc_cmd_pct == 0.0
    assert out.brake_cmd_pct == pytest.approx(3.0)   # 0 → 30 % 방향으로 0.1 s × 30 %/s
    for _ in range(20):
        out = _run(State.HOLDING, last, held=20.0, brake=brake, dt=0.1)
    assert out.brake_cmd_pct == pytest.approx(30.0)  # 유지값에서 멈춤


def test_holding_never_reduces_a_stronger_brake():
    brake = RateLimiter(30.0)
    last = Command(brake_cmd_pct=50.0, **AUTO)
    _run(State.ACTIVE, last, brake=brake)
    out = _run(State.HOLDING, last, brake=brake, dt=0.1)
    assert out.brake_cmd_pct == 50.0       # 유지값 30 보다 세게 밟고 있었으면 줄이지 않는다


def test_holding_keeps_auto_bits_as_given():
    """can_guard 는 제어 노드가 넘겨주지 않은 축의 제어권을 스스로 가져가지 않는다."""
    last = Command(steer_auto=True, brake_auto=False, acc_auto=True, steer_cmd_deg=5.0)
    out = _run(State.HOLDING, last, held=5.0)
    assert (out.steer_auto, out.brake_auto, out.acc_auto) == (True, False, True)


def test_degraded_same_mechanism_as_holding():
    last = Command(steer_cmd_deg=15.0, acc_cmd_pct=6.0, **AUTO)
    d = _run(State.DEGRADED, last, held=15.0)
    h = _run(State.HOLDING, last, held=15.0)
    a = _run(State.ACTIVE, last)
    assert d == h and d != a
    assert d.acc_cmd_pct == 0.0 and d.steer_cmd_deg == 15.0


def test_stopped_hold_mode_keeps_auto_and_brake():
    brake = RateLimiter(30.0)
    brake.reset(30.0)
    last = Command(steer_cmd_deg=7.0, acc_cmd_pct=4.0, **AUTO)
    out = _run(State.STOPPED, last, held=7.0, brake=brake, stopped_mode='hold')
    assert (out.steer_auto, out.brake_auto, out.acc_auto) == (True, True, True)
    assert out.brake_cmd_pct == 30.0 and out.acc_cmd_pct == 0.0 and out.steer_cmd_deg == 7.0


def test_stopped_manual_mode_hands_back():
    out = _run(State.STOPPED, Command(steer_cmd_deg=7.0, **AUTO), stopped_mode='manual')
    assert out == Command()


def test_init_resets_limiters():
    rl = RateLimiter(max_delta_per_s=10.0)
    rl.step(140.0, dt_s=0.02)
    _run(State.INIT, Command(), steer=rl)
    out = _run(State.ACTIVE, Command(steer_cmd_deg=1.0, **AUTO), steer=rl, dt=0.1)
    assert out.steer_cmd_deg == 1.0   # 0 근처에서 시작 → 0.1 × 10 = 1.0 이내라 그대로


def test_held_steer_captured_on_direct_active_to_stopped():
    """2026-10-06 리허설 버그: 정지 상태에서 ACTIVE → STOPPED 직행 시에도 그 순간 조향각을 캡처해야 한다."""
    from command_policy import held_steer_on_transition
    last = Command(steer_cmd_deg=12.0, **AUTO)
    assert held_steer_on_transition(State.ACTIVE, State.STOPPED, 0.0, last) == 12.0
    assert held_steer_on_transition(State.ACTIVE, State.HOLDING, 0.0, last) == 12.0
    assert held_steer_on_transition(State.INIT, State.DEGRADED, 0.0, last) == 12.0


def test_held_steer_kept_between_hold_states():
    """DEGRADED → HOLDING → STOPPED 사이에는 처음 값 유지(그 사이 들어온 신뢰 못 할 명령으로 갱신 금지)."""
    from command_policy import held_steer_on_transition
    later = Command(steer_cmd_deg=-40.0, **AUTO)
    assert held_steer_on_transition(State.DEGRADED, State.HOLDING, 12.0, later) == 12.0
    assert held_steer_on_transition(State.HOLDING, State.STOPPED, 12.0, later) == 12.0
    assert held_steer_on_transition(State.ACTIVE, State.ACTIVE, 12.0, later) == 12.0
