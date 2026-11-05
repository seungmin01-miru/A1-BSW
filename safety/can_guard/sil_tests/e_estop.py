#!/usr/bin/env python3
"""00 SAF-ESTOP SIL 시나리오 (2026-11-04) — vcan 에 can_guard 를 실제 프로세스로 띄워 비상 정지 경로를 잰다.

  sudo ip link add dev vcan0 type vcan && sudo ip link set up vcan0     # 한 번
  python3 sil_tests/e_estop.py [--channel vcan0] [--trials 10] [--only E1,E3]

| # | 시나리오 | 판정 |
|---|---|---|
| E1 | 주행 중(가짜 제어 노드) estop.py 공유메모리 누름 | 누름 → 버스 0x210(가속 0, 브레이크 ≥ 시작값, auto 111) ≤ 40 ms |
| E2 | SIGUSR1                                         | E1 과 같음 |
| E3 | 원격(앱) 0x156 En=1 프레임                      | 첫 원격 프레임 → 버스 반영 ≤ 40 ms (confirm 2프레임 포함) |
| E4 | 래치: 누른 뒤 제어 노드가 계속 명령             | 2 s 동안 ESTOP 유지, 브레이크 단조 증가·상한 82, 가속 0, auto 111 |
| E5 | 제어 노드 kill(명령 끊김, P-1 과 같은 경로)     | 마지막 명령 → HOLDING 첫 0x210(브레이크 ≥ 30) ≤ 100 ms (감지 기준) |
| E6 | silent INIT: 명령 없이 can_guard 시작           | 1 s 동안 0x210 0 프레임 (auto 000 송신 없음) |
| E7 | 원격 우선(11-04): 앱 En 중 차량 핸들이 앱 값으로 감 → 앱 해제 | En 중 0x210 조향이 0x200 실제값(±1°)을 0.2 s 안에 따라감,
|    |                                                 | 해제 뒤 1 s 동안 조향 튐 ≤ 1°, 브레이크 ≥ 30·가속 0·auto 111 유지 (2026-11-05) |

`--interface udp_multicast --channel 239.74.163.2` 로 vcan 없이(sudo 없이) 돌릴 수 있다 — 로직 확인용, 지연 판정은 vcan.

E1~E3 의 40 ms = can_guard 주기 20 ms × 2(주기 위상 + 송신). 차량 브레이크 무반응(56~112 ms)·차량 수락(≤ 22 ms)은
실차에서만 잰다 → 00 goal.md "제동 시작 ≤ 200 ms" 는 여기 값 + 실차 값으로 판정.
"""
import argparse
import os
import shlex
import signal
import statistics
import subprocess
import sys
import time
import uuid

import can

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from protocol import EstopChannel  # noqa: E402
from sil_tests._common import (can_guard_cmd, start_fake_control_node,  # noqa: E402
                               start_fake_perception, wait_for_shm)

PERIOD = 0.02
INTERFACE = 'socketcan'
GUARD_ARGS = ()   # --guard-args 로 채움(예: 격리 코어·SCHED_FIFO)
LIMIT_TRIGGER_S = 0.040
LIMIT_WATCHDOG_S = 0.100


def _decode_210(data):
    d = bytes(data)
    return {'steer': int.from_bytes(d[0:2], 'little', signed=True) * 0.1,
            'brake': int.from_bytes(d[2:4], 'little'), 'acc': d[4],
            'auto': (d[5] & 1, (d[5] >> 1) & 1, (d[5] >> 2) & 1)}


def _f156_en():
    d = bytearray(8)
    d[0] = 1
    d[2] = 1
    return bytes(d)


class Rig:
    """can_guard + 가짜 제어 노드 + 가짜 인지 + 관찰 버스 한 세트."""

    def __init__(self, channel, control=True, extra=()):
        tag = uuid.uuid4().hex[:8]
        self.cmd, self.hb, self.es = f'e_cmd_{tag}', f'e_hb_{tag}', f'e_es_{tag}'
        self.channel = channel
        self.bus = can.Bus(channel=channel, interface=INTERFACE, receive_own_messages=False)
        iface = () if INTERFACE == 'socketcan' else ('--interface', INTERFACE)
        argv = can_guard_cmd(self.cmd, self.hb, channel, 0.05, PERIOD,
                             ('--estop-shm', self.es, '--quiet', *iface, *GUARD_ARGS, *extra))
        self.guard = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ctrl = self.perc = None
        if not (wait_for_shm(self.cmd) and wait_for_shm(self.hb) and wait_for_shm(self.es)):
            self.close()
            raise RuntimeError(f'can_guard 공유메모리가 안 생김 — {channel} 이 UP 인지 확인(ip -br link show {channel})')
        if control:
            self.ctrl = start_fake_control_node(self.cmd, PERIOD)
            self.perc = start_fake_perception(self.hb, 0.05)

    def frames(self, seconds):
        out = []
        t_end = time.monotonic() + seconds
        while time.monotonic() < t_end:
            m = self.bus.recv(timeout=0.02)
            if m is not None and m.arbitration_id == 0x210:
                out.append((time.monotonic(), _decode_210(m.data)))
        return out

    def first_after(self, t0, pred, timeout=1.0):
        t_end = t0 + timeout
        while time.monotonic() < t_end:
            m = self.bus.recv(timeout=0.01)
            if m is not None and m.arbitration_id == 0x210:
                f = _decode_210(m.data)
                if pred(f):
                    return time.monotonic() - t0, f
        return None, None

    def drain(self):
        while self.bus.recv(timeout=0) is not None:
            pass

    def close(self):
        for p in (self.ctrl, self.perc, self.guard):
            if p is not None and p.poll() is None:
                p.send_signal(signal.SIGINT if p is self.guard else signal.SIGTERM)
        for p in (self.ctrl, self.perc, self.guard):
            if p is not None:
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.bus.shutdown()


