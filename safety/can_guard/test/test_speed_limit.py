"""speed_limit.py — 계획서 9절 + 브리프 A5. WP4 시험 목록: 반영 3프레임, 어긋남 0.3 s, 해제 지연 기록, tx off 시작 거부, 0x202 없음, 다른 송신자."""
import pytest

from speed_limit import (ACTION_CORRECT, ACTION_PASS, ACTION_REFUSE, EXIT_SPEED_LIMIT, STATE_CHANGING, STATE_CONFIRMED,
                         STATE_IDLE, SpeedLimitManager, SpeedLimitParams, kph_to_raw, raw_to_kph, startup_check)

TX_ON = SpeedLimitParams(tx_enabled=True, release_kph=100.0)
TICK = 0.02


def names(events):
    return [e[0] for e in events]


class Sim:
    """can_guard 메인 루프 흉내: 20 ms 마다 tick, 21 ms 마다 0x202."""

    def __init__(self, mgr, t=100.0):
        self.mgr = mgr
        self.t = t
        self.tx = []        # (시각, kph)
        self.events = []    # (시각, 이름)

    def run(self, duration, rx=None):
        """rx: kph 또는 None(0x202 안 옴) 또는 callable(t)->kph."""
        end = self.t + duration
        while self.t < end - 1e-9:
            if rx is not None:
                self.mgr.on_0x202(self.t, rx(self.t) if callable(rx) else rx)
            out = self.mgr.tick(self.t)
            if out.tx_kph is not None:
                self.tx.append((self.t, out.tx_kph))
            self.events += [(self.t, e[0]) for e in out.events]
            self.t = round(self.t + TICK, 6)

    def event_times(self, name):
        return [t for t, n in self.events if n == name]


# --- 기본값 고정 ---------------------------------------------------------------------------------------------------
def test_tx_default_off():
    """D10: KIAPI 허락 전 송신 금지 — 기본값이 바뀌면 이 시험이 잡는다."""
    assert SpeedLimitParams().tx_enabled is False


def test_release_default_is_not_invented():
    assert SpeedLimitParams().release_kph is None


def test_numbers_match_plan_table():
    p = SpeedLimitParams()
    assert (p.fast_period_s, p.keep_period_s, p.confirm_frames, p.mismatch_s, p.release_note_s, p.startup_timeout_s) == \
        (0.02, 0.1, 3, 0.3, 1.0, 2.0)
    assert p.practice_kph == 30.0 and p.slow_brake_kph == 9.3 and p.allow_missing is False


def test_exit_code():
    assert EXIT_SPEED_LIMIT == 5


def test_default_manager_never_transmits_whatever_happens():
    mgr = SpeedLimitManager()
    sim = Sim(mgr)
    sim.run(0.1, rx=30.0)
    mgr.start(sim.t)
    mgr.slow_on(sim.t)
    sim.run(0.5, rx=30.0)
    mgr.slow_off(sim.t)
    sim.run(2.0, rx=9.3)
    assert sim.tx == []
    assert mgr.target_kph is None and mgr.state == STATE_IDLE


def test_tx_on_without_release_refused_by_manager():
    with pytest.raises(ValueError):
        SpeedLimitManager(SpeedLimitParams(tx_enabled=True))


# --- raw 비교 ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('kph,raw', [(9.3, 93), (8.2, 82), (30.0, 300), (0.0, 0), (100.0, 1000), (150.0, 1000), (-1.0, 0)])
def test_kph_to_raw(kph, raw):
    assert kph_to_raw(kph) == raw


def test_raw_roundtrip_has_no_float_error():
    for raw in range(0, 1001):
        assert kph_to_raw(raw_to_kph(raw)) == raw


def test_float_noise_still_matches():
    """0.1 km/h 단위가 같으면 일치 — 9.3 과 9.300000190(float32 왕복)을 다르다고 보지 않는다."""
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    for k in range(3):
        mgr.on_0x202(k * TICK, 9.300000190734863)
    assert mgr.state == STATE_CONFIRMED


def test_one_raw_step_is_mismatch():
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    for k in range(10):
        mgr.on_0x202(k * TICK, 9.4)
    assert mgr.state == STATE_CHANGING


# --- 반영 3프레임 --------------------------------------------------------------------------------------------------
def test_confirm_after_exactly_three_frames():
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    assert names(mgr.tick(0.0).events) == ['speed_limit_tx']
    mgr.on_0x202(0.02, 9.3)
    mgr.on_0x202(0.04, 9.3)
    assert mgr.state == STATE_CHANGING
    assert mgr.tick(0.04).events == []
    mgr.on_0x202(0.06, 9.3)
    assert mgr.state == STATE_CONFIRMED
    assert names(mgr.tick(0.06).events) == ['speed_limit_confirm']


