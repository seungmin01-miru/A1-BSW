"""fit_long_map·fit_circle — 합성 로그로 오염 검사·차단·추천·원 맞춤을 시험한다. CAN·ROS 없음. 합성 차량 값은 실측이 아니다."""
import csv
import math
import os
import sys

import numpy as np
import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_circle as fc  # noqa: E402
import fit_long_map as fl  # noqa: E402

BINS = [-3, -1, 0, 0.5, 1.0]
TABLE = {'version': 1, 'source': '시험', 'speed_bins_kph': [0, 10, 20], 'accel_bins_mps2': BINS,
         'pedal_pct': {0: [-80, -40, 0, 80, 90], 10: [-80, -40, 0, 80, 90], 20: [-80, -40, 0, 80, 90]},
         'deadband_mps2': 0.1, 'deadband_off_mps2': 0.03}


def plant(acc, brk):
    """표와 맞는 합성 차량: 80 % → 0.5, 90 % → 1.0, 브레이크 40 % → −1, 80 % → −3 m/s²."""
    if acc:
        return np.interp(acc, [80, 90], [0.5, 1.0])
    return -np.interp(brk, [0, 40, 80], [0, 1.0, 3.0])


def script(repeats=4):
    """(가속 %, 브레이크 %, 시간 s) 열 — 10 km/h 근처를 오간다."""
    out = [(90, 0, 3.0)]
    for _ in range(repeats):
        out += [(80, 0, 2.0), (0, 40, 1.5), (90, 0, 2.0), (0, 80, 1.0), (90, 0, 2.5)]
    return out


def make_logs(tmp, seq=None, app_at=None, foreign_at=None, fix=4, lag=0.0, pitch=0.2, gain=1.0, table=TABLE, v0=5.0):
    """CAN 로그(candump 형식)와 GNSS CSV 를 만든다."""
    seq = seq or script()
    t, v = 1000.0, v0 / 3.6
    lines = []
    gn = []
    dt = 0.02
    k = 0
    for acc, brk, dur in seq:
        for _ in range(int(dur / dt)):
            a = plant(acc, brk) * gain
            v = max(0.0, v + a * dt)
            raw_v = int(round(v * 3.6 * 10)) & 0xFFF
            d210 = (0).to_bytes(2, 'little') + int(brk).to_bytes(2, 'little') + bytes([int(acc), 7, 0, 0])
            d201 = (raw_v | (raw_v << 24)).to_bytes(8, 'little')
            lines.append(f'({t:.6f}) vcan0 210#{d210.hex().upper()}')
            lines.append(f'({t + 0.001:.6f}) vcan0 201#{d201.hex().upper()}')
            en = 1 if app_at is not None and app_at <= t - 1000 < app_at + 1 else 0
            lines.append(f'({t + 0.002:.6f}) vcan0 156#{bytes([en, 0x96, en, 0, 0, 0, 0, 0]).hex().upper()}')
            if foreign_at is not None and foreign_at <= t - 1000 < foreign_at + 3:
                lines.append(f'({t + 0.003:.6f}) vcan0 210#{d210.hex().upper()}')
                lines.append(f'({t + 0.013:.6f}) vcan0 210#{d210.hex().upper()}')
            gn.append((t + lag, v, fix, pitch))
            t += dt
            k += 1
    can = os.path.join(tmp, 'c.log')
    with open(can, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    gp = os.path.join(tmp, 'g.csv')
    with open(gp, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'speed_mps', 'fix', 'pitch_deg'])
        w.writerows(gn)
    ev = os.path.join(tmp, 'ev.csv')
    with open(ev, 'w') as f:
        f.write('wall_s,mono_s,event,detail\n')
    cur = os.path.join(tmp, 'map.yaml')
    with open(cur, 'w', encoding='utf-8') as f:
        yaml.safe_dump({'long_map': table}, f, allow_unicode=True)
    return can, gp, ev, cur


def run(tmp, **kw):
    can, gp, ev, cur = make_logs(str(tmp), **kw)
    out = os.path.join(str(tmp), 'o')
    rc = fl.main(['--can', can, '--gnss-csv', gp, '--events', ev, '--current', cur, '--out', out])
    return rc, out


def blocked_text(out):
    return open(os.path.join(out, 'UPDATE_BLOCKED.md'), encoding='utf-8').read()