def _children(pid):
    out = []
    try:
        for tid in os.listdir(f'/proc/{pid}/task'):
            with open(f'/proc/{pid}/task/{tid}/children') as f:
                out += [int(x) for x in f.read().split()]
    except OSError:
        pass
    return out


def guard_python_pid(root_pid):
    """--guard-args 에 --rt-priority 가 있으면 can_guard 는 `sudo chrt -f N sudo -u USER python3 can_guard.py` 로 뜬다.
    11-04 대회 PC `--rt` 에서 E2 가 FAIL 한 원인: SIGUSR1 을 맨 바깥 sudo 에 보냈는데, sudo 는 **명령과 같은 프로세스
    그룹에서 온 신호는 중계하지 않는다**(이중 전달 방지). 실제 운용의 `pkill -USR1 -f can_guard.py` 처럼 파이썬
    프로세스에 직접 보낸다. sudo 래퍼가 없으면 root_pid 자신이다."""
    todo, seen = [root_pid], set()
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            with open(f'/proc/{p}/cmdline', 'rb') as f:
                argv = f.read().split(b'\0')
        except OSError:
            continue
        if argv and b'python' in os.path.basename(argv[0]) and any(a.endswith(b'can_guard.py') for a in argv):
            return p
        todo += _children(p)
    return root_pid


def is_estop_frame(f):
    return f['acc'] == 0 and f['brake'] >= 30 and f['auto'] == (1, 1, 1)


def _trigger_trial(channel, how):
    rig = Rig(channel)
    try:
        rig.frames(0.5)   # ACTIVE 안정
        rig.drain()
        if how == 'shm':
            tool = EstopChannel.open(rig.es)
            t0 = time.monotonic()
            tool.trigger('e_estop')
            tool.close()
        elif how == 'sigusr1':
            pid = guard_python_pid(rig.guard.pid)
            t0 = time.monotonic()
            os.kill(pid, signal.SIGUSR1)
        else:   # remote
            t0 = time.monotonic()
            for _ in range(3):   # 앱 10 ms 주기 흉내
                rig.bus.send(can.Message(arbitration_id=0x156, data=_f156_en(), is_extended_id=False))
                time.sleep(0.01)
        return rig.first_after(t0, is_estop_frame)
    finally:
        rig.close()


def scenario_trigger(channel, trials, how):
    lat = []
    for _ in range(trials):
        dt, f = _trigger_trial(channel, how)
        if dt is None:
            return False, '비상 정지 프레임이 1 s 안에 안 나옴'
        lat.append(dt)
    ok = max(lat) <= LIMIT_TRIGGER_S
    return ok, (f'n={len(lat)} 중앙 {statistics.median(lat) * 1e3:.1f} / 최대 {max(lat) * 1e3:.1f} ms '
                f'(기준 ≤ {LIMIT_TRIGGER_S * 1e3:.0f})')


def scenario_latch(channel):
    rig = Rig(channel)
    try:
        rig.frames(0.5)
        tool = EstopChannel.open(rig.es)
        tool.trigger('e_estop')
        tool.close()
        fr = rig.frames(2.5)
        est = [f for _, f in fr if f['brake'] >= 30]
        if not est:
            return False, 'ESTOP 프레임 없음'
        brakes = [f['brake'] for f in est]
        mono = all(b2 >= b1 for b1, b2 in zip(brakes, brakes[1:]))
        ok = (mono and max(brakes) == 82 and all(f['acc'] == 0 for f in est)
              and all(f['auto'] == (1, 1, 1) for f in est) and fr[-1][1]['brake'] == 82)
        return ok, (f'{len(est)} 프레임, 브레이크 {brakes[0]}→{brakes[-1]} % 단조={mono}, '
                    f'가속 최대 {max(f["acc"] for f in est)}, auto {sorted({f["auto"] for f in est})}')
    finally:
        rig.close()


