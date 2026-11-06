"""race_status.py 변환 함수 시험 — ROS 불필요. 재연결은 a1_control_adapter/test/test_race_rules.py."""
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', '..', 'safety', 'can_guard'))
import protocol  # noqa: E402

from a1_can_bridge.race_status import change_key, display_phase, PublishGate, status_to_fields  # noqa: E402

CODE = protocol.RACE_PHASE_CODE


def msg_field_names():
    p = os.path.join(HERE, '..', '..', 'a1_can_msgs', 'msg', 'RaceState.msg')
    names = []
    with open(p, encoding='utf-8') as f:
        for ln in f:
            ln = ln.split('#')[0].strip()
            if ln and '=' not in ln:
                names.append(ln.split()[1])
    return names


def test_fields_match_message():
    """.msg 와 변환 함수가 어긋나면 노드가 실행 중에 AttributeError 로 죽는다 — 여기서 잡는다."""
    assert set(status_to_fields(protocol.GuardStatus(), protocol)) == set(msg_field_names()) - {'header'}


@pytest.mark.parametrize('name,code', sorted(CODE.items()))
def test_phase_display_passthrough(name, code):
    f = status_to_fields(protocol.GuardStatus(race_phase=code), protocol)
    assert f['phase'] == code and f['phase_name'] == name


def test_phase_values_match_datasheet():
    assert CODE == {'PRE_GO': 0, 'RACING': 1, 'SLOW': 2, 'FINISHED': 3, 'PARKED': 4, 'SUSPENDED': 5}


def test_racing_plus_slow_active_is_slow():
    st = protocol.GuardStatus(race_phase=CODE['RACING'], slow_active=True)
    assert display_phase(st, protocol) == 2
    f = status_to_fields(st, protocol)
    assert f['phase'] == 2 and f['phase_name'] == 'SLOW'


def test_slow_active_only_changes_racing():
    st = protocol.GuardStatus(race_phase=CODE['SUSPENDED'], slow_active=True)
    assert display_phase(st, protocol) == 5


def test_none_markers_pass_through():
    f = status_to_fields(protocol.GuardStatus(), protocol)
    assert f['speed_cap_kph'] == -1.0 and f['vehicle_speed_limit_kph'] == -1.0 and f['race_signal_age_s'] == -1.0
    assert math.isnan(f['pit_lat_deg']) and math.isnan(f['pit_lon_deg'])
    assert f['guard_state'] == 'INIT'


def test_zero_cap_is_not_none():
    assert status_to_fields(protocol.GuardStatus(speed_cap_kph=0.0), protocol)['speed_cap_kph'] == 0.0


def test_nonfinite_numbers_become_none_marker():
    st = protocol.GuardStatus(speed_cap_kph=float('nan'), vehicle_speed_limit_kph=float('inf'),
                              race_signal_age_s=float('nan'), model_healthy_s=float('nan'))
    f = status_to_fields(st, protocol)
    assert (f['speed_cap_kph'], f['vehicle_speed_limit_kph'], f['race_signal_age_s'], f['model_healthy_s']) \
        == (-1.0, -1.0, -1.0, 0.0)


def test_values_copied():
    st = protocol.GuardStatus(sig_go=True, sig_finish=True, res_active=True, res_alive_ok=True, long_override=True,
                              speed_cap_kph=9.3, vehicle_speed_limit_kph=30.0, model_healthy_s=2.5,
                              guard_state=protocol.GUARD_STATE_CODE['ESTOP'], pit_lat_deg=37.1, pit_lon_deg=127.2,
                              race_signal_age_s=0.02)
    f = status_to_fields(st, protocol)
    assert f['sig_go'] and f['sig_finish'] and not f['sig_stop'] and f['res_active'] and f['res_alive_ok']
    assert f['long_override'] and f['speed_cap_kph'] == 9.3 and f['vehicle_speed_limit_kph'] == 30.0
    assert f['model_healthy_s'] == 2.5 and f['guard_state'] == 'ESTOP'
    assert f['pit_lat_deg'] == 37.1 and f['pit_lon_deg'] == 127.2 and f['race_signal_age_s'] == 0.02


def test_change_key_ignores_volatile_and_handles_nan():
    a = status_to_fields(protocol.GuardStatus(race_signal_age_s=0.01, model_healthy_s=1.0), protocol)
    b = status_to_fields(protocol.GuardStatus(race_signal_age_s=0.03, model_healthy_s=1.2), protocol)
    assert change_key(a) == change_key(b)    # 좌표 NaN 이어도 같음
    c = status_to_fields(protocol.GuardStatus(race_phase=CODE['RACING']), protocol)
    assert change_key(a) != change_key(c)


def test_publish_gate_period_and_immediate_change():
    gate = PublishGate(0.02)
    a = status_to_fields(protocol.GuardStatus(), protocol)
    b = status_to_fields(protocol.GuardStatus(race_phase=CODE['RACING']), protocol)
    assert gate.should_publish(a, 0.000)       # 첫 값
    assert not gate.should_publish(a, 0.010)   # 안 바뀜·주기 전
    assert gate.should_publish(a, 0.021)       # 주기
    assert gate.should_publish(b, 0.023)       # 바뀜 → 주기 안 기다림
    assert not gate.should_publish(b, 0.030)
