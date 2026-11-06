"""race_rules.py 단위 시험(D3·D7·D11·A8 + 폴백) — ROS 불필요. 시간은 가짜 시계(now 인자), StatusChannel 값은 protocol.GuardStatus 그대로."""
import os
import random
import sys
import time
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'a1_can_bridge'))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', '..', 'safety', 'can_guard'))
import protocol  # noqa: E402

from a1_can_bridge.race_status import StatusReader  # noqa: E402
from a1_control_adapter.conversion import InputLimits, validate  # noqa: E402
from a1_control_adapter.race_rules import RaceRules  # noqa: E402

NAN = float('nan')
INF = float('inf')


def status(phase='RACING', guard='ACTIVE', race=True, age=0.0, **kw):
    code = protocol.RACE_PHASE_CODE[phase]
    return protocol.GuardStatus(race_enabled=race, race_phase=code, guard_state=protocol.GUARD_STATE_CODE[guard],
                                age_s=age, **kw)


def rules(st=None, now=100.0):
    r = RaceRules(InputLimits(15.0, 45.0), stale_s=0.15, hold_after_s=0.10, pre_go_relatch_s=1.0)
    r.set_status(st, now)
    return r


# ── 경기 모드 조건 ──────────────────────────────────────
@pytest.mark.parametrize('st,expect', [
    (None, False),                                # 없음
    (status(race=False), False),                  # --race off
    (status(), True),
])
def test_race_active_conditions(st, expect):
    assert rules(st).race_active(100.0) is expect


def test_race_inactive_when_stamp_old_between_polls():
    r = rules(status(age=0.05))
    assert r.race_active(100.04)                  # 나이 0.09
    assert not r.race_active(100.06)              # 나이 0.11 > 0.1


# ── 폴백: 기존 규칙 완전히 그대로 ────────────────────────
@pytest.mark.parametrize('st', [None, status(race=False, phase='PRE_GO', input_grace=True, long_override=True)])
class TestFallback:
    def test_latches_after_stale(self, st):
        r = rules(st)
        ok = r.decide(100.0, 0.15, True, True, False)
        assert ok.latch_reason is None and ok.input_ok and not ok.holding and not ok.race
        d = r.decide(100.0, 0.151, True, True, False)
        assert d.latch_reason == '제어기 입력 끊김 (151 ms)' and not d.input_ok and not d.holding

    def test_never_received_latches_with_old_message(self, st):
        assert rules(st).decide(100.0, None, False, True, False).latch_reason == '제어기 입력 끊김'

    def test_no_latch_when_disarmed(self, st):
        assert rules(st).decide(100.0, 5.0, False, False, False).latch_reason is None

    def test_range_rejected_not_clipped(self, st):
        for a, s in ((15.1, 0.0), (-20.0, 0.0), (0.0, 45.1)):
            d = rules(st).check_input(a, s, 100.0)
            assert d.why and not d.clipped

    def test_no_auto_release_no_slew_reset(self, st):
        r = rules(st)
        for i in range(200):
            d = r.decide(100.0 + i * 0.02, 0.0, True, True, True, False, True)
            assert not d.release_latch and not d.reset_slew


@pytest.mark.parametrize('st', [None, status(race=False), status(age=0.2)])
def test_fallback_matches_old_rules_random(st):
    """변경 전 cmd_adapter 규칙을 그대로 옮긴 식과 무작위 입력으로 비교(--race off 불변)."""
    rng = random.Random(1234)
    lim = InputLimits(15.0, 45.0)
    r = rules(st)
    for _ in range(20000):
        a = rng.choice([rng.uniform(-30, 30), NAN, INF, 15.0, -15.0, 15.0001])
        s = rng.choice([rng.uniform(-90, 90), NAN, -INF, 45.0, -45.0, 45.0001])
        d = r.check_input(a, s, 100.0)
        assert d.why == validate(a, s, lim) and not d.clipped
        if d.why is None:
            assert (d.accel_mps2, d.steer_tire_deg) == (a, s)
        age = rng.choice([None, rng.uniform(0, 0.3), 0.15])
        sa, la, lat = rng.random() < 0.5, rng.random() < 0.5, rng.random() < 0.5
        t = r.decide(100.0, age, sa, la, lat, rng.random() < 0.5, rng.random() < 0.5)
        fresh = age is not None and age <= 0.15
        assert t.input_ok == fresh
        assert bool(t.latch_reason) == ((sa or la) and not fresh and not lat)
        assert not (t.race or t.holding or t.release_latch or t.reset_slew)


