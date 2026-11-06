"""conversion.py 단위 테스트 — ROS 없이 실행: python3 -m pytest ros2_ws/src/a1_control_adapter/test"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from a1_control_adapter.conversion import (  # noqa: E402
    InputLimits, LongMap, SlewLimiter, SteerMap, interp, validate)

import yaml  # noqa: E402

CFG = os.path.join(os.path.dirname(__file__), '..', 'config', 'track.yaml')


def track_params():
    with open(CFG) as f:
        return yaml.safe_load(f)['a1_cmd_adapter']['ros__parameters']


def maps_from_yaml():
    p = track_params()
    s = SteerMap(p['steer_map']['tire_deg'], p['steer_map']['wheel_deg'], p['steer_sign'], p['steer_wheel_limit_deg'])
    lm = LongMap(p['accel_map']['accel_mps2'], p['accel_map']['acc_pct'], p['brake_map']['decel_mps2'],
                 p['brake_map']['brake_pct'], p['long_deadband_mps2'], p['acc_max_pct'], p['brake_max_pct'])
    return s, lm


def hyst_map():
    p = track_params()
    return LongMap(p['accel_map']['accel_mps2'], p['accel_map']['acc_pct'], p['brake_map']['decel_mps2'],
                   p['brake_map']['brake_pct'], p['long_deadband_mps2'], p['acc_max_pct'], p['brake_max_pct'],
                   deadband_off_mps2=p['long_deadband_off_mps2'])


def test_interp_basic_and_saturation():
    xs, ys = [0.0, 1.0, 3.0], [0.0, 10.0, 30.0]
    assert interp(0.5, xs, ys) == pytest.approx(5.0)
    assert interp(2.0, xs, ys) == pytest.approx(20.0)
    assert interp(-5, xs, ys) == 0.0 and interp(99, xs, ys) == 30.0


def test_steer_ratio_and_clamp():
    s = SteerMap([-20.0, 0.0, 20.0], [-150.0, 0.0, 150.0])
    assert s.to_wheel(10.0) == pytest.approx(75.0)
    assert s.to_wheel(-4.0) == pytest.approx(-30.0)
    assert s.to_wheel(40.0) == 150.0          # 표 끝 포화
    s2 = SteerMap([-20.0, 0.0, 20.0], [-300.0, 0.0, 300.0], wheel_limit_deg=150.0)
    assert s2.to_wheel(20.0) == 150.0          # DBC 한계 클램프


def test_steer_sign_and_inverse():
    s = SteerMap([-20.0, 0.0, 20.0], [-150.0, 0.0, 150.0], sign=-1.0)
    assert s.to_wheel(10.0) == pytest.approx(-75.0)
    assert s.to_tire(s.to_wheel(7.0)) == pytest.approx(7.0)


def test_steer_asymmetric_table():
    s = SteerMap([-20.0, 0.0, 20.0], [-160.0, 0.0, 140.0])
    assert s.to_wheel(10.0) == pytest.approx(70.0)
    assert s.to_wheel(-10.0) == pytest.approx(-80.0)


@pytest.mark.parametrize('tire,wheel', [([0, 0, 1], [0, 1, 2]), ([0, 1], [0]), ([0, 1, 2], [0, 5, 3])])
def test_steer_map_rejects_bad_tables(tire, wheel):
    with pytest.raises(ValueError):
        SteerMap(tire, wheel)


def test_long_deadband_and_never_both():
    _, lm = maps_from_yaml()
    assert lm.to_pedals(0.0) == (0.0, 0.0)
    assert lm.to_pedals(0.05) == (0.0, 0.0)
    assert lm.to_pedals(-0.05) == (0.0, 0.0)
    for a in [x / 10 for x in range(-80, 60)]:
        acc, brake = lm.to_pedals(a)
        assert not (acc > 0 and brake > 0)
        assert 0 <= acc <= lm.acc_max_pct and 0 <= brake <= lm.brake_max_pct


def test_long_values_from_track_yaml():
    _, lm = maps_from_yaml()
    assert lm.to_pedals(-4.0) == (0.0, 100.0)
    assert lm.to_pedals(-9.0) == (0.0, 100.0)  # 표 밖 포화
    assert lm.to_pedals(-1.0) == (0.0, pytest.approx(40.0))
    assert lm.to_pedals(0.13)[0] == pytest.approx(80.0)   # 11-04 실차
    assert lm.to_pedals(0.57)[0] == pytest.approx(100.0)
    assert lm.to_pedals(5.0)[0] == pytest.approx(100.0)   # 표 끝 포화


def test_long_caps():
    lm = LongMap([0.1, 2.0], [0.0, 100.0], [0.1, 4.0], [0.0, 100.0], acc_max_pct=20.0, brake_max_pct=60.0)
    assert lm.to_pedals(2.0) == (20.0, 0.0)
    assert lm.to_pedals(-4.0) == (0.0, 60.0)
    with pytest.raises(ValueError):
        LongMap([0.1, 2.0], [0.0, 120.0], [0.1, 4.0], [0.0, 100.0])


def test_validate():
    lim = InputLimits(15.0, 45.0)
    assert validate(1.0, 5.0, lim) is None
    assert validate(math.nan, 0.0, lim)
    assert validate(0.0, math.inf, lim)
    assert validate(20.0, 0.0, lim)
    assert validate(0.0, -50.0, lim)


def test_slew_limiter():
    s = SlewLimiter(120.0)
    s.reset(0.0)
    assert s.step(100.0, 0.02) == pytest.approx(2.4)
    for _ in range(100):
        y = s.step(100.0, 0.02)
    assert y == pytest.approx(100.0)
    s.reset(None)
    assert s.step(-30.0, 0.02) == -30.0          # 시작점 모르면 바로 목표
    off = SlewLimiter(0.0)
    off.reset(0.0)
    assert off.step(50.0, 0.02) == 50.0


def test_track_yaml_loads():
    maps_from_yaml()


# ── 2026-11-06 엔진룸 1차 업데이트(지시서 2.6.2) ────────────────────────────
def test_track_steer_map_v1_monotonic_and_ends():
    s, _ = maps_from_yaml()
    assert s.to_wheel(0.0) == pytest.approx(-9.0)            # 영점 보정
    assert s.to_wheel(11.5) == 150.0 and s.to_wheel(-11.5) == -150.0
    assert s.to_wheel(20.0) == 150.0 and s.to_wheel(-20.0) == -150.0   # 표 밖 포화


def test_track_steer_map_v1_round_trip():
    s, _ = maps_from_yaml()
    for i in range(-115, 116):
        t = i / 10
        assert s.to_tire(s.to_wheel(t)) == pytest.approx(t, abs=0.1)


def test_hysteresis_alternating_input_switches_at_most_once():
    """0.05 ↔ 0.15 를 20 ms 마다 번갈아 넣으면 전환 ≤ 1 회(지시서 단위 시험)."""
    lm = hyst_map()
    out = [lm.to_pedals(0.05 if i % 2 else 0.15)[0] for i in range(500)]
    assert sum(1 for a, b in zip(out, out[1:]) if (a > 0) != (b > 0)) <= 1
    assert out[0] > 0 and out[-1] >= 75.0


def test_old_map_toggles_on_same_input():
    _, lm = maps_from_yaml()                                 # deadband_off 없음 = 예전 동작
    out = [lm.to_pedals(0.05 if i % 2 else 0.15)[0] for i in range(100)]
    assert sum(1 for a, b in zip(out, out[1:]) if (a > 0) != (b > 0)) == 99


def test_hysteresis_entry_and_exit():
    lm = hyst_map()
    assert lm.to_pedals(0.05) == (0.0, 0.0)                  # 0 에서 0.05 는 아직 안 켜짐
    assert lm.to_pedals(0.11)[0] > 0
    assert lm.to_pedals(0.04)[0] == pytest.approx(75.0)      # 유지: 표 끝값
    assert lm.to_pedals(0.029) == (0.0, 0.0)                 # 이탈
    assert lm.to_pedals(0.05) == (0.0, 0.0)                  # 재진입은 0.10 을 넘어야


def test_hysteresis_brake_side():
    lm = hyst_map()
    assert lm.to_pedals(-0.05) == (0.0, 0.0)
    assert lm.to_pedals(-1.0)[1] == pytest.approx(40.0)
    assert lm.to_pedals(-0.05) == (0.0, 0.0)                 # 브레이크 표 0.1 → 0 %: 유지 중에도 0
    assert lm.to_pedals(-1.0)[1] == pytest.approx(40.0)
    assert lm.to_pedals(-0.02) == (0.0, 0.0)


def test_hysteresis_never_both_and_direct_flip():
    lm = hyst_map()
    assert lm.to_pedals(0.5)[1] == 0.0
    assert lm.to_pedals(-0.5)[0] == 0.0                      # 가속 모드에서 바로 브레이크
    assert lm.to_pedals(0.5)[1] == 0.0
    assert lm.to_pedals(-0.05) == (0.0, 0.0)                 # 가속 모드에서 반대쪽 값 → 0
    for a in [x / 100 for x in range(-300, 200, 7)]:
        acc, brake = lm.to_pedals(a)
        assert not (acc > 0 and brake > 0)


def test_hysteresis_reset_on_arm():
    lm = hyst_map()
    lm.to_pedals(0.5)
    lm.reset()
    assert lm.to_pedals(0.05) == (0.0, 0.0)


def test_step_inputs_same_with_or_without_hysteresis_outside_band():
    """기존 계단 입력 회귀: 0.03~0.10 밖의 값은 두 표가 같은 출력."""
    _, old = maps_from_yaml()
    new = hyst_map()
    for a in (0.0, 0.3, 0.6, 0.0, -1.0, -3.0, 0.0, 1.5, -4.0, 0.0):
        assert new.to_pedals(a) == old.to_pedals(a)


def test_bad_off_value_rejected():
    with pytest.raises(ValueError):
        LongMap([0.1, 1.0], [75.0, 100.0], [0.1, 4.0], [0.0, 100.0], deadband_mps2=0.1, deadband_off_mps2=0.2)
