#!/usr/bin/env python3
"""가짜 실차(USER_ 프로토콜) — vcan0 리허설용. 0x200·0x201 을 21 ms 주기(10-07 실측 21.0 ms)로 보내고,
0x210 을 받으면 auto 비트가 켜진 축만 반응한다.

  - 조향 위치: 명령을 1차 지연(시정수 0.2 s)으로 추종 — 배율은 0.1 deg/raw(업체 DBC, 10-07 실차 확정)
  - 브레이크 위치(%): 명령 % 로 추종(10-07 실측: 위치 = 명령 %)
  - 바퀴 속도: 가속 % − 브레이크 % 로 단순 적분(물리 모델 아님, 숫자가 움직이는지만 보는 용도)
  - auto 비트(0x200 byte4): 마지막으로 받은 0x210 의 auto 비트를 그대로 보고, **0x210 이 끊겨도 유지**한다
    (10-07 실차: 0x210 없이 59.9 s 동안 auto 111 유지, auto 0 프레임이 와야 해제). 다른 동작을 시험하려면
    --cmd-timeout-s 로 타임아웃을 줄 수 있다(기본 0 = 없음).

  python3 sil/vcan/fake_a1_vehicle.py --channel vcan0
  python3 sil/vcan/fake_a1_vehicle.py --channel vcan0 --physics --pose-log /tmp/pose.csv   # 02 세션 SIL(속도 제어·U턴)

--physics (2026-11-04, bsw_test 02 세션 SIL): 가속 % → 가속도(불감대 5 %, track.yaml accel_map 비슷하게),
브레이크 % → 감속도(track.yaml brake_map), 주행 저항 0.15 + 0.01·v m/s², 구동·제동 1차 지연 0.15 s, 브레이크 위치는
82 % 에서 포화. 자세(x, y, 방위)는 자전거 모델(휠베이스 2.36 m, 조향비 --steer-ratio)로 적분해 --pose-log 에 남긴다.
**물리 모델이 실차와 같다는 뜻이 아니다** — 제어 로직이 닫히고 멈추는지만 본다(실차 판정에 쓰지 않음).
"""
import csv
import math
import argparse
import os
import signal
import sys
import time

import can

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'race_day'))
from a1_proto import (FRAME_CONTROL_COMMAND, FRAME_CONTROL_INFO, FRAME_WHEEL_INFO,  # noqa: E402
                      decode_0x210, encode_0x200, encode_0x201)