def test_fallback_when_status_goes_stale_inside_gap():
    """경기 모드로 끊김(기록만 멈춤) 중 StatusChannel 이 없어지면 그 순간부터 기존 150 ms 래치."""
    r = rules(status())
    assert r.decide(100.0, 0.2, True, True, False).holding
    r.set_status(None, 100.02)
    d = r.decide(100.02, 0.22, True, True, False)
    assert d.latch_reason and not d.holding


# ── D3: 끊김 ────────────────────────────────────────────
def test_d3_hold_without_latch_then_resume():
    r = rules(status())
    d = r.decide(100.0, 0.10, True, True, False)
    assert d.input_ok and not d.holding
    d = r.decide(100.0, 0.101, True, True, False)
    assert d.holding and not d.input_ok and d.latch_reason is None and d.race
    d = r.decide(100.0, 10.0, True, True, False)         # 아무리 길어도 래치 없음(can_guard 가 판정)
    assert d.holding and d.latch_reason is None
    d = r.decide(100.0, 0.02, True, True, False)         # 돌아오면 재개
    assert d.input_ok and not d.holding


def test_d3_no_input_at_all_is_hold():
    d = rules(status()).decide(100.0, None, True, False, False)
    assert d.holding and d.latch_reason is None


def test_d3_disarmed_keeps_writing():
    d = rules(status()).decide(100.0, 5.0, False, False, False)
    assert not d.holding and not d.input_ok          # 노드: disarm 중 기록·하트비트는 항상


def test_d3_nan_still_rejected_in_every_phase():
    for ph in protocol.RACE_PHASE_CODE:
        r = rules(status(phase=ph, input_grace=True, long_override=True))
        for a, s in ((NAN, 0.0), (0.0, NAN), (INF, 0.0), (0.0, -INF)):
            d = r.check_input(a, s, 100.0)
            assert d.why and not d.clipped


# ── D7: 범위 밖 ─────────────────────────────────────────
@pytest.mark.parametrize('st', [
    status(phase='PRE_GO'), status(phase='SLOW'), status(phase='PARKED'), status(phase='SUSPENDED'),
    status(phase='RACING', slow_active=True),         # SLOW 를 RACING + slow_active 로 써도 같다
    status(phase='RACING', input_grace=True),         # Go·Slow Off 직후
    status(phase='FINISHED', long_override=True),
])
def test_d7_clip_window(st):
    r = rules(st)
    d = r.check_input(100.0, -90.0, 100.0)
    assert d.why is None and d.clipped and d.accel_mps2 == 15.0 and d.steer_tire_deg == -45.0
    d = r.check_input(-16.0, 46.0, 100.0)
    assert d.why is None and d.clipped and d.accel_mps2 == -15.0 and d.steer_tire_deg == 45.0
    d = r.check_input(14.0, 44.0, 100.0)              # 범위 안은 그대로
    assert not d.clipped and d.accel_mps2 == 14.0 and d.steer_tire_deg == 44.0


@pytest.mark.parametrize('phase', ['RACING', 'FINISHED'])
def test_d7_outside_window_rejects(phase):
    d = rules(status(phase=phase)).check_input(15.1, 0.0, 100.0)
    assert d.why and not d.clipped
    d = rules(status(phase=phase)).check_input(0.0, 46.0, 100.0)
    assert d.why and not d.clipped


def test_d7_status_stale_at_input_time_rejects():
    d = rules(status(phase='PRE_GO')).check_input(20.0, 0.0, 100.2)
    assert d.why and not d.clipped


# ── D11: 출발 대기 중 래치 해제 ──────────────────────────
def run_relatch(r, t0=100.0, dur=1.2, **kw):
    """20 ms 주기로 돌려 처음 해제된 시각을 돌려준다(없으면 None)."""
    a = dict(input_age=0.01, steer_armed=True, long_armed=True, latched=True, wait_restart=False, actual_steer_fresh=True)
    a.update(kw)
    for i in range(int(dur / 0.02) + 1):
        now = t0 + i * 0.02
        r.set_status(r._st, now)
        if r.decide(now, **a).release_latch:
            return now
    return None


def test_d11_releases_after_relatch_s():
    t = run_relatch(rules(status(phase='PRE_GO', guard='HOLDING')))
    assert t == pytest.approx(101.0, abs=0.021)


