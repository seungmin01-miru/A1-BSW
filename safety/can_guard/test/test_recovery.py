"""recovery.py — 계획서 8.1 이탈 예산 ★값, 8.2 복구·정지 확정, 8.3 저속 재개."""
import math

import pytest

from recovery import (PHASE_COMMITTED, PHASE_NORMAL, PHASE_RECOVER, RecoveryParams, RecoveryTracker, a_err_mps2,
                      budget_gap_s, resume_blockers, worst_deviation_m)

DT = 0.02
R20 = RecoveryParams(min_radius_m=20.0)


# --- 8.1 ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('kph', [0.0, 9.0, 30.0, 80.0, None])
def test_budget_without_radius_is_about_0_20s_at_any_speed(kph):
    assert budget_gap_s(kph) == pytest.approx(0.197, abs=0.001)


def test_budget_radius_20m_at_9kph_is_about_1_1s():
    assert budget_gap_s(9.0, R20) == pytest.approx(1.136, abs=0.001)


def test_a_err_capped_at_alat_max():
    assert a_err_mps2(9.0, R20) == pytest.approx(2.5 ** 2 / 20.0)
    assert a_err_mps2(100.0, R20) == 3.0


def test_unknown_speed_uses_alat_max_even_with_radius():
    assert a_err_mps2(None, R20) == 3.0


def test_stopped_car_has_infinite_budget_with_radius():
    assert budget_gap_s(0.0, R20) == math.inf


def test_worst_deviation_formula_and_inverse_of_budget():
    assert worst_deviation_m(0.2, None) == pytest.approx(0.5 * 3.0 * 0.45 ** 2)
    assert worst_deviation_m(budget_gap_s(30.0), 30.0) == pytest.approx(0.30)
    assert worst_deviation_m(budget_gap_s(9.0, R20), 9.0, R20) == pytest.approx(0.30)


def test_negative_gap_treated_as_zero():
    assert worst_deviation_m(-1.0, None) == worst_deviation_m(0.0, None)


def test_budget_zero_if_delay_alone_exceeds():
    assert budget_gap_s(50.0, RecoveryParams(budget_m=0.05)) == 0.0


@pytest.mark.parametrize('kw', [{'budget_m': 0.0}, {'alat_max_mps2': 0.0}, {'min_radius_m': 0.0}, {'fresh_frames': 0}])
def test_bad_params_rejected(kw):
    with pytest.raises(ValueError):
        RecoveryParams(**kw)


# --- 8.2 ------------------------------------------------------------------------------------------------------
def run_gap(tr, t0, gap_len, speed=30.0, hold_after=0.0):
    """끊김 gap_len 초(주기 DT). 마지막 시각 반환."""
    t = t0
    decisions = []
    while t - t0 < gap_len:
        decisions.append(tr.step(t, False, (t - t0) + hold_after, speed))
        t += DT
    return t, decisions


def test_normal_stays_normal_with_fresh_input():
    tr = RecoveryTracker()
    for k in range(100):
        d = tr.step(k * DT, True, 0.0, 30.0)
        assert d.phase == PHASE_NORMAL and d.events == []


def test_short_gap_recovers_after_three_fresh_frames():
    tr = RecoveryTracker()
    t, decisions = run_gap(tr, 0.0, 0.06)
    assert decisions[-1].phase == PHASE_RECOVER
    assert tr.step(t, True, 0.0, 30.0).phase == PHASE_RECOVER
    assert tr.step(t + DT, True, 0.0, 30.0).phase == PHASE_RECOVER
    d = tr.step(t + 2 * DT, True, 0.0, 30.0)
    assert d.phase == PHASE_NORMAL and d.events[0][0] == 'recover'


