#!/usr/bin/env python3
"""보고서 데이터시트용 CSV 만들기 — 11-04 CAN 원본(zstd)을 물리값으로 풀어 PlotJuggler 가 여는 CSV 로 낸다.

사용:  python3 make_data.py <저장소 루트> <보고서 폴더>
입력:  measurements/2026-11-04_drive_log/raw_can/*/candump_kv0.log.zst, DBC/A1_dbc_2_fixed.dbc, track.yaml, tables/*.csv
출력:  data/pj/*.csv — 첫 열 t(세션 시작 기준 s). 값은 0x200·0x201·0x210 을 20 ms 격자에 직전 값으로 맞춤(zero-order hold).
의존:  표준 라이브러리 + numpy, pyyaml, zstd 명령. CAN·차량에 닿지 않는다.
"""
import csv
import os
import subprocess
import sys

import numpy as np
import yaml

ROOT, REP = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, os.path.join(ROOT, 'tools', 'signal_audit'))
import dbc_lite  # noqa: E402

RAW = os.path.join(ROOT, 'measurements/2026-11-04_drive_log/raw_can')
OUT = os.path.join(REP, 'data', 'pj')
os.makedirs(OUT, exist_ok=True)
DB = dbc_lite.load(os.path.join(ROOT, 'DBC', 'A1_dbc_2_fixed.dbc'))
DT = 0.02


def read_log(session):
    txt = subprocess.run(['zstd', '-dc', os.path.join(RAW, session, 'candump_kv0.log.zst')],
                         capture_output=True, text=True, check=True).stdout
    frames = {0x200: [], 0x201: [], 0x210: []}
    t0 = None
    for ln in txt.splitlines():
        p = ln.split()
        if len(p) < 3 or '#' not in p[2]:
            continue
        i, d = p[2].split('#')
        t = float(p[0][1:-1])
        t0 = t if t0 is None else t0
        i = int(i, 16)
        if i in frames:
            frames[i].append((t - t0, bytes.fromhex(d)))
    return frames


def signals(frames):
    """dict 이름 → (t, 값) 배열. 단위는 이름에 적는다."""
    out = {}
    c = [(t, DB[0x210].decode(d)) for t, d in frames[0x210]]
    tc = np.array([t for t, _ in c])
    out['cmd_steer_handle_deg'] = (tc, np.array([x['steer_command'] for _, x in c]))
    out['cmd_brake_pct'] = (tc, np.array([x['break_command'] for _, x in c]))
    out['cmd_acc_pct'] = (tc, np.array([x['acc_command'] for _, x in c]))
    s = [(t, DB[0x200].decode(d)) for t, d in frames[0x200]]
    ts = np.array([t for t, _ in s])
    out['pos_steer_handle_deg'] = (ts, np.array([x['steer_postion'] for _, x in s]))
    out['pos_brake_pct'] = (ts, np.array([x['break_postion'] for _, x in s]))
    w = [(t, DB[0x201].decode(d)) for t, d in frames[0x201]]
    tw = np.array([t for t, _ in w])
    out['wheel_speed_kph'] = (tw, np.array([(x['left_speed'] + x['right_speed']) / 2 for _, x in w]))
    return out


def write_window(name, sig, t0, t1, extra=None):
    grid = np.arange(t0, t1, DT)
    names = list(sig)
    cols = [_hold(grid, *sig[n]) for n in names]
    if extra:
        for n, fn in extra.items():
            names.append(n)
            cols.append(fn(dict(zip(names, cols))))
    with open(os.path.join(OUT, name + '.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t'] + names)
        for k, t in enumerate(grid):
            w.writerow([f'{t - t0:.3f}'] + [f'{c[k]:.3f}' for c in cols])
    print(name, len(grid), 'rows')


def _hold(grid, t, v):
    idx = np.clip(np.searchsorted(t, grid, side='right') - 1, 0, len(t) - 1)
    return v[idx]


def dv(t, v, win=0.5):
    """0x201 미분(참고값). 창 평균 → m/s²."""
    n = max(1, int(win / DT))
    ker = np.ones(n) / n
    sm = np.convolve(v / 3.6, ker, mode='same')
    return np.gradient(sm, DT)


