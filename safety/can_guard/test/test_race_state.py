"""경기 상태 기계 — 계획서 4.2절 표의 모든 줄(test_row_NN_*), 우선순위, 신호 방식 3종, 입력 유예, 복귀 램프."""
import itertools

import pytest

from race_signal import RaceSignalDecoder, ResMonitor
from race_state import (RaceInputs, RaceLimits, RaceParams, RacePhase, RaceState, next_race_state, race_phase_code)

P = RaceParams()
GO_RESET = RaceParams(resume_mode='go-reset')
PRE_GO, RACING, FINISHED, PARKED, SUSPENDED = (RacePhase.PRE_GO, RacePhase.RACING, RacePhase.FINISHED,
                                               RacePhase.PARKED, RacePhase.SUSPENDED)


def step(s, *events, now=10.0, params=P, **kw):
    kw.setdefault('vehicle_speed_kph', 0.0)
    return next_race_state(s, RaceInputs(now=now, events=tuple(events), **kw), params)


def names(events):
    return [e[0] for e in events]


def racing(**kw):
    return RaceState(phase=RACING, **kw)


# --- 4.2절 표 --------------------------------------------------------------------------------------------------------

def test_row_01_pre_go_go_to_racing():
    s, lim, ev = step(RaceState(), 'go')
    assert s.phase == RACING and not s.slow_active and lim.allow_motion
    assert ('race_phase', 'PRE_GO->RACING go') in ev


def test_row_01b_pre_go_go_with_slow_memorized_is_racing_slow():
    s, lim, _ = step(RaceState(slow_active=True), 'go')
    assert s.phase == RACING and s.slow_active and lim.long_override and lim.long_override_start


def test_row_02_pre_go_slow_on_is_remembered():
    s, lim, _ = step(RaceState(), 'slow_on')
    assert s.phase == PRE_GO and s.slow_active
    assert not lim.allow_motion and not lim.long_override


def test_row_03_racing_slow_on():
    s, lim, _ = step(racing(), 'slow_on')
    assert s.phase == RACING and s.slow_active and lim.long_override
    assert lim.speed_target_kph == P.slow_target_kph and lim.speed_cap_kph == P.slow_acc_cut_kph


def test_row_04_racing_slow_slow_off_starts_ramp():
    s, lim, _ = step(racing(slow_active=True, long_override=True), 'slow_off', last_acc_pct=12.0)
    assert s.phase == RACING and not s.slow_active and not lim.long_override
    assert lim.acc_ramp_active and lim.acc_ramp_start_pct == pytest.approx(12.0)
    assert lim.acc_cap_pct == pytest.approx(12.0)


def test_row_05_racing_finish_to_finished():
    s, lim, _ = step(racing(), 'finish')
    assert s.phase == FINISHED and lim.speed_cap_kph == P.finish_cap_kph


def test_row_05b_racing_slow_finish_keeps_slow():
    s, lim, _ = step(racing(slow_active=True, long_override=True), 'finish')
    assert s.phase == FINISHED and s.slow_active
    assert lim.long_override and lim.allow_model_stop and lim.speed_cap_kph == P.slow_acc_cut_kph


def test_row_06_finished_stopped_long_enough_to_parked():
    s = RaceState(phase=FINISHED)
    s, _, _ = step(s, now=100.0)
    assert s.phase == FINISHED
    s, _, _ = step(s, now=104.99)
    assert s.phase == FINISHED
    s, lim, ev = step(s, now=105.0)
    assert s.phase == PARKED and not lim.allow_motion and lim.hold_brake_pct == P.parked_brake_pct
    assert ('race_phase', 'FINISHED->PARKED stopped') in ev


