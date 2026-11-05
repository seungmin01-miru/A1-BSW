#!/usr/bin/env python3
"""보고서 3장·2장 그림용 시나리오 — can_guard 를 실제 코드 그대로(python-can `virtual` 버스, 같은 프로세스) 돌려
0x210 출력을 기록한다. vcan·sudo 없이 돈다(개발 PC). 지연 값은 판정에 쓰지 않는다(로직 확인용).

  python3 scenarios.py <저장소 루트> <출력 폴더>      # python-can 필요 → data/scenarios.csv, data/events_<시나리오>.csv

시나리오(모두 차속 15 km/h 로 달리는 가짜 차량: 0x201 15 km/h, 0x200 핸들 = 아래 값):
  follow  앱 En 1.0–2.5 s(차량 핸들 +5°, 우리 명령 −49.8°) → 해제        --estop-remote-steer follow(기본)
  hold    같은 입력                                                      --estop-remote-steer hold(예전)
  loss    1.0 s 에 제어기 입력·하트비트가 끊김(= 어댑터 고장 래치)
  disarm  1.0 s 에 어댑터 disarm(= auto 000 명령을 계속 씀)
  down    1.0 s 에 estop.py(= bsw_down 1단계), 3.0 s 에 can_guard 종료(= bsw_down 2단계)
  hold_disarm   1.0 s 입력 끊김(래치) → 1.5 s(HOLDING 중) disarm
  stop_disarm   1.0 s 입력 끊김(래치) → 3.5 s(STOPPED 뒤) disarm
  estop_disarm  1.0 s estop.py → 2.0 s disarm
"""
import csv
import os
import signal
import sys
import threading
import time
import uuid

REPO, OUT = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
sys.path.insert(0, os.path.join(REPO, 'safety', 'can_guard'))
import can  # noqa: E402
import can_guard  # noqa: E402
from protocol import Command, CommandChannel, EstopChannel, HeartbeatChannel  # noqa: E402

DUR = {'follow': 4.0, 'hold': 4.0, 'loss': 4.5, 'disarm': 3.0, 'down': 3.0,
       'hold_disarm': 3.0, 'stop_disarm': 5.0, 'estop_disarm': 3.5}
DISARM_AT = {'disarm': 1.0, 'hold_disarm': 1.5, 'stop_disarm': 3.5, 'estop_disarm': 2.0}
LOSS = {'loss': (1.0, 99), 'hold_disarm': (1.0, 1.5), 'stop_disarm': (1.0, 3.5)}


def f200(st):
    d = bytearray(8)
    d[0:2] = int(round(st * 10)).to_bytes(2, 'little', signed=True)
    d[4] = 7
    return bytes(d)


def f201(kph):
    raw = int(round(kph * 10)) & 0xFFF
    v = raw | (raw << 24)
    return v.to_bytes(8, 'little')


def f156(en):
    d = bytearray(8)
    d[1] = 0x96
    d[0] = d[2] = en
    return bytes(d)


def dec(d):
    return (int.from_bytes(d[0:2], 'little', signed=True) * 0.1, int.from_bytes(d[2:4], 'little'), d[4], d[5] & 7)


def run(name):
    tag = uuid.uuid4().hex[:6]
    os.makedirs(os.path.join(OUT, 'data'), exist_ok=True)
    evp = os.path.join(OUT, 'data', f'events_{name}.csv')
    if os.path.exists(evp):
        os.remove(evp)
    args = can_guard.parse_args(['--interface', 'virtual', '--channel', 'r' + tag, '--precheck-s', '0', '--quiet',
                                 '--cmd-shm', 'c' + tag, '--hb-shm', 'h' + tag, '--estop-shm', 'e' + tag,
                                 '--estop-remote-steer', 'hold' if name == 'hold' else 'follow', '--event-log', evp])
    rec = []

    def test():
        time.sleep(0.4)
        cmd, hb = CommandChannel.open('c' + tag), HeartbeatChannel.open('h' + tag)
        bus = can.Bus(channel='r' + tag, interface='virtual')
        t0 = time.monotonic()
        estopped = False
        while True:
            t = time.monotonic() - t0
            if t >= DUR[name]:
                break
            en, vst, write, auto = 0, -49.8, True, True
            if name in ('follow', 'hold') and t >= 1.0:
                vst = 5.0
                en = 1 if t < 2.5 else 0
            if name in LOSS and LOSS[name][0] <= t < LOSS[name][1]:
                write = False
            if name in DISARM_AT and t >= DISARM_AT[name]:
                auto = False
            if name in ('down', 'estop_disarm') and t >= 1.0 and not estopped:
                ch = EstopChannel.open('e' + tag)
                ch.trigger('bsw_down')
                ch.close()
                estopped = True
            if write:
                cmd.write(Command(steer_auto=auto, brake_auto=auto, acc_auto=auto,
                                  steer_cmd_deg=-49.8 if auto else 0.0, acc_cmd_pct=40.0 if auto else 0.0))
                hb.beat()
            bus.send(can.Message(arbitration_id=0x156, data=f156(en), is_extended_id=False))
            bus.send(can.Message(arbitration_id=0x200, data=f200(vst), is_extended_id=False))
            bus.send(can.Message(arbitration_id=0x201, data=f201(15.0), is_extended_id=False))
            te = time.monotonic() + 0.01
            while True:
                m = bus.recv(timeout=max(0.0, te - time.monotonic()))
                if m is None:
                    break
                if m.arbitration_id == 0x210:
                    rec.append((round(time.monotonic() - t0, 4), *dec(m.data)))
        if name == 'down':   # bsw_down 2단계: can_guard 종료 뒤 1 s 동안 버스 관찰
            os.kill(os.getpid(), signal.SIGINT)
            t_end = time.monotonic() + 1.0
            while time.monotonic() < t_end:
                m = bus.recv(timeout=0.05)
                if m is not None and m.arbitration_id == 0x210:
                    rec.append((round(time.monotonic() - t0, 4), *dec(m.data)))
            return
        os.kill(os.getpid(), signal.SIGINT)

    th = threading.Thread(target=test, daemon=True)
    th.start()
    try:
        can_guard.run(args)
    except KeyboardInterrupt:
        pass
    th.join(timeout=2)
    return rec


def main():
    if len(sys.argv) > 4 and sys.argv[3] == '--one':   # 시나리오 하나(can_guard 종료 플래그가 전역이라 프로세스를 나눈다)
        name = sys.argv[4]
        with open(os.path.join(OUT, 'data', f'_tmp_{name}.csv'), 'w', newline='') as f:
            csv.writer(f).writerows((name, *r) for r in run(name))
        return
    import subprocess
    rows = []
    for name in DUR:
        subprocess.run([sys.executable, os.path.abspath(__file__), REPO, OUT, '--one', name],
                       stderr=subprocess.DEVNULL, check=True)
        tmp = os.path.join(OUT, 'data', f'_tmp_{name}.csv')
        with open(tmp) as f:
            rows += [tuple(r) for r in csv.reader(f)]
        os.remove(tmp)
        print(name, sum(1 for x in rows if x[0] == name), 'frames', flush=True)
    with open(os.path.join(OUT, 'data', 'scenarios.csv'), 'w', newline='') as f:
        f.write('# scenarios.py — can_guard(virtual 버스) 0x210 출력. t = 시나리오 시작 기준 s, 개발 PC 참고값\n')
        w = csv.writer(f)
        w.writerow(['scenario', 't', 'steer_deg', 'brake_pct', 'acc_pct', 'auto_bits'])
        w.writerows(rows)


if __name__ == '__main__':
    main()
