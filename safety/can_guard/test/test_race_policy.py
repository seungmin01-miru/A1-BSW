"""apply_race_envelope — 계획서 5절 표 각 행, 안전 축과의 합성, A3·A4, auto 000 불생성 속성 시험."""
import random
from dataclasses import replace

import pytest

from command_policy import BrakeRamp, command_for_state
from plausibility import RateLimiter
from protocol import Command
from race_policy import RaceEnvelopeContext, apply_race_envelope
from race_state import RaceInputs, RaceLimits, RaceParams, RacePhase, RaceState, next_race_state
from state_machine import State

P = RaceParams()
ARMED = dict(steer_auto=True, brake_auto=True, acc_auto=True)
MODEL = Command(steer_cmd_deg=25.0, acc_cmd_pct=40.0, brake_cmd_pct=0.0, **ARMED)
AUTO_000 = Command(steer_cmd_deg=25.0, acc_cmd_pct=40.0, brake_cmd_pct=5.0)
HOLD_SAFETY = [State.HOLDING, State.DEGRADED, State.STOPPED, State.ESTOP]


class StubSlow:
    """speed_ctrl.SlowController 인터페이스 스텁."""

    def __init__(self, acc=7.0, brake=0.0):
        self.acc, self.brake = acc, brake
        self.steps = []
        self.resets = 0

    def step(self, speed, dt):
        self.steps.append((speed, dt))
        return self.acc, self.brake

    def reset(self):
        self.resets += 1


def limits_for(state, **kw):
    kw.setdefault('vehicle_speed_kph', 5.0)
    return next_race_state(state, RaceInputs(now=10.0, **kw), P)[1]


PRE_GO_L = limits_for(RaceState())
RACING_L = limits_for(RaceState(phase=RacePhase.RACING))
SLOW_L = limits_for(RaceState(phase=RacePhase.RACING, slow_active=True, long_override=True))
FINISHED_L = limits_for(RaceState(phase=RacePhase.FINISHED))
FINISHED_SLOW_L = limits_for(RaceState(phase=RacePhase.FINISHED, slow_active=True, long_override=True))
PARKED_L = limits_for(RaceState(phase=RacePhase.PARKED))
SUSPENDED_L = limits_for(RaceState(phase=RacePhase.SUSPENDED))
HOLD_LIMITS = [PRE_GO_L, PARKED_L, SUSPENDED_L]


def ctx(safety=State.ACTIVE, steer=3.0, speed=5.0, last=MODEL, slow=None):
    return RaceEnvelopeContext(safety_state=safety, actual_steer_deg=steer, vehicle_speed_kph=speed, last_auto_cmd=last,
                               slow_ctrl=slow if slow is not None else StubSlow(), dt_s=0.02)


# --- 5절 표 각 행 ------------------------------------------------------------------------------------------------------

def test_table_pre_go():
    r = apply_race_envelope(MODEL, PRE_GO_L, ctx())
    assert r == Command(steer_cmd_deg=3.0, acc_cmd_pct=0.0, brake_cmd_pct=P.pre_go_brake_pct, **ARMED)


def test_table_pre_go_ignores_model_brake():
    r = apply_race_envelope(replace(MODEL, brake_cmd_pct=90.0), PRE_GO_L, ctx())
    assert r.brake_cmd_pct == P.pre_go_brake_pct


def test_table_pre_go_before_arm_is_silent():
    assert apply_race_envelope(None, PRE_GO_L, ctx(last=None)) is None


def test_table_racing_passes_model():
    assert apply_race_envelope(MODEL, RACING_L, ctx()) == MODEL


def test_table_racing_silent_stays_silent():
    assert apply_race_envelope(None, RACING_L, ctx()) is None


def test_table_slow_model_steer_controller_longitudinal():
    slow = StubSlow(acc=7.0, brake=0.0)
    r = apply_race_envelope(replace(MODEL, brake_cmd_pct=50.0), SLOW_L, ctx(slow=slow))
    assert r == Command(steer_cmd_deg=25.0, acc_cmd_pct=7.0, brake_cmd_pct=0.0, **ARMED)
    assert slow.steps == [(5.0, 0.02)]