def test_interrupted_streak_restarts():
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    for k, v in enumerate((9.3, 9.3, 30.0, 9.3, 9.3)):
        mgr.on_0x202(k * TICK, v)
    assert mgr.state == STATE_CHANGING
    mgr.on_0x202(5 * TICK, 9.3)
    assert mgr.state == STATE_CONFIRMED


def test_fast_period_until_confirm_then_keep_period():
    mgr = SpeedLimitManager(TX_ON)
    sim = Sim(mgr)
    mgr.slow_on(sim.t)
    sim.run(0.2)                    # 0x202 없음 → 계속 20 ms
    gaps = [b[0] - a[0] for a, b in zip(sim.tx, sim.tx[1:])]
    assert len(sim.tx) == 10 and all(g == pytest.approx(0.02) for g in gaps)
    assert all(kph == 9.3 for _, kph in sim.tx)
    sim.tx.clear()
    sim.run(1.0, rx=9.3)            # 3프레임(반영 확인) 동안은 20 ms, 그 뒤 100 ms
    assert sim.tx[1][0] - sim.tx[0][0] == pytest.approx(0.02)
    gaps = [b[0] - a[0] for a, b in zip(sim.tx[1:], sim.tx[2:])]
    assert 8 <= len(gaps) <= 10 and all(g == pytest.approx(0.1) for g in gaps)


def test_tx_output_carries_raw_for_encoder():
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    out = mgr.tick(0.0)
    assert out.tx_raw == 93 and out.tx_kph == 9.3


def test_period_tolerates_tick_jitter():
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    assert mgr.tick(0.0).tx_kph is not None
    assert mgr.tick(0.0195).tx_kph is not None     # 20 ms 에서 0.5 ms 이른 호출
    assert mgr.tick(0.025).tx_kph is None


def test_no_tx_before_any_target():
    mgr = SpeedLimitManager(TX_ON)
    sim = Sim(mgr)
    sim.run(1.0, rx=30.0)
    assert sim.tx == [] and sim.events == []


def test_same_target_again_does_not_restart():
    mgr = SpeedLimitManager(TX_ON)
    mgr.slow_on(0.0)
    mgr.tick(0.0)
    for k in range(3):
        mgr.on_0x202(k * TICK, 9.3)
    mgr.slow_on(1.0)
    assert mgr.state == STATE_CONFIRMED
    assert names(mgr.tick(1.0).events) == ['speed_limit_confirm']   # 새 speed_limit_tx 없음


# --- 어긋남 0.3 s --------------------------------------------------------------------------------------------------
def confirmed_sim(params=TX_ON):
    mgr = SpeedLimitManager(params)
    sim = Sim(mgr)
    mgr.slow_on(sim.t)
    sim.run(0.2, rx=9.3)
    assert mgr.state == STATE_CONFIRMED
    sim.events.clear()
    sim.tx.clear()
    return mgr, sim


def test_mismatch_after_0_3s_returns_to_fast():
    mgr, sim = confirmed_sim()
    t0 = sim.t
    sim.run(0.29, rx=30.0)
    assert mgr.state == STATE_CONFIRMED and sim.event_times('speed_limit_mismatch') == []
    sim.run(0.04, rx=30.0)
    assert mgr.state == STATE_CHANGING
    assert len(sim.event_times('speed_limit_mismatch')) == 1
    assert sim.event_times('speed_limit_mismatch')[0] - t0 > 0.3
    sim.tx.clear()
    sim.run(0.1, rx=30.0)
    assert len(sim.tx) == 5        # 다시 20 ms
    assert len(sim.event_times('speed_limit_mismatch')) == 1    # 재시도 중에는 반복 기록 안 함
    sim.run(0.1, rx=9.3)
    assert mgr.state == STATE_CONFIRMED


def test_short_mismatch_is_ignored():
    mgr, sim = confirmed_sim()
    sim.run(0.2, rx=30.0)
    sim.run(0.04, rx=9.3)
    sim.run(0.2, rx=30.0)
    assert mgr.state == STATE_CONFIRMED and sim.events == []


