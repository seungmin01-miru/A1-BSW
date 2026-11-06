#!/usr/bin/env python3
"""원 돌기 분석(지시서 2.5절) — GNSS 궤적을 원에 맞춰 반경 → 타이어각 = atan(L/R), 영점과 각도별 실효 조향비(좌/우)를 낸다.

  python3 tools/engine_room/fit_circle.py --run-dir <circle_test 결과 폴더> --gnss-csv <csv> --out <폴더>
  python3 tools/engine_room/fit_circle.py --run-dir <폴더> --bag <bag> --out <폴더>

입력: profile_gen 회차 CSV(<run>_cmd.csv), GNSS(t, x_m, y_m) 또는 bag(`/gsof_client/odom`). 저장된 파일만 읽는다.
**자동 반영 금지** — steer_map 추천은 steer_map_recommended.yaml 로만 낸다. 부호: 좌회전 + (CLAUDE.md 2.1). 저속 데이터만(≤ 8 km/h).
종료 코드: 0 통과, 2 입력 오류, 3 쓸 수 있는 회차 없음.
"""
import argparse
import csv
import glob
import math
import os
import sys

import numpy as np
import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
WHEELBASE_M = 2.360       # CLAUDE.md 2절 확정값
MIN_ARC_DEG = 270.0       # 이만큼 돌지 못한 회차는 반경을 믿지 않는다(잠정)
FIT_RMS_MAX_M = 0.5       # 잠정
SPEED_MAX_KPH = 9.0       # 지시서 ≤ 8 km/h + 여유
ZERO_FIT_HANDLE_DEG = 90.0


def fit_circle_xy(x, y):
    """대수적 원 맞춤(Kasa). (cx, cy, R, rms)."""
    x0, y0 = x.mean(), y.mean()
    u, v = x - x0, y - y0
    a = np.c_[2 * u, 2 * v, np.ones(len(u))]
    sol = np.linalg.lstsq(a, u * u + v * v, rcond=None)[0]
    r = math.sqrt(sol[2] + sol[0] ** 2 + sol[1] ** 2)
    rms = float(np.sqrt(np.mean((np.hypot(u - sol[0], v - sol[1]) - r) ** 2)))
    return float(sol[0] + x0), float(sol[1] + y0), float(r), rms


def arc_signed_deg(x, y, cx, cy):
    """중심 기준 누적 각도. + = 반시계(좌회전)."""
    th = np.unwrap(np.arctan2(y - cy, x - cx))
    return float(np.degrees(th[-1] - th[0]))