def tire_old(x):
    return x / 7.5


def periods(frames):
    """주행 중 주기(ms): 프레임 간격 — bsw5 t=100~130 s."""
    t0, t1 = 100.0, 130.0
    cols = {}
    for cid, nm in ((0x200, 'dt_0x200_ms'), (0x201, 'dt_0x201_ms'), (0x210, 'dt_0x210_ms')):
        t = np.array([x for x, _ in frames[cid]])
        m = (t >= t0) & (t < t1)
        cols[nm] = (t[m][1:] - t0, np.diff(t[m]) * 1e3)
    grid = np.arange(0, t1 - t0, 0.02)
    with open(os.path.join(OUT, 'bsw5_period.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t'] + list(cols))
        for k, g in enumerate(grid):
            w.writerow([f'{g:.3f}'] + [f'{_hold(np.array([g]), *cols[n])[0]:.3f}' for n in cols])
    with open(os.path.join(REP, 'data', 'bsw5_period_stats.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'mean_ms', 'p1_ms', 'p99_ms', 'max_ms'])
        for n, (_, v) in cols.items():
            w.writerow([n, *(round(float(q), 2) for q in (v.mean(), np.percentile(v, 1), np.percentile(v, 99), v.max()))])


def _read_csv(path):
    with open(path, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return rows


def sil_merged():
    """SIL 결과를 PlotJuggler 한 파일로: 히스테리시스 전후(같은 입력 난수), 깃발 3방식, RES, 지연."""
    sil = os.path.join(REP, 'data', 'sil')
    a, b = (_read_csv(os.path.join(sil, f'cruise_{n}_head.csv')) for n in ('old', 'hysteresis'))
    with open(os.path.join(OUT, 'sil_cruise.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'in_accel_mps2', 'acc_old_pct', 'acc_hysteresis_pct'])
        for x, y in zip(a, b):
            w.writerow([x['t'], y['in_accel_mps2'], x['acc_pct'], y['acc_pct']])
    grid = np.arange(0.0, 6.0, 0.01)
    series = {}
    for tag, fn in (('level', 'S02_go'), ('pulse2', 'S04_go_pulse_2frames'), ('pulse1', 'S03_go_pulse_1frame'), ('flicker', 'S03b_go_flicker')):
        rows = _read_csv(os.path.join(sil, fn + '_state.csv'))
        t = np.array([float(r['t']) for r in rows])
        series[f'{tag}_sig_go'] = _hold(grid, t, np.array([float(r['sig_go']) for r in rows]))
        series[f'{tag}_seen_go'] = _hold(grid, t, np.array([float(r['seen_go']) for r in rows]))
    with open(os.path.join(OUT, 'sil_flags.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t'] + list(series))
        for k, g in enumerate(grid):
            w.writerow([f'{g:.2f}'] + [f'{series[n][k]:.0f}' for n in series])
    rows = _read_csv(os.path.join(sil, 'S09_res_then_release_state.csv'))
    with open(os.path.join(OUT, 'sil_res.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'res_active', 'seen_go', 'res_alive_ok', 'res_age_s'])
        for r in rows:
            w.writerow([r['t'], r['res_active'], r['seen_go'], r['res_alive_ok'], r['res_age_s']])
    rows = _read_csv(os.path.join(sil, 'S10_res_alive_stop_state.csv'))
    with open(os.path.join(OUT, 'sil_alive.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'res_alive_ok', 'res_age_s', 'race_signal_age_s'])
        for r in rows:
            w.writerow([r['t'], r['res_alive_ok'], max(float(r['res_age_s']), -1.0), r['race_signal_age_s']])
    i, l = (_read_csv(os.path.join(sil, f'latency_{n}.csv')) for n in ('idle', 'load'))
    with open(os.path.join(OUT, 'sil_latency.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'latency_idle_ms', 'latency_load_ms'])
        for k in range(min(len(i), len(l))):
            w.writerow([k, i[k]['latency_ms'], l[k]['latency_ms']])


def main():
    b5 = signals(read_log('auto_bsw5'))
    for name, a, b in (('bsw5_hairpin', 150, 165), ('bsw5_straight', 105, 120), ('bsw5_brake', 135, 150)):
        write_window(name, b5, a, b)
    # 한 바퀴 전체: 0.2 s 격자, PlotJuggler 전체 보기용
    global DT
    DT = 0.2
    write_window('bsw5_lap', b5, 15, 435)
    DT = 0.02
    # 전환 수 집계(분석 B 근거 + 원본 확인)
    tc, acc = b5['cmd_acc_pct']
    drive = (tc >= 15) & (tc < 435)
    sw = int(np.sum((acc[drive][1:] > 0) != (acc[drive][:-1] > 0)))
    with open(os.path.join(REP, 'data', 'bsw5_accel_switch.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['window', 'seconds', 'switches', 'per_min', 'note'])
        w.writerow(['bsw5 t=15~435 s', 420, sw, round(sw / 7.0, 1), '0x210 acc_command 0% <-> >0%, 0x210 원본 기준'])
        for a, b in ((105, 135), (225, 255), (345, 360)):
            m = (tc >= a) & (tc < b)
            n = int(np.sum((acc[m][1:] > 0) != (acc[m][:-1] > 0)))
            w.writerow([f't={a}~{b} s', b - a, n, round(n * 60 / (b - a), 1), '순항 구간'])
    # 빠른 시험: 가속 % 계단 → 바퀴 속도
    q = signals(read_log('quick_QUICK-LON_s5'))
    tq = q['cmd_acc_pct'][0]
    write_window('quick_lon_s5', q, float(tq[0]), float(tq[-1]))
    # 조향 표: 11-05 표본 점, 옛 표, 새 표 (t = 표본 번호, PlotJuggler XY 보기)
    with open(os.path.join(ROOT, 'ros2_ws/src/a1_control_adapter/config/track.yaml'), encoding='utf-8') as f:
        p = yaml.safe_load(f)['a1_cmd_adapter']['ros__parameters']
    tire, wheel = p['steer_map']['tire_deg'], p['steer_map']['wheel_deg']
    pts = np.loadtxt(os.path.join(ROOT, 'measurements/2026-11-04_drive_log/tables/steer_ratio_points.csv'),
                     delimiter=',', comments='#', skiprows=1)
    pts = pts[np.argsort(pts[:, 0])]
    # 조향 표 한 파일: t = 핸들각(가로축으로 쓴다). 측정은 5° 구간 평균(표본 3 개 미만 구간은 빈칸)
    with open(os.path.join(OUT, 'steer_xy.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'measured_tire_deg', 'new_map_tire_deg', 'old_map_tire_deg'])
        for h in np.arange(-150, 150.01, 2.5):
            m = np.abs(pts[:, 0] - h) < 2.5
            meas = f'{pts[m, 1].mean():.3f}' if m.sum() >= 3 else ''
            w.writerow([f'{h:.1f}', meas, f'{np.interp(h, wheel, tire):.3f}', f'{tire_old(h):.3f}'])
    periods(read_log('auto_bsw5'))
    sil_merged()
    # 표 정확도: 측정 점 기준 오차(타이어 °)
    bins = [(-155, -120), (-120, -80), (-80, -40), (-40, -5), (-5, 20), (20, 60), (60, 110)]
    with open(os.path.join(REP, 'data', 'steer_map_error.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['handle_bin_deg', 'n', 'measured_tire_mean', 'new_map_tire_mean', 'new_err', 'old_err'])
        for a, b in bins:
            m = (pts[:, 0] >= a) & (pts[:, 0] < b)
            if m.sum() == 0:
                continue
            h, t = pts[m, 0], pts[m, 1]
            w.writerow([f'{a}~{b}', int(m.sum()), f'{t.mean():.2f}', f'{np.interp(h, wheel, tire).mean():.2f}',
                        f'{(np.interp(h, wheel, tire) - t).mean():+.2f}', f'{(tire_old(h) - t).mean():+.2f}'])
        nw, ol = np.interp(pts[:, 0], wheel, tire) - pts[:, 1], tire_old(pts[:, 0]) - pts[:, 1]
        w.writerow(['전체 RMS', len(pts), '', '', f'{np.sqrt((nw**2).mean()):.2f}', f'{np.sqrt((ol**2).mean()):.2f}'])


if __name__ == '__main__':
    main()
