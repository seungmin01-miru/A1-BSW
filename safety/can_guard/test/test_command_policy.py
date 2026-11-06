"""상태별 명령 생성 로직 — 실제 클램프·인코딩 없이 "무엇을 만들어내는가"만 확인. (2026-10-06 실차 0x210 기준)"""
import pytest

from command_policy import BrakeProfile, BrakeRamp, command_for_state
from plausibility import RateLimiter
from protocol import Command
from state_machine import State

AUTO = dict(steer_auto=True, brake_auto=True, acc_auto=True)


def _run(state, last, held=0.0, steer=None, acc=None, brake=None, dt=0.02, **kw):
    return command_for_state(state, last, held, steer or RateLimiter(), acc or RateLimiter(),
                             brake or BrakeRamp(), dt, **kw)


HOLD = BrakeProfile(30.0, 20.0, 60.0)
ESTOP = BrakeProfile(30.0, 40.0, 82.0)


def test_init_silent_sends_nothing():
    """2026-11-04: auto 000 은 페일세이프 브레이크를 푼다(11-03) → 기본 INIT 은 아예 안 보낸다."""
    assert _run(State.INIT, Command(steer_cmd_deg=99, acc_cmd_pct=50, **AUTO)) is None


def test_init_legacy_is_manual_and_zero():
    out = _run(State.INIT, Command(steer_cmd_deg=99, acc_cmd_pct=50, **AUTO), init_mode='legacy')
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


def test_holding_freezes_steer_zeroes_acc_steps_then_ramps_brake():
    """2026-11-04: 진입 즉시 30 %, 명령이 안 돌아오면 20 %/s 로 60 % 까지."""
    brake = BrakeRamp()
    last = Command(steer_cmd_deg=20.0, brake_cmd_pct=0.0, acc_cmd_pct=8.0, **AUTO)
    _run(State.ACTIVE, last, brake=brake)                       # ACTIVE 에서 브레이크 기준 0 으로 리셋됨
    out = _run(State.HOLDING, Command(steer_cmd_deg=999.0, acc_cmd_pct=8.0, **AUTO), held=20.0,
               brake=brake, dt=0.1, hold_profile=HOLD)
    assert out.steer_cmd_deg == 20.0       # 진입 순간 값에 고정(last 가 이상해도 held 기준)
    assert out.acc_cmd_pct == 0.0
    assert out.brake_cmd_pct == pytest.approx(32.0)  # 30 으로 바로 + 0.1 s × 20 %/s
    for _ in range(40):
        out = _run(State.HOLDING, last, held=20.0, brake=brake, dt=0.1, hold_profile=HOLD)
    assert out.brake_cmd_pct == pytest.approx(60.0)  # 상한에서 멈춤


def test_holding_never_reduces_a_stronger_brake():
    brake = BrakeRamp()
    last = Command(brake_cmd_pct=70.0, **AUTO)
    _run(State.ACTIVE, last, brake=brake)
    out = _run(State.HOLDING, last, brake=brake, dt=0.1, hold_profile=HOLD)
    assert out.brake_cmd_pct == 70.0       # 상한 60 보다 세게 밟고 있었으면 줄이지 않는다


def test_auto_off_command_is_not_sent_by_default():
    """2026-11-04: 어댑터 disarm·lift_cmd auto off 의 auto 000 은 페일세이프 브레이크를 풀므로 송신 안 함."""
    off = Command(steer_cmd_deg=3.0, brake_cmd_pct=0.0)
    for st in (State.ACTIVE, State.HOLDING, State.DEGRADED, State.STOPPED):
        assert _run(st, off) is None
    assert _run(State.ACTIVE, off, auto_off_mode='send') == off
    assert _run(State.STOPPED, off, stopped_mode='manual') == Command()   # 명시적 "사람에게 넘김"은 유지


def test_partial_auto_is_still_sent():
    out = _run(State.ACTIVE, Command(steer_auto=True, steer_cmd_deg=4.0))
    assert out is not None and out.steer_auto and not out.brake_auto


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
    brake = BrakeRamp()
    brake.reset(60.0)
    last = Command(steer_cmd_deg=7.0, acc_cmd_pct=4.0, **AUTO)
    out = _run(State.STOPPED, last, held=7.0, brake=brake, stopped_mode='hold', hold_profile=HOLD)
    assert (out.steer_auto, out.brake_auto, out.acc_auto) == (True, True, True)
    assert out.brake_cmd_pct == 60.0 and out.acc_cmd_pct == 0.0 and out.steer_cmd_deg == 7.0


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


