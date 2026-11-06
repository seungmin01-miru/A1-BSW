#!/usr/bin/env python3
"""보고서 데이터시트용 CSV — SIL 결과와 11-04 미리보기를 PlotJuggler 가 여는 CSV 로 낸다.

사용:  python3 make_data.py <저장소 루트> <보고서 폴더> <sil_engine_room --out 폴더> <r_race 부하 없음 폴더> <r_race 부하 폴더>
입력:  sil_engine_room 결과(map/status.csv, fit/candump.log·gnss.csv, fit/v2/segments.csv, circle/gnss.csv·runs/*_cmd.csv),
       measurements/2026-11-04_drive_log/raw_can/auto_bsw5(fit_long_map --can-only-preview 를 여기서 돌린다), config/long_map.yaml
출력:  data/pj/*.csv(첫 열 t), data/sil/*(판정표), data/bsw5_preview/*. CAN·차량에 닿지 않는다.
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import yaml

ROOT, REP, SIL, LAT0, LAT1 = [os.path.abspath(x) for x in sys.argv[1:6]]
PJ = os.path.join(REP, 'data', 'pj')
OUT_SIL = os.path.join(REP, 'data', 'sil')
os.makedirs(PJ, exist_ok=True)
os.makedirs(OUT_SIL, exist_ok=True)
sys.path.insert(0, os.path.join(ROOT, 'ros2_ws', 'src', 'a1_control_adapter'))
from a1_control_adapter.conversion import LongMap2D  # noqa: E402

LINE = re.compile(r'^\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)')


def rows(path):
    with open(path, encoding='utf-8') as f:
        return list(csv.DictReader(f))


def write(name, head, data):
    with open(os.path.join(PJ, name + '.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(data)
    print(name, len(data))


def hold(ts, vs, grid):
    i = np.clip(np.searchsorted(ts, grid, side='right') - 1, 0, len(ts) - 1)
    return np.asarray(vs)[i]


def table():
    with open(os.path.join(ROOT, 'ros2_ws/src/a1_control_adapter/config/long_map.yaml'), encoding='utf-8') as f:
        return yaml.safe_load(f)['long_map']


def map_sheets():
    """WP2: 어댑터 상태 + 표 곡선."""
    r = rows(os.path.join(SIL, 'map', 'status.csv'))
    write('sil_map', ['t', 'speed_used_kph', 'in_accel_mps2', 'out_acc_pct', 'out_brake_pct', 'extrapolated', 'speed_stale'],
          [[x['t'], x['speed_used_kph'], x['in_accel_mps2'], x['out_acc_pct'], x['out_brake_pct'], x['extrapolated'], x['speed_stale']] for x in r])
    cfg = table()
    m = LongMap2D(cfg)
    out = []
    for a in np.arange(-4.0, 1.301, 0.05):
        m.reset()
        acc, brk = m.to_pedals(float(a), 10.0)
        out.append([f'{a:.2f}', f'{acc - brk:.2f}'])
    write('long_map_v1', ['t', 'pedal_pct_v1'], out)


def fit_sheets():
    """WP3: 표 측정 SIL 의 시간 축 + 측정점 vs 표."""
    d = os.path.join(SIL, 'fit')
    c = {0x210: [], 0x201: []}
    with open(os.path.join(d, 'candump.log')) as f:
        for ln in f:
            m = LINE.match(ln)
            if not m:
                continue
            cid = int(m.group(2), 16)
            if cid in c:
                c[cid].append((float(m.group(1)), bytes.fromhex(m.group(3))))
    t210 = np.array([x[0] for x in c[0x210]])
    acc = np.array([x[1][4] for x in c[0x210]], float)
    brk = np.array([int.from_bytes(x[1][2:4], 'little') for x in c[0x210]], float)
    t201 = np.array([x[0] for x in c[0x201]])
    wv = np.array([(int.from_bytes(x[1], 'little') & 0xFFF) * 0.1 for x in c[0x201]])
    g = rows(os.path.join(d, 'gnss.csv'))
    gt = np.array([float(x['t']) for x in g])
    gv = np.array([float(x['speed_mps']) for x in g]) * 3.6
    t0 = t210[t210 > 0][0]
    grid = np.arange(t0, min(t210[-1], gt[-1]), 0.05)
    out = [[f'{t - t0:.2f}', f'{a:.0f}', f'{b:.0f}', f'{w:.2f}', f'{v:.2f}']
           for t, a, b, w, v in zip(grid, hold(t210, acc, grid), hold(t210, brk, grid), hold(t201, wv, grid), hold(gt, gv, grid))]
    write('sil_fit_time', ['t', 'cmd_acc_pct', 'cmd_brake_pct', 'wheel_kph', 'gnss_kph'], out)
    scatter('sil_fit_scatter', rows(os.path.join(d, 'v2', 'segments.csv')))


def scatter(name, seg):
    """가로 = 측정 가속도(t 열). 같은 값이 겹치지 않게 조금 벌린다."""
    cfg = table()
    pts = sorted((float(s['a_mps2']), float(s['pedal']) * (1 if s['axis'] == 'acc' else -1)) for s in seg)
    m = LongMap2D(cfg)
    data, last = [], -99.0
    for a, p in pts:
        a = max(a, last + 0.001)
        last = a
        m.reset()
        acc, brk = m.to_pedals(a, 10.0)
        data.append([f'{a:.3f}', f'{p:.1f}', f'{acc - brk:.1f}'])
    write(name, ['t', 'measured_pedal_pct', 'table_v1_pedal_pct'], data)


def circle_sheets():
    d = os.path.join(SIL, 'circle')
    g = rows(os.path.join(d, 'gnss.csv'))
    gt = np.array([float(x['t']) for x in g])
    gx = np.array([float(x['x_m']) for x in g])
    gy = np.array([float(x['y_m']) for x in g])
    runs = sorted(f for f in os.listdir(os.path.join(d, 'runs')) if f.endswith('_cmd.csv'))
    all_t, cmd, pos, v = [], [], [], []
    for f in runs:
        for r in rows(os.path.join(d, 'runs', f)):
            if r['speed_kph'] and r['steer_pos_deg']:
                all_t.append(float(r['wall_s']))
                cmd.append(float(r['steer_cmd_deg']))
                pos.append(float(r['steer_pos_deg']))
                v.append(float(r['speed_kph']))
    t = np.array(all_t)
    order = np.argsort(t)
    t, cmd, pos, v = t[order], np.array(cmd)[order], np.array(pos)[order], np.array(v)[order]
    grid = np.arange(t[0], t[-1], 0.2)
    write('sil_circle_time', ['t', 'cmd_handle_deg', 'pos_handle_deg', 'wheel_kph'],
          [[f'{x - t[0]:.1f}', f'{a:.1f}', f'{b:.1f}', f'{c:.2f}'] for x, a, b, c in zip(grid, hold(t, cmd, grid), hold(t, pos, grid), hold(t, v, grid))])
    s = (gt >= t[0]) & (gt <= t[-1])
    idx = np.where(s)[0][::10]
    write('sil_circle_xy', ['t', 'x_m', 'y_m'], [[f'{gt[i] - t[0]:.2f}', f'{gx[i]:.2f}', f'{gy[i]:.2f}'] for i in idx])
    shutil.copy(os.path.join(d, 'fit', 'circle_fit.csv'), os.path.join(OUT_SIL, 'circle_fit.csv'))
    shutil.copy(os.path.join(d, 'fit', 'report.md'), os.path.join(OUT_SIL, 'circle_report.md'))


def preview_sheets():
    """11-04 실차 로그 미리보기(GNSS 없음, 후륜 ÷ 0.958 — 참고값)."""
    out = os.path.join(REP, 'data', 'bsw5_preview')
    log = os.path.join(ROOT, 'measurements/2026-11-04_drive_log/raw_can/auto_bsw5/candump_kv0.log.zst')
    r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools/engine_room/fit_long_map.py'), '--can', log, '--can-only-preview',
                        '--t0', '15', '--t1', '430', '--out', out], capture_output=True, text=True)
    print(r.stdout.split('\n')[0][:60])
    for f in ('summary.json', ):
        with open(os.path.join(out, f), encoding='utf-8') as fh:
            print(json.load(fh)['accuracy'])
    if os.path.exists(os.path.join(out, 'long_map.png')):
        os.remove(os.path.join(out, 'long_map.png'))
    scatter('bsw5_scatter', rows(os.path.join(out, 'segments.csv')))


def latency_sheet():
    a, b = rows(os.path.join(LAT0, 'latency_idle.csv')), rows(os.path.join(LAT1, 'latency_load.csv'))
    write('vcan_latency', ['t', 'latency_idle_ms', 'latency_load_ms'],
          [[k, a[k]['latency_ms'], b[k]['latency_ms']] for k in range(min(len(a), len(b)))])
    shutil.copy(os.path.join(LAT0, 'summary.csv'), os.path.join(OUT_SIL, 'race_summary_vcan.csv'))
    shutil.copy(os.path.join(LAT1, 'summary.csv'), os.path.join(OUT_SIL, 'race_summary_vcan_load.csv'))


def main():
    map_sheets()
    fit_sheets()
    circle_sheets()
    preview_sheets()
    latency_sheet()
    shutil.copy(os.path.join(SIL, 'summary.json'), os.path.join(OUT_SIL, 'engine_room_summary.json'))
    for sub, name in (('v1', 'fit_v1_blocked.md'), ('v2', 'fit_v2_report.md')):
        src = os.path.join(SIL, 'fit', sub, 'UPDATE_BLOCKED.md' if sub == 'v1' else 'report.md')
        shutil.copy(src, os.path.join(OUT_SIL, name))


if __name__ == '__main__':
    main()
