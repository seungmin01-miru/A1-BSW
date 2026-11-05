#!/usr/bin/env python3
"""Kvaser 채널을 can_guard(송신)와 미러(수신·기록)가 함께 쓸 때 수신이 막히는지 — CAN 원본에서 확인(2026-11-05).

  python3 tools/drive_log/kvaser_rx_check.py measurements/2026-11-04_drive_log/raw_can/auto_bsw*/kvaser_ch0.log.zst

구간: 우리 0x210 이 나가는 동안(첫 0x210 ~ 마지막 0x210). 그 구간에서 차량 0x200·0x201(21 ms)과 앱 0x156(10 ms)을
몇 % 받았는지(받은 수 ÷ 구간 ÷ 주기), 가장 긴 공백, 1 s 동안 한 프레임도 없던 구간 수를 낸다.
수신이 막히면 받은 비율이 떨어지고 공백이 길어진다. 0x210 은 우리 송신의 로컬 에코(미러가 기록)라 우리 송신 주기도 보인다.
"""
import subprocess
import sys

NOMINAL = {0x200: 0.021, 0x201: 0.021, 0x156: 0.010, 0x210: 0.020}


def read(path):
    txt = subprocess.run(['zstd', '-dc', path], capture_output=True, text=True, check=True).stdout if path.endswith('.zst') \
        else open(path).read()
    for ln in txt.splitlines():
        p = ln.split()
        if len(p) >= 3 and '#' in p[2]:
            yield float(p[0][1:-1]), int(p[2].split('#')[0], 16)


def main():
    print('세션\t구간 s\tID\t받은 수\t기대 대비 %\t최대 공백 ms\t1 s 무수신')
    for path in sys.argv[1:]:
        ts = {}
        for t, i in read(path):
            if i in NOMINAL:
                ts.setdefault(i, []).append(t)
        tx = ts.get(0x210, [])
        if not tx:
            continue
        a, b = tx[0], tx[-1]
        name = path.split('/')[-2]
        for i, per in NOMINAL.items():
            w = [t for t in ts.get(i, []) if a <= t <= b]
            if not w:
                print(f'{name}\t{b - a:.0f}\t0x{i:03X}\t0\t0\t-\t-')
                continue
            gaps = [y - x for x, y in zip(w, w[1:])]
            holes = sum(1 for g in gaps if g >= 1.0)
            print(f'{name}\t{b - a:.0f}\t0x{i:03X}\t{len(w)}\t{len(w) / ((b - a) / per) * 100:.1f}\t'
                  f'{max(gaps) * 1e3:.1f}\t{holes}')


if __name__ == '__main__':
    main()