def test_table_slow_controller_brake():
    r = apply_race_envelope(MODEL, SLOW_L, ctx(slow=StubSlow(acc=0.0, brake=30.0), speed=12.0))
    assert (r.acc_cmd_pct, r.brake_cmd_pct, r.steer_cmd_deg) == (0.0, 30.0, 25.0)


def test_table_slow_off_ramp_caps_acc():
    s, lim, _ = next_race_state(RaceState(phase=RacePhase.RACING, slow_active=True),
                                RaceInputs(now=10.0, events=('slow_off',), last_acc_pct=10.0), P)
    assert apply_race_envelope(MODEL, lim, ctx()).acc_cmd_pct == pytest.approx(10.0)
    s, lim, _ = next_race_state(s, RaceInputs(now=10.2), P)
    r = apply_race_envelope(MODEL, lim, ctx())
    assert r.acc_cmd_pct == pytest.approx(20.0) and r.brake_cmd_pct == 0.0 and r.steer_cmd_deg == 25.0
    s, lim, _ = next_race_state(s, RaceInputs(now=10.7), P)   # 상한 45 % > 모델 40 %
    assert apply_race_envelope(MODEL, lim, ctx()).acc_cmd_pct == pytest.approx(40.0)
    _, lim, _ = next_race_state(s, RaceInputs(now=12.0), P)
    assert apply_race_envelope(MODEL, lim, ctx()) == MODEL


@pytest.mark.parametrize('speed,acc,brake', [
    (15.0, 40.0, 0.0),                  # 상한 아래: 모델 그대로
    (20.0, 40.0, 0.0),                  # 상한 = 아직 안 넘음
    (20.2, 0.0, 0.0),                   # 상한 초과: 가속 차단
    (20.5, 0.0, 0.0),                   # + 0.5 = 아직 안 넘음
    (20.6, 0.0, P.finish_over_brake_pct),   # + 0.5 초과: 브레이크
])
def test_table_finished_cap(speed, acc, brake):
    r = apply_race_envelope(MODEL, FINISHED_L, ctx(speed=speed))
    assert (r.acc_cmd_pct, r.brake_cmd_pct, r.steer_cmd_deg) == (acc, brake, 25.0)


def test_table_finished_model_brake_wins_if_stronger():
    r = apply_race_envelope(replace(MODEL, brake_cmd_pct=70.0), FINISHED_L, ctx(speed=25.0))
    assert r.brake_cmd_pct == 70.0


def test_table_finished_model_can_stop():
    m = replace(MODEL, acc_cmd_pct=0.0, brake_cmd_pct=60.0)
    assert apply_race_envelope(m, FINISHED_L, ctx(speed=5.0)) == m


def test_table_parked():
    r = apply_race_envelope(MODEL, PARKED_L, ctx(speed=0.0))
    assert r == Command(steer_cmd_deg=3.0, acc_cmd_pct=0.0, brake_cmd_pct=P.parked_brake_pct, **ARMED)


def test_table_suspended_follows_actual_steer():
    r = apply_race_envelope(MODEL, SUSPENDED_L, ctx(speed=30.0))
    assert r == Command(steer_cmd_deg=3.0, acc_cmd_pct=0.0, brake_cmd_pct=P.suspended_brake_pct, **ARMED)


# --- 안전 축과 합성: 더 엄격한 쪽 --------------------------------------------------------------------------------------

@pytest.mark.parametrize('lim', [PRE_GO_L, PARKED_L, SUSPENDED_L, RACING_L, SLOW_L, FINISHED_L, FINISHED_SLOW_L],
                         ids=['pre_go', 'parked', 'suspended', 'racing', 'slow', 'finished', 'finished_slow'])
def test_estop_beats_race_state(lim):
    """ESTOP 출력(브레이크 82 %, 가속 0, 조향 고정)은 어느 경기 상태에서도 약해지지 않는다."""
    estop_out = Command(steer_cmd_deg=-49.8, acc_cmd_pct=0.0, brake_cmd_pct=82.0, **ARMED)
    r = apply_race_envelope(estop_out, lim, ctx(safety=State.ESTOP, slow=StubSlow(acc=50.0, brake=0.0), speed=8.0))
    assert r == estop_out


