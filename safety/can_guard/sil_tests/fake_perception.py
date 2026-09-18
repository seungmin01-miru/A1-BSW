#!/usr/bin/env python3
"""§7.3 P-2 시험용 — 인지 프로세스 자리를 흉내낸다. HeartbeatChannel 에 주기적으로 beat() 만 한다.

  python3 fake_perception.py --hb-shm p2_hb --period 0.05
"""
import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit('/', 2)[0])
from protocol import HeartbeatChannel  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hb-shm', required=True)
    ap.add_argument('--period', type=float, default=0.05)
    ap.add_argument('--duration', type=float, default=0.0)
    a = ap.parse_args()

    ch = HeartbeatChannel.open(a.hb_shm)
    print(f'[fake_perception] {a.hb_shm} 에 하트비트 시작 (pid={__import__("os").getpid()})', file=sys.stderr)
    t0 = time.monotonic()
    try:
        while a.duration == 0 or time.monotonic() - t0 < a.duration:
            ch.beat()
            time.sleep(a.period)
    except KeyboardInterrupt:
        pass
    finally:
        ch.close()


if __name__ == '__main__':
    main()
