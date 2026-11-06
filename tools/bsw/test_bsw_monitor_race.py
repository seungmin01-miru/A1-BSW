"""상태판 경기 칸 시험 — 화면 문구 + StatusChannel 읽기(재연결). python3 -m pytest -q tools/bsw/test_bsw_monitor_race.py"""
import os
import sys
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'safety', 'can_guard'))
import bsw_monitor as m  # noqa: E402
import protocol  # noqa: E402

CODE = protocol.RACE_PHASE_CODE


def race(**kw):
    kw.setdefault('race_enabled', True)
    return protocol.GuardStatus(**kw)


def test_hidden_when_not_race():
    assert m.race_lines(None) == []
    assert m.race_lines(protocol.GuardStatus(race_enabled=False)) == []


def test_notice_when_race_disappears():
    for st in (None, protocol.GuardStatus(race_enabled=False)):
        lines = m.race_lines(st, seen_race=True)
        assert len(lines) == 1 and '없음' in lines[0]


def test_lines_show_all_values():
    txt = '\n'.join(m.race_lines(race(
        race_phase=CODE['RACING'], slow_active=True, long_override=True, guard_state=protocol.GUARD_STATE_CODE['ACTIVE'],
        sig_go=True, sig_slow_on=True, res_active=True, res_alive_ok=True, speed_cap_kph=9.3,
        vehicle_speed_limit_kph=30.0, model_healthy_s=12.34, race_signal_age_s=0.02)))
    for want in ('SLOW', 'ACTIVE', '종방향 BSW', 'Go 1', 'SlowOn 1', 'SlowOff 0', 'Finish 0', 'Stop 0', '0x124 0.02 s',
                 '작동 중', '9.3 km/h', '30.0 km/h', '원격 조종 제한', '12.3 s'):
        assert want in txt, want


def test_none_markers():
    txt = '\n'.join(m.race_lines(race(speed_cap_kph=-1.0, vehicle_speed_limit_kph=-1.0, race_signal_age_s=-1.0)))
    assert '상한 없음' in txt and '0x202 수신 없음' in txt and '0x124 수신 없음' in txt and '❌ 끊김' in txt
    assert '정지 유지' in '\n'.join(m.race_lines(race(speed_cap_kph=0.0)))


def test_stale_is_marked():
    assert '오래됨' in m.race_lines(race(age_s=2.0))[0]
    assert '오래됨' not in m.race_lines(race(age_s=0.02))[0]


def test_render_text_only_with_race():
    v = {'now': 0, 'speed': None, 'veh': None, 'cmd': None, 'app': None, 'rx': []}
    assert '경기' not in m.render_text(v, '', {}, '', 'x')
    assert '경기        PRE_GO' in m.render_text(v, '', {}, '', 'x', m.race_lines(race()))


def test_race_status_off_when_no_name():
    assert m.RaceStatus(None).poll() is None


@pytest.fixture
def name():
    return f'a1_test_mon_{uuid.uuid4().hex[:8]}'


def test_race_status_reads_and_never_writes(name):
    rs = m.RaceStatus(name)
    assert rs.poll() is None and not rs.seen_race
    w = protocol.StatusChannel.create(name)
    try:
        rs.last_open = 0.0
        assert rs.poll() is None       # 세그먼트는 있지만 아직 안 써짐
        w.write(race(race_phase=CODE['RACING']))
        seq = w._payload.seq
        st = rs.poll()
        assert st.race_phase == CODE['RACING'] and rs.seen_race
        assert w._payload.seq == seq
    finally:
        w.close()
        rs.close()


def test_race_status_follows_restart(name):
    w = protocol.StatusChannel.create(name)
    w.write(race(race_phase=CODE['RACING']))
    rs = m.RaceStatus(name)
    assert rs.poll().race_phase == CODE['RACING']
    w.close()
    w = protocol.StatusChannel.create(name)
    try:
        w.write(race(race_phase=CODE['FINISHED']))
        rs.last.age_s = 5.0            # 옛 핸들의 값이 오래됨
        rs.last_open = 0.0
        assert rs.poll().race_phase == CODE['FINISHED']
    finally:
        w.close()
        rs.close()
