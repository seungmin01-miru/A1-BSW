#!/usr/bin/env python3
"""collect.sh 의 보조 원시 CAN 로거 — candump -l 과 이중화용(§tools/race_day/README.md).
DBC 없이 무조건 arbitration_id+raw_hex 만 남긴다(디코드는 나중에 이 컴퓨터로 가져와서).
python-can 에만 의존, cantools 등 무거운 의존 없음.

  python3 can_csv_logger.py --channel can0 --out can0.csv
"""
import argparse
import signal
import sys
import time

try:
    import can
except ImportError:
    # 남의 PC(다른 팀 노트북 등)에 python-can 이 없을 때를 대비 — 트레이스백 대신 한 줄로 이유를
    # 남기고 조용히 종료(collect.sh 의 candump 캡처는 이 실패와 무관하게 계속 진행됨).
    print("[can_csv_logger] python-can 미설치 — 실행 실패, 이 보조 로거는 건너뜀"
          " (원본은 candump 로그만 남음, 'pip install python-can' 로 설치 가능하면 설치)", file=sys.stderr)
    sys.exit(1)

_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', required=True)
    ap.add_argument('--interface', default='socketcan')
    ap.add_argument('--bitrate', type=int, default=500000, help='socketcan 이 아닐 때만 사용')
    ap.add_argument('--listen-only', action='store_true', help='kvaser: 하드웨어 silent 모드(ACK·에러프레임 안 보냄)')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    try:
        kw = {}
        if a.interface != 'socketcan':
            kw['bitrate'] = a.bitrate
        if a.interface == 'kvaser' and a.listen_only:
            kw['driver_mode'] = False   # python-can DRIVER_MODE_SILENT
        if a.interface == 'kvaser':
            import os
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'safety', 'can_guard'))
            import kvaser_compat
            kvaser_compat.apply()
        bus = can.Bus(channel=a.channel, interface=a.interface, **kw)
    except Exception as e:   # noqa: BLE001 — 남의 PC 에서 인터페이스가 없거나 권한 문제일 수 있음, 한 줄로만 남김
        print(f'[can_csv_logger] {a.channel} 열기 실패 — {e}', file=sys.stderr)
        sys.exit(1)
    n = 0
    with open(a.out, 'w', buffering=1) as f:   # 줄 단위 버퍼링 — 중간에 죽어도 그때까지는 남음
        f.write('wall_time,arbitration_id_hex,raw_hex\n')
        print(f'[can_csv_logger] {a.channel} -> {a.out}', file=sys.stderr)
        while not _stop:
            try:
                msg = bus.recv(timeout=0.5)
            except Exception as e:   # noqa: BLE001 — 인터페이스가 중간에 내려가는 경우(예: 배선 문제) 대비
                print(f'[can_csv_logger] {a.channel} 수신 중 오류 — {e} (종료)', file=sys.stderr)
                break
            if msg is None:
                continue
            f.write(f'{time.time():.6f},0x{msg.arbitration_id:x},{msg.data.hex()}\n')
            n += 1
    bus.shutdown()
    print(f'[can_csv_logger] {a.channel} 종료 — {n}프레임', file=sys.stderr)


if __name__ == '__main__':
    main()