def test_broken_fresh_streak_restarts_count():
    tr = RecoveryTracker()
    t, _ = run_gap(tr, 0.0, 0.04)
    tr.step(t, True, 0.0, 30.0)
    tr.step(t + DT, True, 0.0, 30.0)
    tr.step(t + 2 * DT, False, 0.1, 30.0)
    assert tr.phase == PHASE_RECOVER
    tr.step(t + 3 * DT, True, 0.0, 30.0)
    tr.step(t + 4 * DT, True, 0.0, 30.0)
    assert tr.phase == PHASE_RECOVER
    tr.step(t + 5 * DT, True, 0.0, 30.0)
    assert tr.phase == PHASE_NORMAL


def test_gap_over_budget_commits_and_latches():
    tr = RecoveryTracker()
    t, decisions = run_gap(tr, 0.0, 0.3)
    assert decisions[-1].phase == PHASE_COMMITTED
    commits = [e for d in decisions for e in d.events if e[0] == 'commit']
    assert len(commits) == 1
    # 커밋 시점은 예산(약 0.197 s) 직후
    idx = next(i for i, d in enumerate(decisions) if d.phase == PHASE_COMMITTED)
    assert idx * DT == pytest.approx(0.2, abs=DT + 1e-9)
    # 입력이 돌아와도 래치
    for k in range(200):
        d = tr.step(t + k * DT, True, 0.0, 30.0)
    assert d.phase == PHASE_COMMITTED


def test_gap_start_accounts_for_adapter_hold_after():
    """어댑터가 hold_after_s(0.10 s) 뒤에 기록을 멈추므로 can_guard 는 그만큼 앞에서 끊긴 것으로 센다(8.2)."""
    tr = RecoveryTracker()
    d = tr.step(10.0, False, 0.10, 30.0)
    assert d.phase == PHASE_RECOVER and d.gap_s == pytest.approx(0.10)
    d = tr.step(10.1, False, 0.20, 30.0)
    assert d.phase == PHASE_COMMITTED      # 0.20 s > 0.197 s


def test_budget_counts_confirmation_frames_too():
    """입력이 돌아온 3주기 동안에도 조향은 아직 고정 — 그동안 예산을 넘으면 복구가 아니라 확정."""
    tr = RecoveryTracker()
    tr.step(0.0, False, 0.0, 30.0)
    tr.step(0.15, False, 0.15, 30.0)
    assert tr.phase == PHASE_RECOVER
    tr.step(0.17, True, 0.0, 30.0)
    assert tr.phase == PHASE_RECOVER
    tr.step(0.19, True, 0.0, 30.0)
    assert tr.phase == PHASE_RECOVER
    d = tr.step(0.21, True, 0.0, 30.0)
    assert d.phase == PHASE_COMMITTED


def test_peak_speed_used_with_radius():
    """R_min 있으면 끊김 동안 최고 속도로 계산 — 끊김 중 감속해도 줄어들지 않는다."""
    tr = RecoveryTracker(R20)
    tr.step(0.0, False, 0.0, 30.0)
    expected = budget_gap_s(30.0, R20)
    d = tr.step(0.05, False, 0.05, 5.0)
    assert d.budget_s == pytest.approx(expected)


def test_low_speed_with_radius_tolerates_long_gap():
    tr = RecoveryTracker(R20)
    t, decisions = run_gap(tr, 0.0, 1.0, speed=9.0)
    assert decisions[-1].phase == PHASE_RECOVER
    t2, decisions = run_gap(tr, t, 0.2, speed=9.0)
    assert decisions[-1].phase == PHASE_COMMITTED


def test_unknown_speed_uses_conservative_budget_with_radius():
    tr = RecoveryTracker(R20)
    run_gap(tr, 0.0, 0.3, speed=None)
    assert tr.phase == PHASE_COMMITTED


def test_second_gap_starts_clean():
    tr = RecoveryTracker()
    t, _ = run_gap(tr, 0.0, 0.1)
    for k in range(3):
        tr.step(t + k * DT, True, 0.0, 30.0)
    assert tr.phase == PHASE_NORMAL
    d = tr.step(t + 1.0, False, 0.02, 30.0)
    assert d.phase == PHASE_RECOVER and d.gap_s == pytest.approx(0.02)