def test_row_06b_parked_timer_restarts_when_moving():
    s = RaceState(phase=FINISHED)
    s, _, _ = step(s, now=100.0)
    s, _, _ = step(s, now=104.0, vehicle_speed_kph=1.0)
    s, _, _ = step(s, now=104.5)
    s, _, _ = step(s, now=109.0)
    assert s.phase == FINISHED
    s, _, _ = step(s, now=109.5)
    assert s.phase == PARKED


def test_row_06c_parked_needs_known_speed():
    s = RaceState(phase=FINISHED)
    for t in range(100, 120):
        s, _, _ = step(s, now=float(t), vehicle_speed_kph=None)
    assert s.phase == FINISHED


def test_row_06d_parked_is_latched():
    s = RaceState(phase=PARKED)
    s, _, ev = step(s, 'go', vehicle_speed_kph=5.0)
    assert s.phase == PARKED
    assert ('race_event_ignored', 'go PARKED') in ev


@pytest.mark.parametrize('phase', list(RacePhase))
@pytest.mark.parametrize('kw,reason', [({'res_active': True}, 'res'), ({'res_alive_lost': True}, 'res_alive_lost')])
def test_row_07_any_state_res_to_suspended(phase, kw, reason):
    s, lim, _ = step(RaceState(phase=phase), **kw)
    assert s.phase == SUSPENDED and not lim.allow_motion
    assert phase == SUSPENDED or s.suspend_reason == reason


@pytest.mark.parametrize('phase', list(RacePhase))
def test_row_08_any_state_stop_to_suspended(phase):
    s, lim, _ = step(RaceState(phase=phase), 'stop')
    assert s.phase == SUSPENDED and not lim.allow_motion
    assert lim.hold_brake_pct == P.suspended_brake_pct


@pytest.mark.parametrize('ev', ['go', 'finish', 'slow_off', 'slow_on', 'stop'])
def test_row_09_suspended_restart_mode_never_leaves(ev):
    s = RaceState(phase=SUSPENDED, suspend_reason='res')
    for t in (10.0, 11.0, 12.0):
        s, lim, _ = step(s, ev, now=t)
        assert s.phase == SUSPENDED and not lim.allow_motion


def test_row_09b_suspended_restart_means_new_state_pre_go():
    """재시작 = 새 RaceState — 기본값이 PRE_GO(래치 없음)."""
    assert RaceState().phase == PRE_GO and not RaceState().slow_active


def test_row_10_suspended_go_reset_after_conditions():
    s = RaceState(phase=SUSPENDED, suspend_reason='res')
    s, _, _ = step(s, now=10.0, params=GO_RESET)   # RES=0, STOP=0, 정지 — 조건 만족
    assert s.phase == SUSPENDED and s.reset_ready
    s, lim, ev = step(s, 'go', now=10.02, params=GO_RESET)
    assert s.phase == RACING and lim.allow_motion and s.suspend_reason is None
    assert ('race_phase', 'SUSPENDED->RACING go-reset') in ev


def test_row_10b_go_reset_go_same_cycle_as_conditions_is_not_enough():
    s = RaceState(phase=SUSPENDED, suspend_reason='res')
    s, _, _ = step(s, 'go', now=10.0, params=GO_RESET)
    assert s.phase == SUSPENDED


@pytest.mark.parametrize('kw', [{'res_active': True}, {'res_alive_lost': True}, {'stop_level': True},
                                {'vehicle_speed_kph': 5.0}, {'vehicle_speed_kph': None}])
def test_row_10c_go_reset_blocked_by_each_condition(kw):
    s = RaceState(phase=SUSPENDED, suspend_reason='res')
    s, _, _ = step(s, now=10.0, params=GO_RESET)
    s, _, _ = step(s, 'go', now=10.02, params=GO_RESET, **kw)
    assert s.phase == SUSPENDED and not s.reset_ready


def test_row_10d_go_reset_off_by_default():
    s = RaceState(phase=SUSPENDED, suspend_reason='res')
    s, _, _ = step(s, now=10.0)
    s, _, _ = step(s, 'go', now=10.02)
    assert s.phase == SUSPENDED and not s.reset_ready


