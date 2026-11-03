#!/usr/bin/env python3
"""트랙 녹화에서 어댑터 표(track.yaml)를 맞춘다 — TRACK_MANUAL T2(조향)·T3(브레이크)·T4(가속).

  # T3/T4: 세션 bag 에서 브레이크 %·가속 % 가 일정하게 유지된 구간마다 차량 가감속을 구해 표로
  python3 tools/track_day/fit_maps.py long ~/a1_race_capture/track_latest/bag
  # T2: 정지 상태 실측표(CSV: wheel_deg,left_tire_deg,right_tire_deg — 각도계로 잰 값)
  python3 tools/track_day/fit_maps.py steer t2_steer.csv
  # T2 보조: 주행 bag 에서 요레이트로 역산(δ = atan(r·L/v), 정상 선회 구간만) — 정적 표 검증용
  python3 tools/track_day/fit_maps.py steer-dyn ~/a1_race_capture/track_latest/bag --wheelbase 1.6

출력 끝의 YAML 조각을 세션 폴더의 track_params.yaml 에 붙여 넣고 어댑터를 재시작한다(bringup --params).
명령은 버스 위 0x210(/control/status/command_on_bus — 어댑터든 lift_cmd 든), 속도는 GNSS(/gsof_client/odom
twist 크기). 0x201 바퀴속도는 10-07 내내 0 이었으므로 쓰지 않는다.
"""
import argparse
import csv
import math
import statistics
import sys

import numpy as np


def read_bag(uri, topics):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message
    r = rosbag2_py.SequentialReader()
    storage = 'mcap' if not uri.endswith('.db3') else 'sqlite3'
    r.open(rosbag2_py.StorageOptions(uri=uri, storage_id=storage), rosbag2_py.ConverterOptions('', ''))
    types = {t.name: t.type for t in r.get_all_topics_and_types()}
    r.set_filter(rosbag2_py.StorageFilter(topics=[t for t in topics if t in types]))
    out = {t: [] for t in topics}
    while r.has_next():
        name, data, t_ns = r.read_next()
        out[name].append((t_ns * 1e-9, deserialize_message(data, get_message(types[name]))))
    return out


def speed_series(odom, smooth_s=0.3):
    t = np.array([x[0] for x in odom])
    v = np.array([math.hypot(m.twist.twist.linear.x, m.twist.twist.linear.y) for _, m in odom])
    w = np.array([m.twist.twist.angular.z for _, m in odom])
    if len(t) < 10:
        sys.exit('GNSS odom(/gsof_client/odom) 가 bag 에 없거나 너무 짧다')
    k = max(1, int(round(smooth_s / np.median(np.diff(t)))))
    ker = np.ones(k) / k
    return t, np.convolve(v, ker, 'same'), np.convolve(w, ker, 'same')


def segments(cmds, key, min_s):
    """key(brake/acc) 가 0 보다 크고 같은 값으로 min_s 이상 유지된 구간 [(값, 시작, 끝)]."""
    segs, cur = [], None
    for t, m in cmds:
        val = int(getattr(m, key))
        auto = m.brake_auto if key == 'brake_cmd_pct' else m.acc_auto
        other = m.acc_cmd_pct if key == 'brake_cmd_pct' else m.brake_cmd_pct
        val = val if (auto and other == 0) else 0
        if cur and cur[0] == val:
            cur[2] = t
            continue
        if cur and cur[0] > 0 and cur[2] - cur[1] >= min_s:
            segs.append(tuple(cur))
        cur = [val, t, t]
    if cur and cur[0] > 0 and cur[2] - cur[1] >= min_s:
        segs.append(tuple(cur))
    return segs


def cmd_long(a):
    d = read_bag(a.bag, ['/control/status/command_on_bus', '/gsof_client/odom'])
    t, v, _ = speed_series(d['/gsof_client/odom'])
    rows = []
    for key, sign in (('brake_cmd_pct', -1), ('acc_cmd_pct', 1)):
        for val, t0, t1 in segments(d['/control/status/command_on_bus'], key, a.min_s):
            s = (t >= t0 + a.skip_s) & (t <= t1)
            if s.sum() < 10 or v[s].mean() < a.min_speed:
                continue
            slope = np.polyfit(t[s], v[s], 1)[0]
            rows.append((key, val, slope, v[s].mean() * 3.6, t1 - t0))
    if not rows:
        sys.exit('일정 명령 구간이 없다 — T3/T4 를 계단마다 2초 이상 유지했는지, GNSS odom 이 녹화됐는지 확인')
    print(f'{"축":6s} {"명령%":>5s} {"가감속 m/s²":>11s} {"평균속도 km/h":>13s} {"길이 s":>6s}')
    for key, val, slope, kmh, dur in rows:
        print(f'{("브레이크" if key == "brake_cmd_pct" else "가속"):6s} {val:5d} {slope:11.2f} {kmh:13.1f} {dur:6.1f}')
    for key, xname, yname, sign in (('brake_cmd_pct', 'decel_mps2', 'brake_pct', -1),
                                    ('acc_cmd_pct', 'accel_mps2', 'acc_pct', 1)):
        by = {}
        for k, val, slope, _, _ in rows:
            if k == key:
                by.setdefault(val, []).append(sign * slope)
        pts = sorted((statistics.median(xs), p) for p, xs in by.items())
        # 표는 x(가감속 크기) 오름차순·y(%) 단조여야 한다 — 뒤집힌 점(측정 잡음)은 버리고 알린다
        mono, drop = [], []
        for x, p in pts:
            if x > 0 and (not mono or (x > mono[-1][0] and p > mono[-1][1])):
                mono.append((x, p))
            else:
                drop.append((p, round(x, 2)))
        if drop:
            print(f'  ⚠️ {key}: 단조가 아니라 뺀 점 (%, m/s²) {drop}')
        if mono:
            name = 'brake_map' if key == 'brake_cmd_pct' else 'accel_map'
            xs = [round(a.deadband, 2)] + [round(x, 2) for x, _ in mono]
            ys = [0.0] + [float(p) for _, p in mono]
            print(f'\n{name}:\n  {xname}: {xs}\n  {yname}:  {ys}')