def test_clean_log_recommends_and_table_loads(tmp_path):
    rc, out = run(tmp_path)
    assert rc == 0, open(os.path.join(out, 'report.md'), encoding='utf-8').read()
    with open(os.path.join(out, 'long_map_recommended.yaml'), encoding='utf-8') as f:
        rec = yaml.safe_load(f)['long_map']
    assert rec['version'] == 2
    fl.LongMap2D(rec)
    assert not os.path.exists(os.path.join(out, 'UPDATE_BLOCKED.md'))
    # 표와 맞는 차량이면 정확도 ±20 % 안
    s = __import__('json').load(open(os.path.join(out, 'summary.json'), encoding='utf-8'))
    assert s['accuracy'] and all(r['ok'] for r in s['accuracy'])


def test_never_touches_current_table(tmp_path):
    can, gp, ev, cur = make_logs(str(tmp_path))
    before = open(cur).read()
    fl.main(['--can', can, '--gnss-csv', gp, '--events', ev, '--current', cur, '--out', str(tmp_path / 'o')])
    assert open(cur).read() == before


@pytest.mark.parametrize('kw,word', [
    (dict(app_at=8.0), '다른 조작(CAN)'),
    (dict(foreign_at=8.0), '다른 조작(CAN)'),
    (dict(fix=1), 'GNSS 품질'),
    (dict(lag=0.2), '시간 어긋남'),
    (dict(pitch=3.0), '경사'),
    (dict(table=dict(TABLE, pedal_pct={k: [-80, -40, 0, 60, 70] for k in (0, 10, 20)})), '급변'),
])
def test_contamination_blocks_exit_3(tmp_path, kw, word):
    rc, out = run(tmp_path, **kw)
    assert rc == 3
    assert word in blocked_text(out)
    assert not os.path.exists(os.path.join(out, 'long_map_recommended.yaml'))


def test_too_few_samples_blocks(tmp_path):
    rc, out = run(tmp_path, seq=[(90, 0, 3.0), (80, 0, 2.0), (0, 40, 1.5)] * 1 + [(90, 0, 20.0)] * 0)
    assert rc == 3
    text = blocked_text(out)
    assert '표본 부족' in text or '시간 어긋남' in text


def test_no_events_file_is_not_a_pass(tmp_path):
    can, gp, _, cur = make_logs(str(tmp_path))
    out = str(tmp_path / 'o')
    assert fl.main(['--can', can, '--gnss-csv', gp, '--current', cur, '--out', out]) == 3
    assert 'ESTOP' in blocked_text(out)


def test_physical_limit_blocks(tmp_path):
    rc, out = run(tmp_path, gain=3.0)
    assert rc == 3
    assert '물리 한계' in blocked_text(out)


def test_preview_exit_0_without_recommendation(tmp_path):
    can, _, ev, cur = make_logs(str(tmp_path))
    out = str(tmp_path / 'o')
    assert fl.main(['--can', can, '--can-only-preview', '--events', ev, '--current', cur, '--out', out]) == 0
    assert not os.path.exists(os.path.join(out, 'long_map_recommended.yaml'))
    assert os.path.exists(os.path.join(out, 'cells.csv'))


def test_preview_still_blocks_app(tmp_path):
    can, _, ev, cur = make_logs(str(tmp_path), app_at=8.0)
    assert fl.main(['--can', can, '--can-only-preview', '--events', ev, '--current', cur, '--out', str(tmp_path / 'o')]) == 3


def test_bad_input_exit_2(tmp_path):
    assert fl.main(['--can', str(tmp_path / 'none.log'), '--can-only-preview', '--out', str(tmp_path / 'o')]) == 2


def test_recommend_clears_old_cells_that_break_monotonic():
    """측정 칸만 바꾸면 옛 복사값과 뒤집힐 수 있다 → 그 옛 칸은 미측정으로."""
    cfg = dict(TABLE, accel_bins_mps2=[-1, 0, 0.5, 0.8, 1.0], pedal_pct={0: [-40, 0, 80, 100, 100], 10: [-40, 0, 80, 100, 100], 20: [-40, 0, 80, 100, 100]})
    cells = {(10.0, 4): [85.0, 85.0, 85.0]}     # 1.0 m/s² 에 85 % 면 옛 0.8 칸 100 % 가 뒤집힌다
    new, diffs = fl.recommend(cfg, cells)
    assert new[10][4] == 85.0 and new[10][3] is None
    assert any(d['accel_mps2'] == 1.0 for d in diffs)
    fl.LongMap2D(dict(cfg, pedal_pct=new))