def test_row_10e_go_reset_keeps_slow_memory():
    s = RaceState(phase=SUSPENDED, suspend_reason='stop')
    s, _, _ = step(s, 'slow_on', now=10.0, params=GO_RESET)
    s, lim, _ = step(s, 'go', now=10.02, params=GO_RESET)
    assert s.phase == RACING and s.slow_active and lim.long_override


# --- 동시 이벤트 우선순위 stop > finish > slow_on > slow_off > go ------------------------------------------------------

@pytest.mark.parametrize('others', [('finish',), ('slow_on',), ('slow_off',), ('go',), ('finish', 'go', 'slow_off')])
@pytest.mark.parametrize('phase', [PRE_GO, RACING])
def test_priority_stop_beats_everything(phase, others):
    s, lim, _ = step(RaceState(phase=phase), 'stop', *others)
    assert s.phase == SUSPENDED and s.suspend_reason == 'stop' and not lim.allow_motion


def test_priority_finish_beats_go_in_pre_go():
    s, lim, _ = step(RaceState(), 'go', 'finish')
    assert s.phase == FINISHED and lim.speed_cap_kph == P.finish_cap_kph


def test_priority_finish_beats_slow_off():
    s, lim, ev = step(racing(slow_active=True), 'finish', 'slow_off', vehicle_speed_kph=5.0)
    assert s.phase == FINISHED and s.slow_active and lim.long_override and not lim.acc_ramp_active
    assert ('race_event_ignored', 'slow_off FINISHED') in ev


def test_priority_slow_on_beats_slow_off_racing():
    s, lim, _ = step(racing(slow_active=True), 'slow_off', 'slow_on')
    assert s.slow_active and lim.long_override and not lim.acc_ramp_active


def test_priority_slow_on_beats_slow_off_pre_go():
    s, _, _ = step(RaceState(), 'slow_off', 'slow_on')
    assert s.slow_active
    s, _, _ = step(RaceState(slow_active=True), 'slow_off', 'slow_on')   # 이미 기억 중이어도 풀리지 않는다
    assert s.slow_active


def test_priority_slow_on_with_go_is_racing_slow():
    s, lim, _ = step(RaceState(), 'go', 'slow_on')
    assert s.phase == RACING and s.slow_active and lim.long_override


def test_priority_finish_and_slow_on_both_apply():
    s, lim, _ = step(racing(), 'finish', 'slow_on')
    assert s.phase == FINISHED and s.slow_active and lim.allow_model_stop


def test_event_order_does_not_matter():
    for ev in itertools.permutations(['go', 'stop', 'finish', 'slow_on', 'slow_off']):
        assert step(RaceState(), *ev)[0].phase == SUSPENDED
    for ev in itertools.permutations(['go', 'finish', 'slow_on']):
        s = step(RaceState(), *ev)[0]
        assert (s.phase, s.slow_active) == (FINISHED, True)


# --- 표에 없는 줄의 판단(보수적으로) ----------------------------------------------------------------------------------

def test_gap_finish_before_go_is_remembered():
    s, lim, _ = step(RaceState(), 'finish')
    assert s.phase == PRE_GO and s.finish_pending and not lim.allow_motion
    s, _, _ = step(s, 'go')
    assert s.phase == FINISHED and not s.finish_pending


def test_gap_slow_off_in_pre_go_clears_memory_no_ramp():
    s, _, _ = step(RaceState(slow_active=True), 'slow_off')
    assert not s.slow_active and s.ramp_start_t is None
    s, lim, _ = step(s, 'go')
    assert s.phase == RACING and not lim.long_override and not lim.acc_ramp_active


def test_gap_slow_off_in_finished_is_ignored():
    s, lim, ev = step(RaceState(phase=FINISHED, slow_active=True, long_override=True), 'slow_off', vehicle_speed_kph=5.0)
    assert s.slow_active and lim.long_override
    assert ('race_event_ignored', 'slow_off FINISHED') in ev


