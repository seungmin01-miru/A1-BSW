#!/usr/bin/env python3
"""가짜 실차(USER_ 프로토콜) — vcan0 리허설용. 0x200·0x201 을 20 ms 주기(9/17 실측 20.8 ms)로 보내고,
0x210 을 받으면 auto 비트가 켜진 축만 반응한다.

  - 조향 위치: 명령을 1차 지연(시정수 0.2 s)으로 추종 — 배율은 0.1 deg/raw(업체 DBC, 10-07 실차 확정)
  - 브레이크 위치: 명령 % / 10 로 추종(9/17 로그에서 명령 0–100 % 일 때 위치 0–10 범위였던 것만 흉내)
  - 바퀴 속도: 가속 % − 브레이크 % 로 단순 적분(물리 모델 아님, 숫자가 움직이는지만 보는 용도)
  - auto 비트(0x200 byte4): 마지막으로 받은 0x210 의 auto 비트를 그대로 보고, 0x210 이 0.5 s 끊기면 0
    ⚠️ 실차의 명령 타임아웃 동작은 모르므로 흉내 내지 않는다 — 이 0.5 s 는 가짜 차량의 임의 값

  python3 sil/vcan/fake_a1_vehicle.py --channel vcan0
"""
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
    ap.add_argument('--period-ms', type=float, default=20.0)
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
        live = cmd is not None and now - cmd_t < 0.5
        sa = ba = aa = 0
        if live:
            sa, ba, aa = cmd['steer_auto'], cmd['brake_auto'], cmd['acc_auto']
            if sa:
                steer += (cmd['steer_deg'] - steer) * min(1.0, dt / 0.2)
            if ba:
                brake += (cmd['brake_pct'] / 10.0 - brake) * min(1.0, dt / 0.2)
            acc = cmd['acc_pct'] if aa else 0
            bpct = cmd['brake_pct'] if ba else 0
            kph = max(0.0, kph + (acc * 0.5 - bpct * 1.0 - 0.5) * dt)
        else:
            kph = max(0.0, kph - 0.5 * dt)
        rpm = int(kph * 9.5)   # 9/17 로그의 속도·rpm 비율 근처(숫자 확인용)
        bus.send(can.Message(arbitration_id=FRAME_CONTROL_INFO, is_extended_id=False,
                             data=encode_0x200(steer, brake, sa, ba, aa, cnt, cnt, cnt)))
        bus.send(can.Message(arbitration_id=FRAME_WHEEL_INFO, is_extended_id=False,
                             data=encode_0x201(kph, rpm, kph, rpm, cnt, cnt)))
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