@pytest.mark.parametrize('safety', [State.HOLDING, State.DEGRADED, State.STOPPED])
@pytest.mark.parametrize('lim,floor', [(PRE_GO_L, P.pre_go_brake_pct), (PARKED_L, P.parked_brake_pct),
                                       (SUSPENDED_L, P.suspended_brake_pct)])
def test_hold_safety_brake_is_max_of_both(safety, lim, floor):
    strong = Command(steer_cmd_deg=-5.0, acc_cmd_pct=2.0, brake_cmd_pct=75.0, **ARMED)
    assert apply_race_envelope(strong, lim, ctx(safety=safety)).brake_cmd_pct == 75.0
    weak = replace(strong, brake_cmd_pct=0.0)   # 복구 구간(관성 주행) 출력이어도 경기 브레이크가 산다
    r = apply_race_envelope(weak, lim, ctx(safety=safety))
    assert r.brake_cmd_pct == floor and r.acc_cmd_pct == 0.0


@pytest.mark.parametrize('safety', HOLD_SAFETY)
def test_slow_with_safety_hold_takes_stricter(safety):
    out = Command(steer_cmd_deg=-5.0, acc_cmd_pct=3.0, brake_cmd_pct=40.0, **ARMED)
    r = apply_race_envelope(out, SLOW_L, ctx(safety=safety, slow=StubSlow(acc=20.0, brake=0.0)))
    assert (r.acc_cmd_pct, r.brake_cmd_pct, r.steer_cmd_deg) == (0.0, 40.0, -5.0)
    r = apply_race_envelope(replace(out, brake_cmd_pct=10.0), SLOW_L, ctx(safety=safety, slow=StubSlow(acc=0.0, brake=30.0)))
    assert (r.acc_cmd_pct, r.brake_cmd_pct) == (0.0, 30.0)


@pytest.mark.parametrize('safety', HOLD_SAFETY)
def test_slow_with_coasting_safety_output_keeps_low_acc(safety):
    out = Command(steer_cmd_deg=-5.0, acc_cmd_pct=3.0, brake_cmd_pct=0.0, **ARMED)
    r = apply_race_envelope(out, SLOW_L, ctx(safety=safety, slow=StubSlow(acc=20.0, brake=0.0)))
    assert (r.acc_cmd_pct, r.brake_cmd_pct) == (3.0, 0.0)


@pytest.mark.parametrize('safety', HOLD_SAFETY)
def test_estop_steer_kept_in_pre_go(safety):
    out = Command(steer_cmd_deg=-20.0, acc_cmd_pct=0.0, brake_cmd_pct=82.0, **ARMED)
    r = apply_race_envelope(out, PRE_GO_L, ctx(safety=safety, steer=3.0))
    assert r.steer_cmd_deg == (-20.0 if safety == State.ESTOP else 3.0)


# --- 서행 중 Finish: 모델이 세울 수 있어야 한다 -------------------------------------------------------------------------

def test_finished_slow_model_can_stop():
    m = replace(MODEL, acc_cmd_pct=0.0, brake_cmd_pct=60.0)
    r = apply_race_envelope(m, FINISHED_SLOW_L, ctx(slow=StubSlow(acc=15.0, brake=0.0)))
    assert (r.acc_cmd_pct, r.brake_cmd_pct) == (0.0, 60.0)


def test_finished_slow_acc_never_above_controller():
    r = apply_race_envelope(MODEL, FINISHED_SLOW_L, ctx(slow=StubSlow(acc=7.0, brake=0.0)))
    assert (r.acc_cmd_pct, r.brake_cmd_pct) == (7.0, 0.0)


def test_finished_slow_controller_brake_applies():
    r = apply_race_envelope(MODEL, FINISHED_SLOW_L, ctx(slow=StubSlow(acc=0.0, brake=30.0)))
    assert (r.acc_cmd_pct, r.brake_cmd_pct) == (0.0, 30.0)


# --- 서행 제어기 호출 --------------------------------------------------------------------------------------------------

def test_long_override_start_resets_controller_before_step():
    slow = StubSlow()
    lim = next_race_state(RaceState(phase=RacePhase.RACING), RaceInputs(now=1.0, events=('slow_on',)), P)[1]
    apply_race_envelope(MODEL, lim, ctx(slow=slow))
    assert slow.resets == 1 and len(slow.steps) == 1