@pytest.mark.parametrize('guard', ['INIT', 'ACTIVE', 'HOLDING', 'DEGRADED'])
def test_d11_allowed_guard_states(guard):
    assert run_relatch(rules(status(phase='PRE_GO', guard=guard))) is not None


@pytest.mark.parametrize('guard', ['STOPPED', 'ESTOP'])
def test_d11_never_from_stopped_or_estop(guard):
    assert run_relatch(rules(status(phase='PRE_GO', guard=guard))) is None


@pytest.mark.parametrize('phase', ['RACING', 'SLOW', 'FINISHED', 'PARKED', 'SUSPENDED'])
def test_d11_only_in_pre_go(phase):
    assert run_relatch(rules(status(phase=phase))) is None


def test_d11_blocked_cases():
    pre = status(phase='PRE_GO')
    assert run_relatch(rules(pre), input_age=0.11) is None                     # 입력 끊김
    assert run_relatch(rules(pre), input_age=None) is None
    assert run_relatch(rules(pre), wait_restart=True) is None                  # 운영자 disarm 뒤
    assert run_relatch(rules(pre), steer_armed=False, long_armed=False) is None  # 운영자가 축을 내림
    assert run_relatch(rules(pre), actual_steer_fresh=False) is None           # 조향 arm 인데 0x200 없음
    assert run_relatch(rules(pre), latched=False) is None
    assert run_relatch(rules(pre), steer_armed=False, actual_steer_fresh=False) is not None   # 종방향만이면 0x200 불필요
    assert run_relatch(rules(None)) is None                                    # StatusChannel 없음


def test_d11_gap_restarts_timer():
    r = rules(status(phase='PRE_GO'))
    kw = dict(steer_armed=True, long_armed=True, latched=True, wait_restart=False, actual_steer_fresh=True)
    assert not r.decide(100.0, 0.01, **kw).release_latch
    assert not r.decide(100.9, 0.01, **kw).release_latch
    r.set_status(r._st, 100.92)
    assert not r.decide(100.92, 0.2, **kw).release_latch      # 끊김 → 처음부터
    r.set_status(r._st, 101.1)
    assert not r.decide(101.1, 0.01, **kw).release_latch
    r.set_status(r._st, 102.0)
    assert not r.decide(102.0, 0.01, **kw).release_latch
    r.set_status(r._st, 102.1)
    assert r.decide(102.1, 0.01, **kw).release_latch


def test_d11_nan_restarts_timer():
    r = rules(status(phase='PRE_GO'))
    kw = dict(steer_armed=True, long_armed=True, latched=True, wait_restart=False, actual_steer_fresh=True)
    r.decide(100.0, 0.01, **kw)
    r.set_status(r._st, 100.95)
    assert r.check_input(NAN, 0.0, 100.95).why
    assert not r.decide(100.96, 0.01, **kw).release_latch     # NaN 때문에 다시 처음부터
    r.set_status(r._st, 101.5)
    assert not r.decide(101.5, 0.01, **kw).release_latch
    r.set_status(r._st, 101.97)
    assert r.decide(101.97, 0.01, **kw).release_latch


def test_d11_clipped_input_counts_as_normal():
    r = rules(status(phase='PRE_GO'))
    kw = dict(steer_armed=True, long_armed=True, latched=True, wait_restart=False, actual_steer_fresh=True)
    r.decide(100.0, 0.01, **kw)
    r.set_status(r._st, 100.5)
    assert r.check_input(30.0, 0.0, 100.5).clipped
    r.set_status(r._st, 101.0)
    assert r.decide(101.0, 0.01, **kw).release_latch


def test_d11_go_during_timer_cancels():
    r = rules(status(phase='PRE_GO'))
    kw = dict(steer_armed=True, long_armed=True, latched=True, wait_restart=False, actual_steer_fresh=True)
    r.decide(100.0, 0.01, **kw)
    r.set_status(status(phase='RACING', input_grace=True), 100.9)
    assert not r.decide(101.1, 0.01, **kw).release_latch      # RACING 에서 래치는 재시작으로만
    r.set_status(status(phase='PRE_GO'), 101.2)
    assert not r.decide(101.2, 0.01, **kw).release_latch      # 다시 PRE_GO 면 타이머 새로


# ── A8: Slew 를 실제 핸들각에 ───────────────────────────
@pytest.mark.parametrize('phase,expect', [('PRE_GO', True), ('PARKED', True), ('SUSPENDED', True), ('RACING', False),
                                          ('SLOW', False), ('FINISHED', False)])
