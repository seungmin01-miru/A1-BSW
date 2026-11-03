#!/usr/bin/env python3
"""정지 시험 감시(kv0): 0x201 바퀴가 돌면 lift_cmd 파이프에 'brake 100'. 0x200 변화·우리 0x210 명령 변화를 한 줄씩."""
import can, sys, time, datetime
fifo = sys.argv[1]
bus = can.Bus(channel='kv0', interface='socketcan')
hm = lambda: datetime.datetime.now().strftime('%T.%f')[:-4]
last200 = last210 = None; tripped = 0; last_rx = time.time(); quiet = False
print(f'{hm()} 감시 시작 (바퀴 움직임 → brake 100)', flush=True)
while True:
    m = bus.recv(0.5)
    if m is None:
        if not quiet and time.time() - last_rx > 3: print(f'{hm()} ⚠ kv0 프레임 없음', flush=True); quiet = True
        continue
    last_rx = time.time(); quiet = False; d = m.data
    if m.arbitration_id == 0x201 and len(d) >= 6:
        rs = d[0] | (d[1] & 0x0F) << 8; rr = (d[1] >> 4) | (d[2] << 4); ls = d[3] | (d[4] & 0x0F) << 8; lr = (d[4] >> 4) | (d[5] << 4)
        if (rs or rr or ls or lr) and time.time() - tripped > 2:
            tripped = time.time()
            with open(fifo, 'w') as f: f.write('brake 100\n')
            print(f'{hm()} 🛑 바퀴 움직임 (speed {rs/10:.1f}/{ls/10:.1f} km/h, rpm {rr}/{lr}) → brake 100', flush=True)
    elif m.arbitration_id == 0x200 and len(d) >= 5:
        st = int.from_bytes(d[0:2], 'little', signed=True) / 10; br = int.from_bytes(d[2:4], 'little', signed=True) / 10
        au = f'{d[4] & 1}{(d[4] >> 1) & 1}{(d[4] >> 2) & 1}'
        if last200 is None or abs(st - last200[0]) >= 2 or abs(br - last200[1]) >= 0.5 or au != last200[2]:
            print(f'{hm()} 차량 0x200: 조향 {st:+.1f}° 브레이크 {br:.1f} auto(조/브/가) {au}', flush=True); last200 = (st, br, au)
    elif m.arbitration_id == 0x210 and len(d) >= 6:
        k = (int.from_bytes(d[0:2], 'little', signed=True) / 10, int.from_bytes(d[2:4], 'little'), d[4], f'{d[5] & 1}{(d[5] >> 1) & 1}{(d[5] >> 2) & 1}')
        if k != last210:
            print(f'{hm()} 우리 0x210: 조향 {k[0]:+.1f}° 브레이크 {k[1]}% 가속 {k[2]}% auto {k[3]}', flush=True); last210 = k