def cmd_steer(a):
    wheel, tire = [], []
    with open(a.csv) as f:
        for row in csv.DictReader(f):
            wheel.append(float(row['wheel_deg']))
            lt, rt = float(row['left_tire_deg']), float(row['right_tire_deg'])
            tire.append((lt + rt) / 2)    # 자전거 모델 등가각 ≈ 좌우 평균(작은 각에서 애커만 근사)
    pts = sorted(zip(tire, wheel))
    if any(b[0] <= a_[0] for a_, b in zip(pts, pts[1:])):
        sys.exit(f'타이어각이 단조 증가하지 않는다 — 측정·부호 확인 {pts}')
    for x, y in pts:
        print(f'  타이어 {x:+6.2f}° ↔ 핸들 {y:+7.1f}°' + (f'   (조향비 {y / x:.2f})' if abs(x) > 0.5 else ''))
    print(f'\nsteer_map:\n  tire_deg:  {[round(x, 2) for x, _ in pts]}\n  wheel_deg: {[round(y, 1) for _, y in pts]}')
    print('※ 0 점이 없으면 (0, 0) 을 넣을지 판단(불감대 ±1.5° 관측). 좌회전 핸들이 + 인지 T1 결과와 맞춰 steer_sign 확인.')


def cmd_steer_dyn(a):
    d = read_bag(a.bag, ['/control/status/control_info', '/gsof_client/odom'])
    t, v, w = speed_series(d['/gsof_client/odom'], smooth_s=0.5)
    info = d['/control/status/control_info']
    ti = np.array([x[0] for x in info])
    sw = np.array([m.steer_position_deg for _, m in info])
    swi = np.interp(t, ti, sw)
    dsw = np.abs(np.gradient(swi, t))
    ok = (v > a.min_speed) & (dsw < 5.0)          # 정상 선회: 핸들이 거의 멈춰 있을 때
    delta = np.degrees(np.arctan(w[ok] * a.wheelbase / v[ok]))
    if ok.sum() < 100:
        sys.exit('정상 선회 표본 부족')
    bins = np.arange(-150, 151, 15)
    print(f'{"핸들각 구간":>12s} {"n":>6s} {"역산 타이어각":>12s} {"조향비":>7s}')
    for lo, hi in zip(bins, bins[1:]):
        s = (swi[ok] >= lo) & (swi[ok] < hi)
        if s.sum() >= 50:
            c, dd = swi[ok][s].mean(), np.median(delta[s])
            print(f'{lo:+5.0f}~{hi:+5.0f}° {s.sum():6d} {dd:+12.2f} {c / dd if abs(dd) > 0.5 else float("nan"):7.2f}')
    print('※ 요레이트 부호(odom angular.z)와 핸들 부호가 반대면 조향비가 음수로 나온다 — 크기만 비교하고 부호는 T1 로.')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('long')
    p.add_argument('bag')
    p.add_argument('--min-s', type=float, default=1.5, help='같은 명령이 이만큼 유지된 구간만')
    p.add_argument('--skip-s', type=float, default=0.4, help='구간 앞부분 버림(액추에이터 무반응·과도)')
    p.add_argument('--min-speed', type=float, default=1.0, help='m/s, 이보다 느린 구간은 버림(정지 중 브레이크)')
    p.add_argument('--deadband', type=float, default=0.1)
    p.set_defaults(fn=cmd_long)
    p = sub.add_parser('steer')
    p.add_argument('csv')
    p.set_defaults(fn=cmd_steer)
    p = sub.add_parser('steer-dyn')
    p.add_argument('bag')
    p.add_argument('--wheelbase', type=float, required=True, help='축거 L [m] — 업체 제원')
    p.add_argument('--min-speed', type=float, default=3.0)
    p.set_defaults(fn=cmd_steer_dyn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