# --- ESTOP (2026-11-04, bsw_test 00 SAF-ESTOP) ---

def test_estop_zeroes_acc_immediately_even_with_rate_limit():
    acc = RateLimiter(max_delta_per_s=1.0)
    acc.step(20.0, dt_s=0.02)
    out = _run(State.ESTOP, Command(acc_cmd_pct=20.0, steer_cmd_deg=5.0, **AUTO), held=5.0, acc=acc,
               estop_profile=ESTOP)
    assert out.acc_cmd_pct == 0.0


def test_estop_brake_starts_then_ramps_gradually_to_max():
    """휠락 방지(사용자 결정): 계단 100 % 가 아니라 30 % → 40 %/s → 82 %."""
    brake = BrakeRamp()
    last = Command(steer_cmd_deg=-12.0, acc_cmd_pct=15.0, **AUTO)
    _run(State.ACTIVE, last, brake=brake)
    outs = [_run(State.ESTOP, last, held=-12.0, brake=brake, dt=0.1, estop_profile=ESTOP) for _ in range(20)]
    assert outs[0].brake_cmd_pct == pytest.approx(34.0)
    assert outs[5].brake_cmd_pct == pytest.approx(54.0)
    assert outs[-1].brake_cmd_pct == pytest.approx(82.0)
    assert all(b.brake_cmd_pct <= a.brake_cmd_pct + 4.0 + 1e-9 for a, b in zip(outs, outs[1:]))
    assert all(o.steer_cmd_deg == -12.0 for o in outs)


def test_estop_keeps_auto_bits_never_000():
    out = _run(State.ESTOP, Command(steer_cmd_deg=1.0, **AUTO), held=1.0, estop_profile=ESTOP)
    assert (out.steer_auto, out.brake_auto, out.acc_auto) == (True, True, True)


def test_estop_without_any_auto_sends_nothing():
    """제어권을 받은 적 없이(INIT 에서) 비상 정지 → auto 000 을 보내면 브레이크가 풀리므로 송신 안 함."""
    assert _run(State.ESTOP, Command(), estop_profile=ESTOP) is None


def test_estop_continues_from_stronger_hold_brake():
    brake = BrakeRamp()
    brake.reset(50.0)   # HOLDING 에서 이미 50 % 까지 올라와 있었음
    out = _run(State.ESTOP, Command(**AUTO), brake=brake, dt=0.1, estop_profile=ESTOP)
    assert out.brake_cmd_pct == pytest.approx(54.0)


def test_brake_never_drops_after_estop_reset_to_stopped():
    brake = BrakeRamp()
    brake.reset(82.0)
    out = _run(State.STOPPED, Command(**AUTO), brake=brake, hold_profile=HOLD)
    assert out.brake_cmd_pct == 82.0   # STOPPED 상한(60)이 더 낮아도 줄이지 않는다


def test_brake_profile_rejects_bad_values():
    with pytest.raises(ValueError):
        BrakeProfile(90.0, 10.0, 60.0)


# --- 2026-11-05: ESTOP 중 원격(앱) En=1 동안 조향 고정값이 실제 핸들각을 따라감 ---------------------------------------
from command_policy import held_steer_follow_remote  # noqa: E402


def test_estop_held_steer_follows_actual_while_remote_enabled():
    """11-04 bsw4: 앱 En 동안 차량은 앱(0°)을 따랐고, 해제 순간 우리 진입값 −49.8° 로 튀었다 → 실제값을 따라간다."""
    assert held_steer_follow_remote(State.ESTOP, -49.8, True, 1.9) == 1.9


def test_estop_held_steer_kept_when_remote_released_or_unknown():
    assert held_steer_follow_remote(State.ESTOP, 1.9, False, -30.0) == 1.9      # 해제 뒤 = 마지막 값 고정
    assert held_steer_follow_remote(State.ESTOP, -49.8, True, None) == -49.8    # 0x200 없음
    assert held_steer_follow_remote(State.ESTOP, -49.8, True, 1.9, mode='hold') == -49.8


def test_only_estop_follows_remote():
    for st in (State.ACTIVE, State.HOLDING, State.STOPPED, State.DEGRADED):
        assert held_steer_follow_remote(st, -10.0, True, 5.0) == -10.0