# ── fit_circle ─────────────────────────────────────────────────
def circle_points(r, n=400, sign=1, noise=0.0, turns=1.2, seed=1):
    rng = np.random.default_rng(seed)
    th = np.linspace(0, sign * 2 * math.pi * turns, n)
    return 5 + r * np.cos(th) + rng.normal(0, noise, n), -3 + r * np.sin(th) + rng.normal(0, noise, n)


def test_fit_circle_xy_recovers_radius_and_sign():
    x, y = circle_points(11.6, noise=0.02)
    cx, cy, r, rms = fc.fit_circle_xy(x, y)
    assert r == pytest.approx(11.6, abs=0.02) and cx == pytest.approx(5, abs=0.02) and rms < 0.05
    assert fc.arc_signed_deg(x, y, cx, cy) == pytest.approx(432, abs=2)
    x, y = circle_points(11.6, sign=-1)
    assert fc.arc_signed_deg(x, y, *fc.fit_circle_xy(x, y)[:2]) < 0


def write_run(tmp, name, handle, r, sign, t0, v_kph=6.0, turns=1.2, label='원선회'):
    v = v_kph / 3.6
    dur = turns * 2 * math.pi * r / v
    ts = np.arange(0, dur, 0.02)
    th = sign * ts * v / r
    gx, gy = r * np.cos(th), r * np.sin(th)
    with open(os.path.join(tmp, f'{name}_cmd.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['wall_s', 'phase', 'label', 'steer_cmd_deg', 'steer_pos_deg', 'speed_kph'])
        for tt in ts:
            w.writerow([t0 + tt, 'run', label, handle, handle + 0.5, v_kph])
    return np.c_[t0 + ts, gx, gy]


def test_main_recovers_zero_and_ratio(tmp_path):
    runs = tmp_path / 'runs'
    runs.mkdir()
    gn = []
    t0 = 1000.0
    h0 = -10.0            # 직진 핸들각
    for name, handle, in (('a', 60.0), ('b', -80.0), ('c', 150.0), ('d', -150.0)):
        tire = (handle - h0) / 10.0        # 핸들 = 10·타이어 − 10
        r = 2.36 / math.tan(math.radians(abs(tire)))
        gn.append(write_run(str(runs), name, handle - 0.5, r, 1 if tire > 0 else -1, t0))
        t0 += 500
    gp = tmp_path / 'g.csv'
    with open(gp, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'x_m', 'y_m'])
        for arr in gn:
            w.writerows(arr)
    out = tmp_path / 'o'
    assert fc.main(['--run-dir', str(runs), '--gnss-csv', str(gp), '--out', str(out)]) == 0
    rows = list(csv.DictReader(open(out / 'circle_fit.csv')))
    assert len(rows) == 4
    assert float(rows[2]['tire_deg']) == pytest.approx(16.0, abs=0.1)
    assert float(rows[3]['tire_deg']) == pytest.approx(-14.0, abs=0.1)
    text = open(out / 'report.md', encoding='utf-8').read()
    assert '직진 핸들 -10.' in text or '직진 핸들 -9.9' in text
    rec = yaml.safe_load(open(out / 'steer_map_recommended.yaml'))['steer_map']
    assert rec['tire_deg'] == sorted(rec['tire_deg']) and 0.0 in rec['tire_deg']


def test_short_arc_excluded(tmp_path):
    runs = tmp_path / 'runs'
    runs.mkdir()
    arr = write_run(str(runs), 'a', 60.0, 20.0, 1, 1000.0, turns=0.5)
    gp = tmp_path / 'g.csv'
    with open(gp, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'x_m', 'y_m'])
        w.writerows(arr)
    assert fc.main(['--run-dir', str(runs), '--gnss-csv', str(gp), '--out', str(tmp_path / 'o')]) == 3
    assert '호' in open(tmp_path / 'o' / 'report.md', encoding='utf-8').read()


def test_no_runs_exit_2(tmp_path):
    assert fc.main(['--run-dir', str(tmp_path), '--gnss-csv', 'x', '--out', str(tmp_path / 'o')]) == 2