def test_gap_slow_on_in_finished_applies():
    s, lim, _ = step(RaceState(phase=FINISHED), 'slow_on', vehicle_speed_kph=5.0)
    assert s.slow_active and lim.long_override and lim.allow_model_stop


def test_gap_go_in_racing_is_ignored_without_grace():
    s, lim, ev = step(racing(), 'go')
    assert s.phase == RACING and not lim.input_grace
    assert ('race_event_ignored', 'go RACING') in ev


def test_gap_slow_off_without_slow_is_ignored_no_ramp():
    s, lim, ev = step(racing(), 'slow_off')
    assert not lim.acc_ramp_active and not lim.input_grace
    assert ('race_event_ignored', 'slow_off RACING not_slow') in ev


# --- 신호 방식 3종이 같은 결과 -----------------------------------------------------------------------------------------

BYTE0 = {'go': 0x80, 'stop': 0x40, 'finish': 0x20, 'slow_on': 0x10, 'slow_off': 0x08}


def _level(flag, n=50):      # 계속 켜짐
    return [BYTE0[flag]] * n


def _pulse(flag, n=50):      # 2프레임만 켜졌다 꺼짐
    return [BYTE0[flag]] * 2 + [0] * (n - 2)


def _flicker(flag, n=50):    # 2프레임 켜짐 + 1프레임 꺼짐 반복
    return [BYTE0[flag] if i % 3 != 2 else 0 for i in range(n)]


def _run(sequence):
    """프레임 열 → 디코더 → 상태 기계. 전체 경로."""
    dec = RaceSignalDecoder()
    s = RaceState()
    t = 0.0
    for b0 in sequence:
        dec.feed(bytes([b0]) + bytes(7), t)
        s, _, _ = next_race_state(s, RaceInputs(now=t, events=dec.take_events(), vehicle_speed_kph=5.0,
                                                race_signal_age_s=dec.age_s(t)), P)
        t += 0.02
    return s.phase, s.slow_active


@pytest.mark.parametrize('method', [_level, _pulse, _flicker], ids=['level', 'pulse', 'flicker'])
@pytest.mark.parametrize('flags,expected', [
    (['go'], (RACING, False)),
    (['go', 'slow_on'], (RACING, True)),
    (['go', 'slow_on', 'slow_off'], (RACING, False)),
    (['slow_on', 'go'], (RACING, True)),
    (['go', 'finish'], (FINISHED, False)),
    (['go', 'slow_on', 'finish'], (FINISHED, True)),
    (['go', 'stop'], (SUSPENDED, False)),
    (['stop', 'go'], (SUSPENDED, False)),
])
def test_signal_methods_same_result(method, flags, expected):
    frames = [0] * 5
    for flag in flags:
        frames += method(flag) + [0] * 5
    assert _run(frames) == expected


def test_full_path_old_go_on_at_start_stays_pre_go_a1():
    assert _run(_level('go', 100))[0] == PRE_GO


def test_full_path_old_slow_off_at_start_does_not_clear_a1():
    dec = RaceSignalDecoder()
    s = RaceState(slow_active=True)
    for i in range(20):
        dec.feed(bytes([BYTE0['slow_off']]) + bytes(7), i * 0.02)
        s, _, _ = next_race_state(s, RaceInputs(now=i * 0.02, events=dec.take_events()), P)
    assert s.slow_active


def test_full_path_res_latch():
    m = ResMonitor()
    s = RaceState(phase=RACING)
    t = 0.0
    for i, start in enumerate([0] * 3 + [1] * 5 + [0] * 50):
        m.feed(bytes([0x80 if start else 0]) + bytes(6) + bytes([i & 0xFF]), t)
        m.update(t)
        s, _, _ = next_race_state(s, RaceInputs(now=t, res_active=m.active, res_alive_lost=m.alive_lost,
                                                vehicle_speed_kph=0.0), P)
        t += 0.02
    assert not m.active
    assert s.phase == SUSPENDED and s.suspend_reason == 'res'   # RES 가 풀려도 래치


