#!/usr/bin/env python3
"""대회 신호가 can_guard 동작을 바꾸지 않는지 — 실제 can_guard 를 python-can virtual 버스로 두 번(신호 있음/없음) 돌려 비교한다.

  python3 race_log_check.py <with|without> <이벤트 CSV>   # 한 번 돌리고 0x210 값을 JSON 으로 출력

차량·CAN 장치에 닿지 않는다(vcan 도 필요 없음). test/test_race_log_guard.py 가 부른다.
"""
import json
import os
import signal
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import can  # noqa: E402
import can_guard  # noqa: E402
from protocol import Command, CommandChannel, HeartbeatChannel  # noqa: E402

DUR_S = 2.4


def main():
    mode, evp = sys.argv[1], sys.argv[2]
    tag = uuid.uuid4().hex[:6]
    args = can_guard.parse_args(['--interface', 'virtual', '--channel', 'g' + tag, '--precheck-s', '0', '--quiet',
                                 '--cmd-shm', 'c' + tag, '--hb-shm', 'h' + tag, '--estop-shm', 'e' + tag,
                                 '--event-log', evp])
    rec = []

    def drive():
        time.sleep(0.3)
        cmd, hb = CommandChannel.open('c' + tag), HeartbeatChannel.open('h' + tag)
        bus = can.Bus(channel='g' + tag, interface='virtual')
        t0 = time.monotonic()
        k = 0
        while time.monotonic() - t0 < DUR_S:
            t = time.monotonic() - t0
            cmd.write(Command(steer_auto=True, brake_auto=True, acc_auto=True, steer_cmd_deg=-20.0, acc_cmd_pct=40.0))
            hb.beat()
            bus.send(can.Message(arbitration_id=0x200, data=bytes(4) + bytes([7]) + bytes(3), is_extended_id=False))
            bus.send(can.Message(arbitration_id=0x201, data=(150 | (150 << 24)).to_bytes(8, 'little'),
                                 is_extended_id=False))
            if mode == 'with':
                k += 1
                go = 0x80 if t > 0.3 else 0
                res = 0x80 if 0.8 < t < 1.2 else 0
                bus.send(can.Message(arbitration_id=0x124, data=bytes([go]) + bytes(7), is_extended_id=False))
                if t < 1.6:   # 그 뒤 alive 끊김
                    bus.send(can.Message(arbitration_id=0x127, data=bytes([res]) + bytes(6) + bytes([k & 0xFF]),
                                         is_extended_id=False))
                bus.send(can.Message(arbitration_id=0x128, data=bytes(8), is_extended_id=False))
                bus.send(can.Message(arbitration_id=0x202, data=bytes.fromhex('2C01000000000000'), is_extended_id=False))
            te = time.monotonic() + 0.01
            while True:
                m = bus.recv(timeout=max(0.0, te - time.monotonic()))
                if m is None:
                    break
                if m.arbitration_id == 0x210:
                    rec.append(bytes(m.data).hex())
        os.kill(os.getpid(), signal.SIGINT)

    th = threading.Thread(target=drive, daemon=True)
    th.start()
    try:
        rc = can_guard.run(args)
    except KeyboardInterrupt:
        rc = None
    th.join(timeout=2)
    print(json.dumps({'frames': sorted(set(rec)), 'n': len(rec), 'rc': rc}))


if __name__ == '__main__':
    main()