def test_a8_slew_reset_phases(phase, expect):
    assert rules(status(phase=phase)).decide(100.0, 0.01, True, True, False).reset_slew is expect


def test_a8_racing_slow_active_keeps_model_steer():
    assert not rules(status(phase='RACING', slow_active=True)).decide(100.0, 0.01, True, True, False).reset_slew


# ── 재연결: 실제 공유메모리 ─────────────────────────────
def writer(name):
    return protocol.StatusChannel.create(name)


@pytest.fixture
def shm_name():
    return f'a1_test_status_{uuid.uuid4().hex[:8]}'


def test_reader_no_segment_then_appears(shm_name):
    rd = StatusReader(protocol, shm_name, reopen_s=0.0)
    assert rd.poll(1.0) is None and rd.result == '없음'
    w = writer(shm_name)
    try:
        assert rd.poll(2.0) is None and rd.result == '오래됨'       # 세그먼트는 있지만 아직 안 써짐
        w.write(protocol.GuardStatus(race_enabled=True))
        st = rd.poll(3.0)
        assert st.race_enabled and rd.result == '정상'
    finally:
        rd.close()
        w.close()


def test_reader_reconnects_after_guard_restart_and_adapter_rules_follow(shm_name):
    """can_guard 재시작: 옛 세그먼트 unlink → 새 세그먼트. 옛 핸들은 옛 내용뿐이라 다시 열어야 경기 모드가 돌아온다."""
    w1 = writer(shm_name)
    w1.write(protocol.GuardStatus(race_enabled=True, race_phase=protocol.RACE_PHASE_CODE['RACING']))
    rd = StatusReader(protocol, shm_name, reopen_s=0.0, max_age_s=0.5)
    r = rules(None)
    now = 100.0
    r.set_status(rd.poll(now), now)
    assert r.race_active(now)
    w1.close()                                                     # 재시작: 세그먼트 사라짐
    w2 = writer(shm_name)
    try:
        w2.write(protocol.GuardStatus(race_enabled=True, race_phase=protocol.RACE_PHASE_CODE['SLOW']))
        r.set_status(rd.poll(now), now)
        assert r.phase(now) == 'RACING'                            # 옛 핸들의 옛 값(아직 0.5 s 안)
        time.sleep(0.55)
        w2.write(protocol.GuardStatus(race_enabled=True, race_phase=protocol.RACE_PHASE_CODE['SLOW']))
        r.set_status(rd.poll(now), now)
        assert r.phase(now) == 'SLOW'                              # 옛 값 오래됨 → 다시 열어 새 세그먼트
    finally:
        rd.close()
        w2.close()


def test_reader_respects_reopen_period(shm_name):
    rd = StatusReader(protocol, shm_name, reopen_s=1.0)
    assert rd.poll(0.0) is None
    w = writer(shm_name)
    try:
        w.write(protocol.GuardStatus(race_enabled=True))
        assert rd.poll(0.5) is None                                # 아직 재시도 안 함
        assert rd.poll(1.0) is not None
    finally:
        rd.close()
        w.close()


class TornChannel:
    """읽을 때마다 정해 둔 동작."""

    def __init__(self, seq):
        self.seq = list(seq)

    def read(self, max_age_s=None):
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    def close(self):
        pass


def test_reader_torn_keeps_last_only_while_fresh():
    rd = StatusReader(protocol, 'unused', reopen_s=100.0, max_age_s=0.1)
    rd.last_try = 0.0
    good = protocol.GuardStatus(race_enabled=True, age_s=0.01)
    torn = protocol.TornReadError('x')
    rd.ch = TornChannel([good, torn, torn])
    assert rd.poll(10.0) is good
    assert rd.poll(10.05) is good and rd.torn_count == 1          # 이번 주기 새 값 없음 → 아직 신선한 직전 값
    assert rd.poll(10.2) is None and rd.torn_count == 2           # 직전 값도 오래됨


def test_reader_torn_without_previous_is_none():
    rd = StatusReader(protocol, 'unused', reopen_s=100.0)
    rd.last_try = 0.0
    rd.ch = TornChannel([protocol.TornReadError('x')])
    assert rd.poll(1.0) is None


def test_reader_old_protocol_without_status_channel():
    class Old:
        pass
    rd = StatusReader(Old(), 'x')
    assert rd.poll(1.0) is None and rd.result == '없음'
