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
    assert lm.to_pedals(1.3)[0] == pytest.approx(30.0)
    assert lm.to_pedals(5.0)[0] == pytest.approx(30.0)   # 가속 상한


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