# --- 끊김 --------------------------------------------------------------------------------------------------------------

def test_signal_timeout_off_only_records():
    s, _, ev = step(racing(), race_signal_age_s=100.0)
    assert s.phase == RACING and 'race_signal_lost' in names(ev)
    s, _, ev = step(s, race_signal_age_s=100.02)
    assert 'race_signal_lost' not in names(ev)   # 한 번만
    s, _, ev = step(s, race_signal_age_s=0.01)
    assert 'race_signal_restored' in names(ev)


def test_signal_never_seen_is_not_lost():
    s, _, ev = step(racing(), race_signal_age_s=None)
    assert s.phase == RACING and not ev


def test_signal_timeout_on_suspends():
    params = RaceParams(race_signal_timeout_s=1.0)
    s, _, _ = step(racing(), race_signal_age_s=1.0, params=params)
    assert s.phase == RACING
    s, _, _ = step(s, race_signal_age_s=1.01, params=params)
    assert s.phase == SUSPENDED and s.suspend_reason == 'race_signal_timeout'


def test_signal_timeout_on_never_seen_does_not_suspend():
    s, _, _ = step(racing(), race_signal_age_s=None, params=RaceParams(race_signal_timeout_s=1.0))
    assert s.phase == RACING


def test_go_reset_blocked_while_signal_timed_out():
    params = RaceParams(race_signal_timeout_s=1.0, resume_mode='go-reset')
    s = RaceState(phase=SUSPENDED, suspend_reason='race_signal_timeout')
    s, _, _ = step(s, race_signal_age_s=5.0, params=params)
    assert not s.reset_ready


# --- D7 입력 유예 ------------------------------------------------------------------------------------------------------

def test_d7_grace_after_go():
    s, lim, _ = step(RaceState(), 'go', now=10.0)
    assert lim.input_grace
    s, lim, _ = step(s, now=10.99)
    assert lim.input_grace
    s, lim, _ = step(s, now=11.0)
    assert not lim.input_grace


def test_d7_grace_after_slow_off():
    s, lim, _ = step(racing(slow_active=True), 'slow_off', now=10.0)
    assert lim.input_grace
    s, lim, _ = step(s, now=10.5)
    assert lim.input_grace
    s, lim, _ = step(s, now=11.1)
    assert not lim.input_grace


@pytest.mark.parametrize('s', [RaceState(), RaceState(phase=PARKED), RaceState(phase=SUSPENDED), racing(slow_active=True),
                               RaceState(phase=FINISHED, slow_active=True)])
def test_d7_grace_in_ignoring_states(s):
    assert step(s, vehicle_speed_kph=5.0)[1].input_grace


@pytest.mark.parametrize('s', [racing(), RaceState(phase=FINISHED)])
def test_d7_no_grace_when_model_is_used(s):
    assert not step(s, vehicle_speed_kph=5.0)[1].input_grace


# --- 6.4 복귀 램프 -----------------------------------------------------------------------------------------------------

def test_ramp_rises_to_100_and_ends():
    s, lim, _ = step(racing(slow_active=True), 'slow_off', now=10.0, last_acc_pct=10.0)
    assert lim.acc_cap_pct == pytest.approx(10.0)
    s, lim, _ = step(s, now=11.0)
    assert lim.acc_ramp_active and lim.acc_cap_pct == pytest.approx(60.0) and lim.acc_ramp_start_pct == pytest.approx(10.0)
    s, lim, _ = step(s, now=11.8)
    assert not lim.acc_ramp_active and lim.acc_cap_pct is None and s.ramp_start_t is None


