#!/usr/bin/env python3
"""can_guard 비상 정지 누르기 / 해제 / 상태 보기 (2026-11-04, bsw_test 00 SAF-ESTOP).

  python3 estop.py               # 누름 — can_guard 가 다음 주기(≤ 20 ms)에 ESTOP
  python3 estop.py --reset       # 해제 요청 — 정지 요청이 모두 꺼졌고 차속 ≤ 정지 문턱일 때만 can_guard 가 받음
  python3 estop.py --status

공유메모리가 없으면(can_guard 가 안 떠 있음) 예비 경로로 `pkill -USR1 -f can_guard.py` 를 안내한다.
누름은 래치된다: 해제 전까지 제어 명령이 다시 와도 can_guard 는 ESTOP 에 머문다.
"""
import argparse
import sys

from protocol import ESTOP_SHM_NAME_DEFAULT, EstopChannel


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--estop-shm', default=ESTOP_SHM_NAME_DEFAULT)
    ap.add_argument('--source', default='estop.py', help='로그에 남길 이름(최대 15자)')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--reset', action='store_true')
    g.add_argument('--status', action='store_true')
    a = ap.parse_args()

    try:
        ch = EstopChannel.open(a.estop_shm)
    except FileNotFoundError:
        print(f'🛑 공유메모리 {a.estop_shm} 없음 — can_guard 가 안 떠 있다. '
              f'떠 있는데 이 메시지면: pkill -USR1 -f can_guard.py', file=sys.stderr)
        return 2
    try:
        if a.status:
            st = ch.read()
            print('요청 없음(한 번도 안 씀)' if st is None else
                  f'정지 요청={"ON" if st.request else "off"} source={st.source} reset_seq={st.reset_seq}')
        elif a.reset:
            ch.reset(a.source)
            print('해제 요청 보냄 — can_guard 로그에서 "estop_reset accepted/refused" 확인')
        else:
            ch.trigger(a.source)
            print('🛑 비상 정지 요청 보냄')
    finally:
        ch.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