def test_no_reset_while_running():
    slow = StubSlow()
    apply_race_envelope(MODEL, SLOW_L, ctx(slow=slow))
    assert slow.resets == 0


def test_silent_in_slow_resets_controller_no_step():
    slow = StubSlow()
    assert apply_race_envelope(None, SLOW_L, ctx(slow=slow)) is None
    assert slow.resets == 1 and slow.steps == []


def test_controller_not_called_outside_override():
    slow = StubSlow()
    for lim in [PRE_GO_L, RACING_L, FINISHED_L, PARKED_L, SUSPENDED_L]:
        apply_race_envelope(MODEL, lim, ctx(slow=slow))
    assert slow.steps == [] and slow.resets == 0


# --- A3 -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('lim', HOLD_LIMITS, ids=['pre_go', 'parked', 'suspended'])
@pytest.mark.parametrize('out', [None, AUTO_000], ids=['silent', 'auto000'])
def test_a3_disarm_keeps_auto_and_brake_if_auto_was_sent(lim, out):
    r = apply_race_envelope(out, lim, ctx(last=MODEL))
    assert r == Command(steer_cmd_deg=3.0, acc_cmd_pct=0.0, brake_cmd_pct=lim.hold_brake_pct, **ARMED)


@pytest.mark.parametrize('lim', HOLD_LIMITS, ids=['pre_go', 'parked', 'suspended'])
@pytest.mark.parametrize('out', [None, AUTO_000, Command()], ids=['silent', 'auto000', 'init_legacy'])
@pytest.mark.parametrize('last', [None, Command()], ids=['never', 'last_no_auto'])
def test_a3_never_sent_auto_is_silent(lim, out, last):
    assert apply_race_envelope(out, lim, ctx(last=last)) is None


def test_a3_end_to_end_with_command_for_state():
    """어댑터 disarm(auto 000) → command_for_state 침묵 → 경기 제한이 브레이크를 유지."""
    steer, acc, ramp = RateLimiter(None), RateLimiter(None), BrakeRamp()
    out = command_for_state(State.ACTIVE, Command(), 0.0, steer, acc, ramp, 0.02, last_auto_cmd=MODEL)
    assert out is None
    r = apply_race_envelope(out, PRE_GO_L, ctx(last=MODEL))
    assert r.brake_auto and r.brake_cmd_pct == P.pre_go_brake_pct


# --- A4 -----------------------------------------------------------------------------------------------------------------

def test_a4_pre_go_no_0x200_is_silent():
    assert apply_race_envelope(MODEL, PRE_GO_L, ctx(steer=None)) is None


def test_a4_parked_no_0x200_is_silent():
    assert apply_race_envelope(MODEL, PARKED_L, ctx(steer=None)) is None


def test_a4_suspended_no_0x200_keeps_brake_not_silent():
    r = apply_race_envelope(MODEL, SUSPENDED_L, ctx(steer=None))
    assert r.brake_cmd_pct == P.suspended_brake_pct and r.steer_cmd_deg == 25.0 and r.acc_cmd_pct == 0.0


@pytest.mark.parametrize('safety', HOLD_SAFETY)
def test_a4_pre_go_no_0x200_with_safety_hold_not_silent(safety):
    out = Command(steer_cmd_deg=-20.0, acc_cmd_pct=0.0, brake_cmd_pct=60.0, **ARMED)
    r = apply_race_envelope(out, PRE_GO_L, ctx(safety=safety, steer=None))
    assert r.steer_cmd_deg == -20.0 and r.brake_cmd_pct == 60.0


def test_a4_slow_speed_unknown():
    slow = StubSlow(acc=20.0, brake=0.0)
    r = apply_race_envelope(MODEL, SLOW_L, ctx(speed=None, slow=slow))
    assert (r.acc_cmd_pct, r.brake_cmd_pct) == (0.0, P.slow_unknown_brake_pct)
    assert slow.steps == [(None, 0.02)]


def test_a4_slow_speed_unknown_controller_brake_higher():
    r = apply_race_envelope(MODEL, SLOW_L, ctx(speed=None, slow=StubSlow(acc=0.0, brake=55.0)))
    assert r.brake_cmd_pct == 55.0