def test_estop_output_uses_followed_steer():
    held = held_steer_follow_remote(State.ESTOP, -49.8, True, 2.0)
    out = _run(State.ESTOP, Command(steer_cmd_deg=-49.8, acc_cmd_pct=80, **AUTO), held=held, estop_profile=ESTOP)
    assert out.steer_cmd_deg == pytest.approx(2.0) and out.acc_cmd_pct == 0


# --- 2026-11-05: ESTOP·STOPPED 는 disarm(auto 000 명령)으로 풀리지 않는다 ---------------------------------------------
def test_estop_ignores_disarm_and_keeps_auto_and_brake():
    armed = Command(steer_cmd_deg=-10.0, brake_cmd_pct=0, acc_cmd_pct=40, **AUTO)
    disarmed = Command()   # 어댑터 disarm = auto 000·명령 0
    ramp = BrakeRamp()
    _run(State.ESTOP, armed, held=-10.0, brake=ramp, estop_profile=ESTOP, dt=0.5, last_auto_cmd=armed)
    out = _run(State.ESTOP, disarmed, held=-10.0, brake=ramp, estop_profile=ESTOP, dt=0.5, last_auto_cmd=armed)
    assert out is not None and (out.steer_auto, out.brake_auto, out.acc_auto) == (True, True, True)
    assert out.acc_cmd_pct == 0 and out.brake_cmd_pct >= 30 and out.steer_cmd_deg == pytest.approx(-10.0)


def test_stopped_hold_ignores_disarm():
    armed = Command(steer_cmd_deg=3.0, **AUTO)
    out = _run(State.STOPPED, Command(), held=3.0, last_auto_cmd=armed)
    assert out is not None and out.brake_auto and out.brake_cmd_pct >= 30


def test_holding_disarm_still_silent_by_design():
    """HOLDING 은 명령 재개 시 ACTIVE 로 가는 설계 — 상태머신이 ACTIVE 로 바꾼 뒤 침묵. 여기선 ACTIVE 에서 침묵만 확인."""
    armed = Command(**AUTO)
    assert _run(State.ACTIVE, Command(), last_auto_cmd=armed) is None


def test_never_armed_estop_still_sends_nothing():
    assert _run(State.ESTOP, Command(), last_auto_cmd=None, estop_profile=ESTOP) is None


# --- 복구 구간 관성 주행(2026-11-06, 계획서 8.2) -----------------------------------------------------------------------
def test_recovery_coast_zero_brake_zero_accel_steer_held():
    armed = Command(steer_auto=True, brake_auto=True, acc_auto=True, steer_cmd_deg=5.0, acc_cmd_pct=40.0, brake_cmd_pct=20.0)
    ramp = BrakeRamp()
    ramp.reset(20.0)
    acc = RateLimiter(10.0)
    acc.reset(40.0)
    out = command_for_state(State.HOLDING, armed, 7.5, RateLimiter(), acc, ramp, 0.02, recovery_coast=True)
    assert (out.steer_cmd_deg, out.acc_cmd_pct, out.brake_cmd_pct) == (7.5, 0.0, 0.0)
    assert out.steer_auto and out.brake_auto and out.acc_auto
    assert ramp.value == 0.0
    # 정지 확정: 기존 hold 램프가 시작값부터
    out = command_for_state(State.HOLDING, armed, 7.5, RateLimiter(), acc, ramp, 0.02, recovery_coast=False)
    assert out.brake_cmd_pct >= 30.0 and out.acc_cmd_pct == 0.0


def test_recovery_coast_keeps_silence_rule_and_other_states():
    off = Command(steer_cmd_deg=5.0)
    assert command_for_state(State.HOLDING, off, 7.5, RateLimiter(), RateLimiter(), BrakeRamp(), 0.02, recovery_coast=True) is None
    armed = Command(steer_auto=True, brake_auto=True, acc_auto=True, acc_cmd_pct=40.0)
    # HOLDING 이 아닌 상태에는 영향 없음
    for st in (State.ACTIVE, State.ESTOP, State.STOPPED, State.DEGRADED):
        a = command_for_state(st, armed, 1.0, RateLimiter(), RateLimiter(), BrakeRamp(), 0.02)
        b = command_for_state(st, armed, 1.0, RateLimiter(), RateLimiter(), BrakeRamp(), 0.02, recovery_coast=True)
        assert a == b