def scenario_watchdog(channel, trials):
    lat = []
    for _ in range(trials):
        rig = Rig(channel)
        try:
            rig.frames(0.5)
            rig.drain()
            rig.ctrl.kill()
            t_kill = time.monotonic()
            rig.ctrl.wait()
            dt, f = rig.first_after(t_kill, lambda f: f['acc'] == 0 and f['brake'] >= 30)
            if dt is None:
                return False, 'HOLDING 프레임 없음'
            lat.append(dt)
        finally:
            rig.close()
    # 마지막 명령은 kill 직전(≤ 20 ms 전)이므로 kill 기준 지연 + 20 ms 가 "감지" 상한
    worst = max(lat) + PERIOD
    return worst <= LIMIT_WATCHDOG_S, (f'n={len(lat)} kill→HOLDING 첫 프레임 중앙 {statistics.median(lat) * 1e3:.1f} / '
                                       f'최대 {max(lat) * 1e3:.1f} ms, 마지막 명령 기준 ≤ {worst * 1e3:.1f} ms '
                                       f'(기준 ≤ {LIMIT_WATCHDOG_S * 1e3:.0f})')


def scenario_silent_init(channel):
    rig = Rig(channel, control=False)
    try:
        fr = rig.frames(1.0)
        return len(fr) == 0, f'명령 없이 1 s: 0x210 {len(fr)} 프레임'
    finally:
        rig.close()


def _f200(steer_deg):
    d = bytearray(8)
    d[0:2] = int(round(steer_deg * 10)).to_bytes(2, 'little', signed=True)
    d[4] = 0b111
    return bytes(d)


def scenario_remote_priority(channel):
    """11-04 bsw4 재현: ESTOP 진입 때 명령 조향은 가짜 제어 노드 값(±30° 사인), 앱 En 동안 차량 0x200 은 +5°(앱 값).
    해제 뒤에도 0x200 은 +5° — can_guard 가 진입값으로 돌아가면 그만큼 튄다."""
    rig = Rig(channel)
    app_steer = 5.0
    try:
        rig.frames(0.5)
        rig.drain()

        def pump(seconds, en):
            out, t_end, k = [], time.monotonic() + seconds, 0
            while time.monotonic() < t_end:
                rig.bus.send(can.Message(arbitration_id=0x156, data=_f156_en() if en else bytes(8),
                                         is_extended_id=False))
                if k % 2 == 0:
                    rig.bus.send(can.Message(arbitration_id=0x200, data=_f200(app_steer), is_extended_id=False))
                k += 1
                t_next = time.monotonic() + 0.01
                while True:
                    m = rig.bus.recv(timeout=max(0.0, t_next - time.monotonic()))
                    if m is None:
                        break
                    if m.arbitration_id == 0x210:
                        out.append((time.monotonic(), _decode_210(m.data)))
            return out

        t0 = time.monotonic()
        during = pump(1.0, True)
        after = pump(1.0, False)
        late = [f for t, f in during if t - t0 >= 0.2]
        follow = bool(late) and all(abs(f['steer'] - app_steer) <= 1.0 for f in late)
        jump = max((abs(f['steer'] - app_steer) for _, f in after), default=99)
        safe = all(is_estop_frame(f) for _, f in during[3:] + after)
        ok = follow and jump <= 1.0 and safe and len(after) > 20
        return ok, (f'En 중 조향 {late[0]["steer"] if late else None:+.1f}→{late[-1]["steer"] if late else None:+.1f}° '
                    f'(0x200 {app_steer:+.1f}), 해제 뒤 최대 차이 {jump:.1f}°, ESTOP 출력 유지 {safe}, '
                    f'프레임 {len(during)}/{len(after)}')
    finally:
        rig.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--trials', type=int, default=10)
    ap.add_argument('--only', default='', help='예: E1,E3')
    ap.add_argument('--interface', default='socketcan',
                    help='python-can 인터페이스. udp_multicast 면 vcan·sudo 없이 로직만 확인(지연 판정은 socketcan)')
    ap.add_argument('--guard-args', default='',
                    help='can_guard 에 덧붙일 인자, 예 "--cpu 8 --rt-priority 90" (sudo chrt 로 감쌈, 대회 PC 실측용)')
    a = ap.parse_args()
    global GUARD_ARGS, INTERFACE
    GUARD_ARGS = tuple(shlex.split(a.guard_args))
    INTERFACE = a.interface
    sel = set(a.only.split(',')) if a.only else None
    plan = [
        ('E1', '공유메모리 누름', lambda: scenario_trigger(a.channel, a.trials, 'shm')),
        ('E2', 'SIGUSR1', lambda: scenario_trigger(a.channel, a.trials, 'sigusr1')),
        ('E3', '원격 0x156 En=1', lambda: scenario_trigger(a.channel, a.trials, 'remote')),
        ('E4', '래치·서서히 제동', lambda: scenario_latch(a.channel)),
        ('E5', '명령 끊김 → HOLDING', lambda: scenario_watchdog(a.channel, a.trials)),
        ('E6', 'silent INIT', lambda: scenario_silent_init(a.channel)),
        ('E7', '원격 우선: 앱 En 중 조향 추종·해제 튐 없음', lambda: scenario_remote_priority(a.channel)),
    ]
    fails = 0
    for key, name, fn in plan:
        if sel and key not in sel:
            continue
        ok, detail = fn()
        fails += not ok
        print(f'[{key}] {"PASS" if ok else "FAIL"}  {name}: {detail}', flush=True)
    print('전체:', 'PASS' if fails == 0 else f'FAIL {fails}건')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
