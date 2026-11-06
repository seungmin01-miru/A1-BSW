"""profile_lib 단위 시험 — env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest bsw_test/common/scripts -q"""
import glob
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import profile_lib as pl  # noqa: E402

PROFILES = sorted(glob.glob(os.path.join(HERE, '..', '..', '02_LON_profile', 'profiles', '*.yaml')))


def base(**kw):
    cfg = {'limits': {'steer_deg': 30, 'brake_pct': 100, 'acc_pct': 20, 'max_speed_kph': 10},
           'segments': [{'type': 'set', 'brake': 0, 't': 0.1}]}
    cfg.update(kw)
    return cfg


def vals(prof, ax):
    return [s[4][ax] for s in prof.samples()]


def test_step_ramp_values():
    p = pl.build(base(segments=[{'type': 'set', 'brake': 0},
                                {'type': 'step', 'axis': 'acc', 'to': 10, 't': 0.1},
                                {'type': 'ramp', 'axis': 'acc', 'to': 0, 't': 0.1}]))
    a = vals(p, 'acc')
    assert a[:5] == [10] * 5
    assert a[5] == pytest.approx(10) and a[6] == pytest.approx(8) and a[-1] == pytest.approx(0)
    assert pl.validate(p) == []


def test_ramp_rate_duration():
    p = pl.build(base(segments=[{'type': 'ramp', 'axis': 'steer', 'to': 20, 'rate': 10}]))
    assert p.duration_s == pytest.approx(2.0)
    assert vals(p, 'steer')[-1] == pytest.approx(20)


def test_trap_shape():
    p = pl.build(base(segments=[{'type': 'set', 'brake': 0},
                                {'type': 'trap', 'axis': 'acc', 'peak': 8, 'up': 1, 'hold': 1, 'down': 1}]))
    a = vals(p, 'acc')
    assert max(a) == pytest.approx(8) and a[0] == 0 and a[-1] == pytest.approx(0)
    assert p.duration_s == pytest.approx(3)


def test_sine_and_rate_limit():
    ok = pl.build(base(segments=[{'type': 'sine', 'axis': 'steer', 'amp': 10, 'freq': 0.5, 'cycles': 2}]))
    s = vals(ok, 'steer')
    assert max(s) == pytest.approx(10, abs=0.05) and min(s) == pytest.approx(-10, abs=0.05)
    assert abs(s[-1]) < 1e-6
    assert pl.validate(ok) == []
    bad = pl.build(base(segments=[{'type': 'sine', 'axis': 'steer', 'amp': 30, 'freq': 1.0, 'cycles': 1}]))
    assert any('°/s' in e for e in pl.validate(bad))


def test_limits_violation_detected():
    p = pl.build(base(segments=[{'type': 'set', 'brake': 0}, {'type': 'step', 'axis': 'acc', 'to': 25, 't': 0.1}]))
    assert any('가속' in e for e in pl.validate(p))


def test_steer_step_rejected():
    p = pl.build(base(segments=[{'type': 'step', 'axis': 'steer', 'to': 20, 't': 0.1}]))
    assert any('조향 속도' in e for e in pl.validate(p))


def test_acc_brake_overlap_rejected():
    p = pl.build(base(segments=[{'type': 'step', 'axis': 'acc', 'to': 5, 't': 0.1}]))   # start 브레이크 30 그대로
    assert any('동시' in e for e in pl.validate(p))


@pytest.mark.parametrize('limits', [{'max_speed_kph': 40}, {'steer_deg': 200}, {'acc_pct': 0}])
def test_hard_limits(limits):
    with pytest.raises(pl.ProfileError):
        pl.build(base(limits=limits))


def test_vars_and_override():
    cfg = base(vars={'a': 5}, segments=[{'type': 'set', 'brake': 0}, {'type': 'step', 'axis': 'acc', 'to': '$a', 't': 0.1}])
    assert max(vals(pl.build(cfg), 'acc')) == 5
    assert max(vals(pl.build(cfg, {'a': 7}), 'acc')) == 7
    with pytest.raises(pl.ProfileError):
        pl.build(cfg, {'typo': 1})
    with pytest.raises(pl.ProfileError):
        pl.build(base(segments=[{'type': 'step', 'axis': 'acc', 'to': '$nope'}]))


def test_start_acc_must_be_zero():
    with pytest.raises(pl.ProfileError):
        pl.build(base(start={'acc': 5}))


def test_deterministic():
    cfg = base(segments=[{'type': 'set', 'brake': 0},
                         {'type': 'sine', 'axis': 'steer', 'amp': 5, 'freq': 0.3, 'cycles': 1}])
    assert pl.build(cfg).samples() == pl.build(cfg).samples()


def test_end_command():
    e = pl.end_command({'steer': 12, 'brake': 0, 'acc': 9}, 100, 2.5, 0)
    assert e == {'steer': 12, 'brake': 0, 'acc': 0}
    assert pl.end_command({'steer': 0, 'brake': 0, 'acc': 9}, 100, 2.5, 1.25)['brake'] == pytest.approx(50)
    assert pl.end_command({'steer': 0, 'brake': 0, 'acc': 9}, 100, 2.5, 9)['brake'] == 100
    assert pl.end_command({'steer': 0, 'brake': 100, 'acc': 0}, 82, 2.5, 0)['brake'] == 100   # 이미 세면 유지


def test_until_speed_kph_capped():
    with pytest.raises(pl.ProfileError):
        pl.build(base(segments=[{'type': 'until_speed', 'kph': 20, 'timeout': 5}]))