# --- 해제 지연 기록 (D14) -------------------------------------------------------------------------------------------
def test_release_slow_is_record_only_and_retry_continues():
    mgr, sim = confirmed_sim()
    t0 = sim.t
    mgr.slow_off(t0)
    sim.run(0.98, rx=9.3)
    assert sim.event_times('speed_limit_release_slow') == []
    sim.run(0.1, rx=9.3)
    assert len(sim.event_times('speed_limit_release_slow')) == 1
    assert sim.event_times('speed_limit_release_slow')[0] - t0 > 1.0
    assert mgr.state == STATE_CHANGING
    sim.tx.clear()
    sim.run(2.0, rx=9.3)            # 재시도는 계속, 표시는 한 번만
    assert len(sim.tx) == 100 and all(kph == 100.0 for _, kph in sim.tx)
    assert len(sim.event_times('speed_limit_release_slow')) == 1
    sim.run(0.1, rx=100.0)
    assert mgr.state == STATE_CONFIRMED


def test_release_on_time_records_nothing():
    mgr, sim = confirmed_sim()
    mgr.slow_off(sim.t)
    sim.run(0.5, rx=100.0)
    sim.run(2.0, rx=100.0)
    assert sim.event_times('speed_limit_release_slow') == []
    assert mgr.state == STATE_CONFIRMED


def test_slow_on_cancels_pending_release_note():
    mgr, sim = confirmed_sim()
    mgr.slow_off(sim.t)
    sim.run(0.5, rx=9.3)
    mgr.slow_on(sim.t)
    sim.run(2.0, rx=30.0)
    assert sim.event_times('speed_limit_release_slow') == []


def test_slow_on_change_has_no_release_note():
    mgr = SpeedLimitManager(TX_ON)
    sim = Sim(mgr)
    mgr.slow_on(sim.t)
    sim.run(3.0, rx=100.0)
    assert sim.event_times('speed_limit_release_slow') == []


# --- 시작 점검 (9.2 + A5) -------------------------------------------------------------------------------------------
TX_OFF_NO_RELEASE = SpeedLimitParams()
TX_OFF_RELEASE = SpeedLimitParams(release_kph=100.0)


@pytest.mark.parametrize('observed', [30.0, 100.0, 50.0, 9.4, 0.0])
def test_a5_no_release_tx_off_passes_and_records(observed):
    r = startup_check(observed, TX_OFF_NO_RELEASE)
    assert r.action == ACTION_PASS and r.exit_code == 0
    assert names(r.events) == ['speed_limit_startup']
    assert str(observed) in r.events[0][1]


@pytest.mark.parametrize('observed', [0.1, 8.0, 9.3])
def test_a5_no_release_tx_off_refuses_residual_slow_limit(observed):
    r = startup_check(observed, TX_OFF_NO_RELEASE)
    assert r.action == ACTION_REFUSE and r.exit_code == EXIT_SPEED_LIMIT
    assert '--speed-limit-tx' in r.message and f'{observed:g}' in r.message
    assert names(r.events) == ['speed_limit_startup']


def test_a5_residual_boundary_follows_slow_brake():
    p = SpeedLimitParams(slow_brake_kph=9.5)
    assert startup_check(9.5, p).action == ACTION_REFUSE
    assert startup_check(9.6, p).action == ACTION_PASS


def test_a5_tx_on_without_release_is_refused_even_when_value_looks_fine():
    r = startup_check(30.0, SpeedLimitParams(tx_enabled=True))
    assert r.action == ACTION_REFUSE and r.exit_code == EXIT_SPEED_LIMIT
    assert '--speed-limit-release-kph' in r.message


def test_a5_30_kph_never_reads_as_vehicle_limit_message():
    """30.0 은 원격 조종 제한값 — 자율 제한으로 표현하지 않는다."""
    r = startup_check(30.0, TX_OFF_NO_RELEASE)
    assert '제한이 남아' not in r.message


def test_release_set_same_value_passes():
    assert startup_check(100.0, TX_OFF_RELEASE).action == ACTION_PASS
    assert startup_check(100.0, TX_ON).action == ACTION_PASS


def test_release_set_tx_off_mismatch_refused_with_plan_message():
    r = startup_check(30.0, TX_OFF_RELEASE)
    assert r.action == ACTION_REFUSE and r.exit_code == EXIT_SPEED_LIMIT
    assert '차량에 30 km/h 제한이 남아 있음' in r.message and '--speed-limit-tx on' in r.message


def test_release_set_tx_on_mismatch_corrects():
    r = startup_check(9.3, TX_ON)
    assert r.action == ACTION_CORRECT and r.expected_kph == 100.0 and r.exit_code == 0


def test_missing_0x202_refused_in_race_mode():
    for p in (TX_OFF_NO_RELEASE, TX_OFF_RELEASE, TX_ON):
        r = startup_check(None, p)
        assert r.action == ACTION_REFUSE and r.exit_code == EXIT_SPEED_LIMIT