# --- 8.3 ------------------------------------------------------------------------------------------------------
ON = RecoveryParams(min_radius_m=20.0, resume_low_speed=True)


def test_blockers_all_clear():
    assert resume_blockers(0.1, 0.5, False, False, False, 0, ON) == []


@pytest.mark.parametrize('args,name', [
    ((0.31, 0.5, False, False, False, 0), 'deviation'),
    ((0.1, 0.49, False, False, False, 0), 'model_not_healthy'),
    ((0.1, 0.5, True, False, False, 0), 'estop_res_suspended'),
    ((0.1, 0.5, False, True, False, 0), 'estop_res_suspended'),
    ((0.1, 0.5, False, False, True, 0), 'estop_res_suspended'),
    ((0.1, 0.5, False, False, False, 2), 'resume_count'),
])
def test_each_condition_blocks(args, name):
    assert resume_blockers(*args, ON) == [name]


def test_nan_deviation_blocks():
    assert 'deviation' in resume_blockers(float('nan'), 0.5, False, False, False, 0, ON)


def committed_externally(params):
    """예산이 아닌 이유로 확정된 상황(can_guard 가 commit() 호출)."""
    tr = RecoveryTracker(params)
    tr.step(0.0, False, 0.0, 9.0)
    tr.commit(0.1, 'external')
    return tr


def test_resume_off_by_default():
    assert RecoveryParams().resume_low_speed is False
    tr = committed_externally(RecoveryParams(min_radius_m=20.0))
    assert tr.try_resume(0.6, 0.5) is False and tr.phase == PHASE_COMMITTED


def test_resume_returns_to_normal_and_sets_cap_for_3s():
    tr = committed_externally(ON)
    tr.step(0.3, True, 0.0, 9.0)
    assert tr.try_resume(0.6, 0.5) is True
    assert tr.phase == PHASE_NORMAL and tr.resumes_used == 1
    assert tr.speed_cap_kph(0.6) == 20.0
    assert tr.speed_cap_kph(3.59) == 20.0
    assert tr.speed_cap_kph(3.6) is None
    assert tr.step(3.7, True, 0.0, 9.0).phase == PHASE_NORMAL


def test_third_gap_never_resumes():
    tr = RecoveryTracker(ON)
    t = 0.0
    for expected in (True, True, False):
        tr.step(t, False, 0.0, 9.0)
        tr.commit(t + 0.1)
        assert tr.try_resume(t + 0.6, 0.5) is expected
        t += 10.0
    assert tr.phase == PHASE_COMMITTED and tr.resumes_used == 2


def test_resume_blocked_during_estop():
    tr = committed_externally(ON)
    assert tr.try_resume(0.6, 0.5, estop=True) is False
    assert tr.try_resume(0.6, 0.5, res_active=True) is False
    assert tr.try_resume(0.6, 0.5, suspended=True) is False
    assert tr.phase == PHASE_COMMITTED


def test_resume_needs_commit():
    tr = RecoveryTracker(ON)
    assert tr.try_resume(0.0, 1.0) is False


def test_budget_commit_never_resumes_literal_condition_1():
    """계획서 8.3 조건 1 을 그대로 구현한 결과: 이탈 예산을 넘어 확정되면 재개 조건을 만족할 수 없다(최악 이탈은 시간에 따라 커지기만 함).
    안전한 쪽. 미결 항목으로 보고함 — 해석이 정해지면 이 시험을 고친다."""
    tr = RecoveryTracker(ON)
    t, decisions = run_gap(tr, 0.0, 1.3, speed=9.0)
    assert decisions[-1].phase == PHASE_COMMITTED
    for k in range(100):
        tr.step(t + k * DT, True, 0.0, 9.0)
        assert tr.try_resume(t + k * DT, 10.0) is False