def load_gnss_csv(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows or not {'t', 'x_m', 'y_m'} <= set(rows[0]):
        raise ValueError(f'{path}: t, x_m, y_m 열이 필요')
    return (np.array([float(r['t']) for r in rows]), np.array([float(r['x_m']) for r in rows]),
            np.array([float(r['y_m']) for r in rows]))


def load_gnss_bag(uri, topic):
    sys.path.insert(0, os.path.join(REPO, 'tools', 'track_day'))
    import fit_maps
    m = fit_maps.read_bag(uri, [topic])[topic]
    if not m:
        raise ValueError(f'bag 에 {topic} 없음')
    return (np.array([x[0] for x in m]), np.array([x[1].pose.pose.position.x for x in m]),
            np.array([x[1].pose.pose.position.y for x in m]))


def analyze_run(path, gt, gx, gy, label='원선회'):
    """회차 CSV 하나 → (결과 dict, '') 또는 (None, 이유)."""
    name = os.path.basename(path).replace('_cmd.csv', '')
    with open(path) as f:
        rows = [r for r in csv.DictReader(f) if r['phase'] == 'run' and r['label'] == label]
    if not rows:
        return None, f'{name}: {label} 구간 없음'
    t = np.array([float(r['wall_s']) for r in rows])
    v = np.array([float(r['speed_kph']) if r['speed_kph'] else math.nan for r in rows])
    cmd = np.array([float(r['steer_cmd_deg']) for r in rows])
    pos = np.array([float(r['steer_pos_deg']) if r['steer_pos_deg'] else math.nan for r in rows])
    keep = (np.abs(pos - cmd) < 5.0) & (v >= 0.8 * np.nanmedian(v)) & (v > 1.0)
    if keep.sum() < 50:
        return None, f'{name}: 안정 구간 표본 {int(keep.sum())}'
    tk = t[keep]
    idx = (gt >= tk[0]) & (gt <= tk[-1])
    if idx.sum() < 30:
        return None, f'{name}: 이 시간대 GNSS 없음'
    x, y = gx[idx], gy[idx]
    cx, cy, r, rms = fit_circle_xy(x, y)
    arc = arc_signed_deg(x, y, cx, cy)
    if abs(arc) < MIN_ARC_DEG:
        return None, f'{name}: 호 {abs(arc):.0f}° < {MIN_ARC_DEG:.0f}°'
    if rms > FIT_RMS_MAX_M:
        return None, f'{name}: 원 맞춤 RMS {rms:.2f} m > {FIT_RMS_MAX_M} m'
    vmax = float(np.nanmax(v))
    if vmax > SPEED_MAX_KPH:
        return None, f'{name}: 최고 {vmax:.1f} km/h > {SPEED_MAX_KPH} (저속 데이터만)'
    tire = (1.0 if arc > 0 else -1.0) * math.degrees(math.atan(WHEELBASE_M / r))
    handle = float(np.nanmean(pos[keep]))
    return dict(run=name, handle_cmd_deg=float(np.median(cmd[keep])), handle_pos_deg=handle,
                speed_kph=float(np.nanmean(v[keep])), radius_m=r, fit_rms_m=rms, arc_deg=arc, tire_deg=tire,
                ratio=abs(handle / tire) if tire else math.nan), ''


def zero_and_ratio(res):
    """작은 각 직선 맞춤: 핸들 = k·타이어 + h0. h0 = 직진 핸들각. 좌우가 모두 있어야 한다."""
    small = [r for r in res if abs(r['handle_pos_deg']) <= ZERO_FIT_HANDLE_DEG]
    if len({r['handle_cmd_deg'] > 0 for r in small}) < 2:
        return None
    k, h0 = np.polyfit([r['tire_deg'] for r in small], [r['handle_pos_deg'] for r in small], 1)
    side = {}
    for s, name in ((1, 'left'), (-1, 'right')):
        rows = [r for r in res if r['tire_deg'] * s > 0]
        if rows:
            side[name] = float(np.mean([abs((r['handle_pos_deg'] - h0) / r['tire_deg']) for r in rows]))
    return dict(k=float(k), h0_deg=float(h0), tire_at_zero_deg=float(-h0 / k), ratio_left=side.get('left'),
                ratio_right=side.get('right'))


def recommend_map(res, zero):
    """측정 점 + 영점으로 표. 같은 명령은 평균. 단조가 아니면 None."""
    by = {}
    for r in res:
        by.setdefault(round(r['handle_cmd_deg']), []).append(r)
    pts = [(float(np.mean([x['tire_deg'] for x in v])), float(np.mean([x['handle_pos_deg'] for x in v]))) for v in by.values()]
    if zero:
        pts.append((0.0, zero['h0_deg']))
    pts.sort()
    if len(pts) < 2 or any(b[1] <= a[1] or b[0] <= a[0] for a, b in zip(pts, pts[1:])):
        return None
    return dict(tire_deg=[round(p[0], 2) for p in pts], wheel_deg=[round(p[1], 1) + 0.0 for p in pts])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run-dir', required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--gnss-csv')
    g.add_argument('--bag')
    ap.add_argument('--gnss-topic', default='/gsof_client/odom')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.run_dir, '*_cmd.csv')))
    if not files:
        print(f'입력 오류: {a.run_dir} 에 *_cmd.csv 없음', file=sys.stderr)
        return 2
    try:
        gt, gx, gy = load_gnss_csv(a.gnss_csv) if a.gnss_csv else load_gnss_bag(a.bag, a.gnss_topic)
    except (ValueError, OSError, ImportError, RuntimeError) as e:
        print(f'입력 오류: {e}', file=sys.stderr)
        return 2
    res, skipped = [], []
    for f in files:
        r, why = analyze_run(f, gt, gx, gy)
        if r:
            res.append(r)
        else:
            skipped.append(why)
    os.makedirs(a.out, exist_ok=True)
    keys = ['run', 'handle_cmd_deg', 'handle_pos_deg', 'speed_kph', 'radius_m', 'fit_rms_m', 'arc_deg', 'tire_deg', 'ratio']
    with open(os.path.join(a.out, 'circle_fit.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        for r in res:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})
    zero = zero_and_ratio(res) if res else None
    lines = ['# 원 돌기 분석', '', f'회차 {len(files)} 개 중 {len(res)} 개 사용. L = {WHEELBASE_M} m, 타이어 = atan(L/R), 좌 +.', '',
             '| 회차 | 명령 핸들 ° | 실제 핸들 ° | km/h | R m | 맞춤 RMS m | 호 ° | 타이어 ° | 비 |', '|---|---|---|---|---|---|---|---|---|']
    for r in res:
        lines.append(f"| {r['run']} | {r['handle_cmd_deg']:.1f} | {r['handle_pos_deg']:.1f} | {r['speed_kph']:.1f} | {r['radius_m']:.2f} "
                     f"| {r['fit_rms_m']:.3f} | {r['arc_deg']:.0f} | {r['tire_deg']:.2f} | {r['ratio']:.1f} |")
    if zero:
        lines += ['', f"**영점:** 직진 핸들 {zero['h0_deg']:+.1f}°, 핸들 0° 에서 타이어 {zero['tire_at_zero_deg']:+.2f}°. "
                      f"**실효 조향비:** 좌 {zero['ratio_left'] or math.nan:.1f}, 우 {zero['ratio_right'] or math.nan:.1f}."]
    else:
        lines += ['', '영점 계산 불가: 좌·우 작은 각 회차가 모두 필요하다.']
    rec = recommend_map(res, zero) if res else None
    if rec:
        with open(os.path.join(a.out, 'steer_map_recommended.yaml'), 'w', encoding='utf-8') as f:
            f.write('# 추천 — 자동 반영 금지. 사람이 보고 track.yaml steer_map 에 넣고 커밋.\n')
            yaml.safe_dump({'steer_map': rec}, f, default_flow_style=None)
        lines += ['', f"**추천 표:** 타이어 {rec['tire_deg']} ↔ 핸들 {rec['wheel_deg']}"]
    else:
        lines += ['', '추천 표 없음(점 부족 또는 단조 아님).']
    if skipped:
        lines += ['', '## 뺀 회차', ''] + [f'- {x}' for x in skipped]
    with open(os.path.join(a.out, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return 0 if res else 3


if __name__ == '__main__':
    sys.exit(main())