@pytest.mark.parametrize('path', PROFILES, ids=[os.path.basename(p) for p in PROFILES])
def test_shipped_profiles_pass(path):
    yaml = pytest.importorskip('yaml')
    with open(path, encoding='utf-8') as f:
        prof = pl.build(yaml.safe_load(f))
    assert pl.validate(prof) == []
    assert prof.limits['max_speed_kph'] <= 30


# ── 2026-11-04: 폐루프·코스·오프셋 ──────────────────────────────

def ctrl(**kw):
    p = dict(pl.SPEED_CTRL_DEFAULTS)
    p.update(kw)
    return pl.SpeedController(pl.check_speed_ctrl(p), acc_limit=30, brake_limit=100)


def test_speed_controller_never_acc_and_brake():
    c = ctrl()
    v = 0.0
    seq = [(10, 0)] * 200 + [(10, 0)] * 50 + [(3, 0)] * 100 + [(0, 0)] * 100
    for vr, ar in seq:
        acc, br = c.step(v, vr, ar, 0.02)
        assert not (acc > 0 and br > 0)
        assert acc == int(acc) and br == int(br)          # 0x210 정수 %
        v = max(0.0, v + (acc * 0.05 - br * 0.08 - 0.1) * 0.02 * 3.6)


def test_speed_controller_brakes_on_overspeed_and_stop():
    c = ctrl()
    c.reset(15.0)
    acc, br = 0, 0
    for _ in range(50):
        acc, br = c.step(15.0, 10.0, 0.0, 0.02)
    assert acc == 0 and br > 0 and c.mode == 'brake'
    c.reset(0.5)
    for _ in range(100):
        acc, br = c.step(0.5, 0.0, 0.0, 0.02)
    assert acc == 0 and br >= 40      # 목표 0 + 거의 섬 → stop_brake_pct


def test_speed_controller_limits_and_start():
    c = pl.SpeedController(pl.check_speed_ctrl(dict(pl.SPEED_CTRL_DEFAULTS)), acc_limit=12, brake_limit=60)
    outs = [c.step(0.0, 25.0, 0.0, 0.02) for _ in range(300)]
    assert max(a for a, _ in outs) <= 12
    assert outs[0][0] > 0                  # 정지에서 바로 출발(start_pct, 올림 변화율 안)


def test_speed_ctrl_bad_params():
    with pytest.raises(pl.ProfileError):
        pl.check_speed_ctrl(dict(pl.SPEED_CTRL_DEFAULTS, nope=1))
    with pytest.raises(pl.ProfileError):
        pl.check_speed_ctrl(dict(pl.SPEED_CTRL_DEFAULTS, hold_kph=[0, 5], hold_pct=[1]))
    with pytest.raises(pl.ProfileError):
        pl.check_speed_ctrl(dict(pl.SPEED_CTRL_DEFAULTS, brake_off_kph=3.0))


def test_speed_segment_target_ramp():
    p = pl.build(base(limits={'max_speed_kph': 15, 'acc_pct': 20, 'steer_deg': 30},
                      segments=[{'type': 'speed', 'from': 0, 'to': 10, 'rate': 2, 't': 8}]))
    pr = p.prims[0]
    assert pr.closed_loop and p.needs_speed
    assert pr.target(0) == (0, 2) and pr.target(2.5) == (5, 2) and pr.target(6) == (10, 0)
    with pytest.raises(pl.ProfileError):   # t 가 램프보다 짧음
        pl.build(base(limits={'max_speed_kph': 15}, segments=[{'type': 'speed', 'to': 10, 'rate': 2, 't': 3}]))
    with pytest.raises(pl.ProfileError):   # 목표가 상한 − 1 위
        pl.build(base(limits={'max_speed_kph': 10}, segments=[{'type': 'speed', 'to': 10, 't': 3}]))


def test_steer_ramp_inherits_closed_loop_and_acc_breaks_it():
    p = pl.build(base(limits={'max_speed_kph': 10, 'steer_deg': 150, 'acc_pct': 10},
                      segments=[{'type': 'speed', 'from': 0, 'to': 5, 't': 2},
                                {'type': 'ramp', 'axis': 'steer', 'to': 90, 'rate': 10},
                                {'type': 'set', 'acc': 0, 'brake': 40, 't': 1}]))
    assert [x.closed_loop for x in p.prims] == [True, True, False]
    assert p.prims[1].target(0) == (5, 0)
    assert pl.validate(p) == []


def test_steer_offset_in_validate():
    cfg = base(limits={'steer_deg': 20, 'max_speed_kph': 10}, segments=[{'type': 'ramp', 'axis': 'steer', 'to': 19, 'rate': 10}])
    assert pl.validate(pl.build(cfg)) == []
    assert any('오프셋' in e for e in pl.validate(pl.build(cfg, steer_offset=2.0)))
    with pytest.raises(pl.ProfileError):
        pl.build(cfg, steer_offset=20)


def test_course_and_stop_distance():
    p = pl.build(base(course={'length_m': 50}))
    assert p.course['length_m'] == 50 and p.needs_speed
    c = {'decel_mps2': 1.5, 'delay_s': 0.3}
    assert pl.stop_distance_m(0, c, 2.5) == 0
    d20 = pl.stop_distance_m(20, c, 2.5)      # 5.56 m/s × 1.55 s + 5.56²/3 ≈ 18.9 m
    assert d20 == pytest.approx(20 / 3.6 * 1.55 + (20 / 3.6) ** 2 / 3.0)
    with pytest.raises(pl.ProfileError):
        pl.build(base(course={'length_m': 2}))
