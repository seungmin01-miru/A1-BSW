"""profile_lib 단위 시험 — env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest bsw_test/common/scripts -q"""
import glob
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
