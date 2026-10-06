#!/usr/bin/env python3
"""§7.3 P-1/P-2 시험용 — 진짜 ROS2 제어 노드가 아직 없어서 그 자리를 흉내낸다.
CommandChannel 에 주기적으로 값이 계속 바뀌는 명령을 쓴다(사인파) — can_guard 가 ACTIVE 로 들어가는지,
그리고 이 프로세스를 kill 했을 때 HOLDING 으로 넘어가는지(P-1) 보는 데 쓴다.

  python3 fake_control_node.py --cmd-shm p1_cmd --period 0.01
"""
import argparse
import math
import sys
import time

sys.path.insert(0, __file__.rsplit('/', 2)[0])   # safety/can_guard/ 를 import 경로에 추가
from protocol import Command, CommandChannel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cmd-shm', required=True)
    ap.add_argument('--period', type=float, default=0.01)
    ap.add_argument('--duration', type=float, default=0.0, help='0=무한')
    a = ap.parse_args()

    ch = CommandChannel.open(a.cmd_shm)   # can_guard 가 먼저 떠서 만들어 둔 세그먼트를 연다
    print(f'[fake_control_node] {a.cmd_shm} 에 쓰기 시작 (pid={__import__("os").getpid()})', file=sys.stderr)
    t0 = time.monotonic()
    try:
        while a.duration == 0 or time.monotonic() - t0 < a.duration:
            t = time.monotonic() - t0
            ch.write(Command(
                eps_en=True, acc_en=True,
                eps_cmd=30.0 * math.sin(2 * math.pi * 0.2 * t),
                acc_cmd=0.5 * math.sin(2 * math.pi * 0.1 * t),
                eps_speed=100,
            ))
            time.sleep(a.period)
    except KeyboardInterrupt:
        pass
    finally:
        ch.close()


if __name__ == '__main__':
    main()