def test_missing_0x202_allowed_by_flag_and_in_practice():
    r = startup_check(None, SpeedLimitParams(allow_missing=True))
    assert r.action == ACTION_PASS
    assert names(r.events) == ['speed_limit_startup']
    assert startup_check(None, TX_OFF_NO_RELEASE, race_mode=False).action == ACTION_PASS


def test_practice_mode_expects_practice_value():
    p = SpeedLimitParams()
    assert startup_check(30.0, p, race_mode=False).action == ACTION_PASS
    assert startup_check(50.0, p, race_mode=False).action == ACTION_REFUSE


def test_startup_correction_success_and_timeout():
    mgr = SpeedLimitManager(TX_ON)
    sim = Sim(mgr)
    t0 = sim.t
    mgr.start(sim.t)
    assert mgr.target_kph == 100.0
    sim.run(1.9, rx=30.0)
    assert mgr.startup_result(sim.t) is None
    sim.run(0.2, rx=30.0)
    assert sim.t - t0 > 2.0 and mgr.startup_result(sim.t) is False

    mgr2 = SpeedLimitManager(TX_ON)
    sim2 = Sim(mgr2)
    mgr2.start(sim2.t)
    assert mgr2.startup_result(sim2.t) is None
    sim2.run(0.1, rx=100.0)
    assert mgr2.startup_result(sim2.t) is True
    assert mgr2.startup_result(sim2.t + 10.0) is True


def test_practice_manager_targets_practice_value():
    mgr = SpeedLimitManager(TX_ON, race_mode=False)
    mgr.start(0.0)
    assert mgr.target_kph == 30.0
    mgr.slow_on(1.0)
    mgr.slow_off(2.0)
    assert mgr.target_kph == 30.0


# --- 읽기 점검 (tx off 도 항상) --------------------------------------------------------------------------------------
def test_read_check_tx_off_records_change_without_sending():
    mgr = SpeedLimitManager()
    sim = Sim(mgr)
    sim.run(0.5, rx=30.0)
    assert sim.events == []
    sim.run(0.4, rx=20.0)
    assert len(sim.event_times('speed_limit_mismatch')) == 1
    sim.run(1.0, rx=20.0)
    assert len(sim.event_times('speed_limit_mismatch')) == 1    # 같은 값이면 또 안 남김
    assert sim.tx == []


def test_vehicle_limit_value_and_age():
    mgr = SpeedLimitManager()
    assert mgr.vehicle_limit_kph(0.0) is None
    mgr.on_0x202(1.0, 30.0)
    assert mgr.vehicle_limit_kph(1.1) == 30.0
    assert mgr.vehicle_limit_kph(1.6) is None


def test_no_0x202_at_runtime_keeps_retrying_without_mismatch():
    """실행 중 0x202 가 아예 없으면 비교할 값이 없다 — 20 ms 재시도만 이어진다."""
    mgr = SpeedLimitManager(TX_ON)
    sim = Sim(mgr)
    mgr.slow_on(sim.t)
    sim.run(1.0)
    assert len(sim.tx) == 50 and mgr.state == STATE_CHANGING
    assert sim.event_times('speed_limit_mismatch') == []


# --- 다른 송신자 ----------------------------------------------------------------------------------------------------
def test_foreign_0x203_logged_once_per_second_with_count():
    mgr = SpeedLimitManager()
    evs = []
    for k in range(100):                # 20 ms × 100 = 2 s
        t = 50.0 + k * TICK
        mgr.on_foreign_0x203(t, 'e803')
        evs += mgr.tick(t).events
    assert names(evs) == ['foreign_0x203', 'foreign_0x203']
    assert 'data=e803' in evs[0][1] and 'n=1' in evs[0][1]
    assert mgr.foreign_count == 100


def test_foreign_0x203_detected_with_tx_on_too():
    mgr = SpeedLimitManager(TX_ON)
    mgr.on_foreign_0x203(0.0, '00')
    assert names(mgr.tick(0.0).events) == ['foreign_0x203']


def test_all_event_names_match_plan_list():
    """계획서 WP3 이벤트 이름 + A5 speed_limit_startup 밖의 이름이 나오면 안 된다."""
    allowed = {'speed_limit_tx', 'speed_limit_confirm', 'speed_limit_mismatch', 'speed_limit_release_slow',
               'foreign_0x203', 'speed_limit_startup'}
    mgr, sim = confirmed_sim()
    mgr.on_foreign_0x203(sim.t)
    mgr.slow_off(sim.t)
    sim.run(1.5, rx=9.3)
    sim.run(0.2, rx=100.0)
    sim.run(0.5, rx=30.0)
    assert {n for _, n in sim.events} <= allowed
    assert len({n for _, n in sim.events}) >= 4