_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--interface', default='socketcan', choices=('socketcan', 'kvaser'),
                    help='kvaser = CANlib 가상 채널로 리허설(Kvaser 설치 후, 채널 번호를 --channel 로)')
    ap.add_argument('--bitrate', type=int, default=500000)
    ap.add_argument('--period-ms', type=float, default=21.0, help='0x200/0x201 주기 (10-07 실측 21.0 ms)')
    ap.add_argument('--cmd-timeout-s', type=float, default=0.0,
                    help='0x210 이 이 시간 끊기면 auto 0 (기본 0 = 타임아웃 없음, 10-07 실차 동작)')
    ap.add_argument('--physics', action='store_true', help='변환표 기반 가감속·자세 모델(02 세션 SIL)')
    ap.add_argument('--steer-ratio', type=float, default=7.5)
    ap.add_argument('--plant', default='sil', choices=('sil', 'real'),
                    help='--physics 응답. real = 11-04 실차 흉내(가속 불감대 70 %, 속도가 오를수록 가속 감소, 후륜은 실제의 0.958 배) — 숫자는 잠정, 판정에 쓰지 않음')
    ap.add_argument('--gnss-csv', default='', help='--physics: 가짜 GNSS CSV (t=벽시계, x_m, y_m, speed_mps, fix, pitch_deg) — fit_long_map·fit_circle SIL')
    ap.add_argument('--pose-log', default='', help='--physics: 자세 CSV (t, x, y, yaw_deg, kph, steer_deg)')
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    bus_kw = {} if a.interface == 'socketcan' else {'bitrate': a.bitrate}
    if a.interface == 'kvaser':
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'safety', 'can_guard'))
        import kvaser_compat
        kvaser_compat.apply()
    bus = can.Bus(channel=a.channel, interface=a.interface, **bus_kw)
    dt = a.period_ms / 1000.0
    steer = brake = kph = 0.0
    a_drive = a_brake = 0.0
    x = y = yaw = 0.0
    pose_w = None
    if a.pose_log:
        pose_f = open(a.pose_log, 'w', newline='', buffering=1)
        pose_w = csv.writer(pose_f)
        pose_w.writerow(['mono_s', 'x_m', 'y_m', 'yaw_deg', 'kph', 'steer_deg', 'brake_pos_pct'])

    gnss_w = None
    if a.gnss_csv:
        gnss_f = open(a.gnss_csv, 'w', newline='', buffering=1)
        gnss_w = csv.writer(gnss_f)
        gnss_w.writerow(['t', 'x_m', 'y_m', 'speed_mps', 'fix', 'pitch_deg'])
    wheel_scale = 0.958 if a.plant == 'real' else 1.0

    def interp(v, xs, ys):
        if v <= xs[0]:
            return ys[0]
        for j in range(1, len(xs)):
            if v <= xs[j]:
                return ys[j - 1] + (ys[j] - ys[j - 1]) * (v - xs[j - 1]) / (xs[j] - xs[j - 1])
        return ys[-1]
    cmd = None
    cmd_t = None
    cnt = 0
    next_t = time.monotonic()
    print(f'[fake_a1_vehicle] {a.channel} 송신 시작(0x200/0x201 {a.period_ms:g} ms)', file=sys.stderr, flush=True)
    while not _stop:
        while True:
            m = bus.recv(timeout=0)
            if m is None:
                break
            if m.arbitration_id == FRAME_CONTROL_COMMAND and len(m.data) == 8:
                cmd, cmd_t = decode_0x210(m.data), time.monotonic()
        now = time.monotonic()
        live = cmd is not None and (a.cmd_timeout_s <= 0 or now - cmd_t < a.cmd_timeout_s)
        sa = ba = aa = 0
        if live:
            sa, ba, aa = cmd['steer_auto'], cmd['brake_auto'], cmd['acc_auto']
            if sa:
                steer += (cmd['steer_deg'] - steer) * min(1.0, dt / 0.2)
            if ba:
                brake += (cmd['brake_pct'] - brake) * min(1.0, dt / 0.2)
            acc = cmd['acc_pct'] if aa else 0
            bpct = cmd['brake_pct'] if ba else 0
            if not a.physics:
                kph = max(0.0, kph + (acc * 0.5 - bpct * 1.0 - 0.5) * dt)
        else:
            acc = bpct = 0
            if not a.physics:
                kph = max(0.0, kph - 0.5 * dt)
        if a.physics:
            if ba and live:
                brake = min(brake, 82.0)
            k = min(1.0, dt / 0.15)
            v = kph / 3.6
            if a.plant == 'real':
                drive = interp(acc, [0, 70, 75, 85, 100], [0, 0, 0.75, 1.1, 1.6]) * max(0.0, 1.0 - kph / 150.0)
                a_brake += (interp(bpct, [0, 20, 50, 80, 100], [0, 0.4, 1.4, 2.6, 3.0]) - a_brake) * k
            else:
                drive = interp(acc, [0, 5, 30, 100], [0, 0, 1.3, 3.0])
                a_brake += (interp(min(bpct, 82), [0, 40, 70, 82], [0, 1.0, 2.0, 2.8]) - a_brake) * k
            a_drive += (drive - a_drive) * k
            res = (0.15 + 0.01 * v) if v > 0 else 0.0
            v = max(0.0, v + (a_drive - a_brake - res) * dt)
            if v == 0.0 and a_drive <= a_brake + 0.15:
                v = 0.0
            kph = v * 3.6
            tire = math.radians(steer / a.steer_ratio)
            yaw += v * math.tan(tire) / 2.36 * dt
            x += v * math.cos(yaw) * dt
            y += v * math.sin(yaw) * dt
            if pose_w:
                pose_w.writerow([f'{now:.4f}', f'{x:.3f}', f'{y:.3f}', f'{math.degrees(yaw):.2f}', f'{kph:.2f}',
                                 f'{steer:.1f}', f'{brake:.1f}'])
            if gnss_w:
                gnss_w.writerow([f'{time.time():.4f}', f'{x:.3f}', f'{y:.3f}', f'{v:.4f}', 4, 0.2])
        rpm = int(kph * 9.5)   # 속도·rpm 비율은 미확인(10-07 은 바퀴 0) — 숫자 확인용 임의값
        bus.send(can.Message(arbitration_id=FRAME_CONTROL_INFO, is_extended_id=False,
                             data=encode_0x200(steer, brake, sa, ba, aa, cnt, cnt, cnt)))
        bus.send(can.Message(arbitration_id=FRAME_WHEEL_INFO, is_extended_id=False,
                             data=encode_0x201(kph * wheel_scale, rpm, kph * wheel_scale, rpm, cnt, cnt)))
        cnt = (cnt + 1) & 0xFF
        next_t += dt
        d = next_t - time.monotonic()
        if d > 0:
            time.sleep(d)
        else:
            next_t = time.monotonic()
    bus.shutdown()
    print('[fake_a1_vehicle] 종료', file=sys.stderr)


if __name__ == '__main__':
    main()