def test_a4_finished_speed_unknown():
    r = apply_race_envelope(MODEL, FINISHED_L, ctx(speed=None))
    assert (r.acc_cmd_pct, r.brake_cmd_pct, r.steer_cmd_deg) == (0.0, 0.0, 25.0)


# --- 속성 시험 -----------------------------------------------------------------------------------------------------------

def _random_cmd(rnd):
    auto = [rnd.random() < 0.6 for _ in range(3)]
    return Command(steer_auto=auto[0], brake_auto=auto[1], acc_auto=auto[2], steer_cmd_deg=rnd.uniform(-150, 150),
                   brake_cmd_pct=rnd.uniform(0, 100), acc_cmd_pct=rnd.uniform(0, 100))


def test_property_auto_000_never_generated_in_hold_phases():
    """PRE_GO·PARKED·SUSPENDED: 어떤 입력에도 auto 000 Command 를 만들지 않고, 나오면 가속 0·브레이크 바닥 이상."""
    rnd = random.Random(20261106)
    outputs = 0
    for _ in range(20000):
        lim = rnd.choice(HOLD_LIMITS)
        out = rnd.choice([None, Command(), _random_cmd(rnd)])
        last = rnd.choice([None, Command(), _random_cmd(rnd)])
        c = ctx(safety=rnd.choice(list(State)), steer=rnd.choice([None, rnd.uniform(-150, 150)]),
                speed=rnd.choice([None, rnd.uniform(0, 60)]), last=last)
        r = apply_race_envelope(out, lim, c)
        if r is None:
            continue
        outputs += 1
        assert r.steer_auto or r.brake_auto or r.acc_auto
        assert r.acc_cmd_pct == 0.0
        assert r.brake_cmd_pct >= lim.hold_brake_pct
    assert outputs > 5000


def test_property_never_weaker_than_safety_output():
    """안전 상태 출력보다 가속이 크거나 브레이크가 약해지지 않는다(전 경기 상태)."""
    rnd = random.Random(1106)
    all_limits = [PRE_GO_L, PARKED_L, SUSPENDED_L, RACING_L, SLOW_L, FINISHED_L, FINISHED_SLOW_L]
    for _ in range(20000):
        out = replace(_random_cmd(rnd), steer_auto=True)
        slow = StubSlow(*rnd.choice([(rnd.uniform(0, 50), 0.0), (0.0, rnd.uniform(0, 80))]))
        r = apply_race_envelope(out, rnd.choice(all_limits),
                                ctx(safety=rnd.choice(HOLD_SAFETY), speed=rnd.choice([None, rnd.uniform(0, 60)]),
                                    steer=rnd.choice([None, 3.0]), slow=slow))
        if r is None:   # A4: 안전 상태에서는 침묵으로 브레이크를 잃지 않는다
            pytest.fail('안전 상태 출력이 있는데 침묵')
        assert r.acc_cmd_pct <= out.acc_cmd_pct
        assert r.brake_cmd_pct >= out.brake_cmd_pct


def test_property_full_state_machine_walk():
    """이벤트를 무작위로 걸어도 PRE_GO·PARKED·SUSPENDED 에서 움직임 허용·auto 000 이 나오지 않는다."""
    rnd = random.Random(7)
    flags = ['go', 'stop', 'finish', 'slow_on', 'slow_off']
    for _ in range(200):
        s = RaceState()
        t = 0.0
        for _ in range(100):
            t += rnd.choice([0.02, 0.02, 1.0, 6.0])
            ev = tuple(f for f in flags if rnd.random() < 0.1)
            s, lim, _ = next_race_state(s, RaceInputs(now=t, events=ev, vehicle_speed_kph=rnd.choice([None, 0.0, 9.0, 25.0]),
                                                      res_active=rnd.random() < 0.01), P)
            assert isinstance(lim, RaceLimits)
            if s.phase in (RacePhase.PRE_GO, RacePhase.PARKED, RacePhase.SUSPENDED):
                assert not lim.allow_motion
                r = apply_race_envelope(rnd.choice([None, Command(), MODEL]), lim, ctx(last=MODEL))
                assert r.brake_auto and r.acc_cmd_pct == 0.0
            else:
                assert lim.allow_motion