def test_ramp_cancelled_by_slow_on():
    s, _, _ = step(racing(slow_active=True), 'slow_off', now=10.0)
    s, lim, _ = step(s, 'slow_on', now=10.5)
    assert not lim.acc_ramp_active and s.ramp_start_t is None


def test_ramp_start_clipped_to_percent_range():
    assert step(racing(slow_active=True), 'slow_off', last_acc_pct=-3.0)[1].acc_cap_pct == pytest.approx(0.0)


def test_ramp_disabled_by_zero_rate():
    params = RaceParams(slow_off_ramp_pct_s=0.0)
    _, lim, _ = step(racing(slow_active=True), 'slow_off', params=params)
    assert not lim.acc_ramp_active and lim.acc_cap_pct is None


def test_ramp_cleared_on_suspend():
    s, _, _ = step(racing(slow_active=True), 'slow_off', now=10.0)
    s, lim, _ = step(s, 'stop', now=10.2)
    assert s.ramp_start_t is None and not lim.acc_ramp_active


# --- 서행 제어 시작 표시 -----------------------------------------------------------------------------------------------

def test_long_override_start_only_once():
    s, lim, _ = step(racing(), 'slow_on')
    assert lim.long_override_start
    _, lim, _ = step(s)
    assert lim.long_override and not lim.long_override_start


def test_long_override_start_after_go_when_slow_was_remembered():
    s, lim, _ = step(RaceState(), 'slow_on')
    assert not lim.long_override_start
    _, lim, _ = step(s, 'go')
    assert lim.long_override_start


# --- 감시·표시 ---------------------------------------------------------------------------------------------------------

def test_pre_go_motion_event_once_per_excursion():
    s, _, ev = step(RaceState(), vehicle_speed_kph=P.stop_speed_kph)
    assert not ev
    s, _, ev = step(s, vehicle_speed_kph=4.0)
    assert ev == [('pre_go_motion', '4.0 km/h')] and s.phase == PRE_GO
    s, _, ev = step(s, vehicle_speed_kph=4.0)
    assert not ev
    s, _, _ = step(s, vehicle_speed_kph=0.0)
    s, _, ev = step(s, vehicle_speed_kph=4.0)
    assert names(ev) == ['pre_go_motion']


@pytest.mark.parametrize('state,code', [
    (RaceState(), 0), (RaceState(slow_active=True), 0), (racing(), 1), (racing(slow_active=True), 2),
    (RaceState(phase=FINISHED), 3), (RaceState(phase=FINISHED, slow_active=True), 3), (RaceState(phase=PARKED), 4),
    (RaceState(phase=SUSPENDED), 5),
])
def test_phase_code_section_3_1(state, code):
    assert race_phase_code(state) == code


def test_pre_go_limits():
    _, lim, _ = step(RaceState())
    assert lim == RaceLimits(phase=PRE_GO, allow_motion=False, hold_brake_pct=P.pre_go_brake_pct, input_grace=True)


def test_racing_limits_have_no_caps():
    _, lim, _ = step(racing(), vehicle_speed_kph=80.0)
    assert lim == RaceLimits(phase=RACING, allow_motion=True)


def test_finished_limits_brake_margin():
    _, lim, _ = step(RaceState(phase=FINISHED), vehicle_speed_kph=5.0)
    assert lim.speed_cap_kph == 20.0 and lim.speed_cap_brake_kph == pytest.approx(20.5) and not lim.long_override


def test_input_state_is_not_mutated():
    s0 = RaceState()
    step(s0, 'go')
    assert s0 == RaceState()


@pytest.mark.parametrize('kw', [{'resume_mode': 'x'}, {'race_stop_action': 'x'}, {'slow_target_kph': 9.0},
                                {'pre_go_brake_pct': 120.0}])
def test_bad_params_rejected(kw):
    with pytest.raises(ValueError):
        RaceParams(**kw)
