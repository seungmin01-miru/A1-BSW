#!/usr/bin/env python3
"""실제 cmd_adapter·race_state_publisher 노드 + 가짜 can_guard(공유메모리만) 실행 시험. CAN·차량에 닿지 않는다.

  source ros2_ws/install/setup.bash
  python3 ros2_ws/src/a1_control_adapter/test/e2e_race_nodes.py [--can-guard-dir safety/can_guard]

pytest 가 모으지 않는 이름이다(노드를 띄우고 약 25 s 걸림). 이 스크립트의 StatusChannel 값은 손으로 쓴 것이다 —
"/race/state 가 **실제 can_guard** 의 phase 를 따라가는가"는 이 시험으로 판정할 수 없다(SIL 시나리오 단계).
"""
import argparse
import os
import random
import subprocess
import sys
import threading
import time
import uuid

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import Trigger

from a1_can_msgs.msg import AdapterStatus, ControlInfo, ControllerOutput, RaceState

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  — " + str(detail) if detail != "" else ""}', flush=True)


class FakeGuard:
    """can_guard 대역: 세그먼트를 만들고 StatusChannel 을 20 ms 마다 쓴다."""

    def __init__(self, proto, names):
        self.proto, self.names = proto, names
        self.cmd = proto.CommandChannel.create(names['cmd'])
        self.hb = proto.HeartbeatChannel.create(names['hb'])
        self.status = proto.StatusChannel.create(names['status'])
        self.st = proto.GuardStatus(race_enabled=True, race_phase=proto.RACE_PHASE_CODE['PRE_GO'],
                                    guard_state=proto.GUARD_STATE_CODE['ACTIVE'], vehicle_speed_limit_kph=30.0)
        self.writing = True
        self.alive = True
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        while self.alive:
            if self.writing:
                self.status.write(self.st)
            time.sleep(0.02)

    def set(self, phase=None, **kw):
        if phase is not None:
            self.st.race_phase = self.proto.RACE_PHASE_CODE[phase]
        for k, v in kw.items():
            setattr(self.st, k, v)

    def restart_status(self):
        """can_guard 재시작 흉내: 세그먼트 unlink → 새로 생성."""
        self.writing = False
        time.sleep(0.05)
        self.status.close()
        self.status = self.proto.StatusChannel.create(self.names['status'])
        self.writing = True

    def close(self):
        self.alive = False
        self.th.join()
        for ch in (self.cmd, self.hb, self.status):
            ch.close()


class Probe(Node):
    def __init__(self):
        super().__init__('e2e_race_probe')
        qb = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.target = self.create_publisher(ControllerOutput, '/control/target', qb)
        self.info = self.create_publisher(ControlInfo, '/control/status/control_info', qb)
        rs = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.race = None
        self.race_rx = None
        self.race_count = 0
        self.create_subscription(RaceState, '/race/state', self.on_race, rs)
        self.adapter = None
        self.create_subscription(AdapterStatus, '/control/adapter/status', self.on_adapter, 10)
        self.disarm = self.create_client(Trigger, '/control/adapter/disarm')
        self.accel, self.steer, self.sending = 0.0, 0.0, True
        self.create_timer(0.02, self.on_tick)

    def on_race(self, m):
        self.race, self.race_rx = m, time.monotonic()
        self.race_count += 1

    def on_adapter(self, m):
        self.adapter = m

    def on_tick(self):
        info = ControlInfo()
        info.steer_position_deg = 12.5
        self.info.publish(info)
        if self.sending:
            m = ControllerOutput()
            m.accel_mps2, m.steer_tire_deg = float(self.accel), float(self.steer)
            self.target.publish(m)


def wait_for(fn, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if fn():
            return time.monotonic() - t0
        time.sleep(0.005)
    return None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--can-guard-dir', default=os.path.join(here, '..', '..', '..', '..', 'safety', 'can_guard'))
    ap.add_argument('--log-dir', default=None, help='노드 출력 저장 폴더(기본: 버림)')
    a = ap.parse_args()
    cg = os.path.abspath(a.can_guard_dir)
    sys.path.insert(0, cg)
    import protocol

    tag = uuid.uuid4().hex[:8]
    names = {'cmd': f'e2e_cmd_{tag}', 'hb': f'e2e_hb_{tag}', 'status': f'e2e_status_{tag}'}
    os.environ['ROS_DOMAIN_ID'] = str(random.randint(100, 200))   # 다른 세션과 안 섞이게
    guard = FakeGuard(protocol, names)
    procs = []
    logs = []

    def launch(pkg, exe, *params):
        cmd = ['ros2', 'run', pkg, exe, '--ros-args'] + [x for p in params for x in ('-p', p)]
        out = subprocess.DEVNULL
        if a.log_dir:
            os.makedirs(a.log_dir, exist_ok=True)
            out = open(os.path.join(a.log_dir, f'{exe}.log'), 'w')
            logs.append(out)
        procs.append(subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT))

    launch('a1_can_bridge', 'race_state_publisher', f'can_guard_dir:={cg}', f'status_shm:={names["status"]}')
    launch('a1_control_adapter', 'cmd_adapter', f'can_guard_dir:={cg}', f'status_shm:={names["status"]}',
           f'cmd_shm:={names["cmd"]}', f'hb_shm:={names["hb"]}', 'auto_arm:=true')

    rclpy.init()
    probe = Probe()
    ex = SingleThreadedExecutor()
    ex.add_node(probe)
    threading.Thread(target=ex.spin, daemon=True).start()
    try:
        return scenario(protocol, guard, probe)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in logs:
            f.close()
        ex.shutdown()
        probe.destroy_node()
        rclpy.shutdown()
        guard.close()


def phase_is(probe, code):
    return lambda: probe.race is not None and probe.race.phase == code


def scenario(protocol, guard, probe):
    # ── 1. PRE_GO: 큰 가속이 계속 와도 arm·래치 없음 ───────────
    probe.accel = 40.0
    t = wait_for(lambda: probe.race is not None, 20.0)
    if t is None:
        check('/race/state 수신(DDS)', False, '20 s 안에 못 받음 — 샌드박스에서 DDS 가 안 되는지 확인')
        return 2
    check('/race/state 수신(DDS)', True, f'{t:.1f} s')
    check('PRE_GO phase=0', phase_is(probe, 0)() and probe.race.phase_name == 'PRE_GO')
    check('0x202 값 전달 30.0', abs(probe.race.vehicle_speed_limit_kph - 30.0) < 1e-3)
    armed = wait_for(lambda: probe.adapter is not None and probe.adapter.steer_armed and probe.adapter.long_armed, 15.0)
    check('자동 arm(PRE_GO, 큰 가속 중)', armed is not None)
    # 노드가 첫 주기에 StatusChannel 을 읽기 전 들어온 입력은 기존 규칙으로 거부될 수 있다(arm 전이라 래치 없음) — arm 뒤 증가분만 본다
    rejected0 = probe.adapter.rejected_count
    time.sleep(0.4)
    ad = probe.adapter
    check('큰 가속 PRE_GO: 래치 없음·기록 중', ad.writing and ad.rejected_count == rejected0, repr(ad.note))
    check('큰 가속 PRE_GO: 잘라 씀(in=15)', abs(ad.in_accel_mps2 - 15.0) < 1e-6, ad.in_accel_mps2)
    cmd, age = guard.cmd.read()
    check('CommandChannel 기록됨', cmd.acc_auto and age < 0.1, (cmd, age))
    check('A8: PRE_GO 동안 Slew 가 실제 핸들각 12.5 근처', abs(ad.out_steer_wheel_deg - 12.5) < 3.0, ad.out_steer_wheel_deg)

    # ── 2. Go → RACING + input_grace: 여전히 래치 없음 ─────────
    t0 = time.monotonic()
    guard.set('RACING', input_grace=True)
    t = wait_for(phase_is(probe, 1), 1.0)
    check('Go: /race/state phase=1', t is not None, None if t is None else f'{(time.monotonic() - t0) * 1000:.0f} ms')
    time.sleep(0.3)
    check('Go 직후 큰 가속: 래치 없음', probe.adapter.writing and probe.adapter.rejected_count == rejected0, repr(probe.adapter.note))
    probe.accel = 0.5
    time.sleep(0.1)
    guard.set(input_grace=False)
    time.sleep(0.1)

    # ── 3. SLOW: RACING + slow_active 표시 2, 큰 가속 래치 없음 ──
    guard.set(slow_active=True, long_override=True, speed_cap_kph=9.3)
    t = wait_for(phase_is(probe, 2), 1.0)
    check('Slow On: /race/state phase=2(SLOW)', t is not None and probe.race.phase_name == 'SLOW'
          and probe.race.long_override and abs(probe.race.speed_cap_kph - 9.3) < 1e-3)
    probe.accel = 40.0
    time.sleep(0.3)
    check('SLOW 큰 가속: 래치 없음', probe.adapter.writing and probe.adapter.rejected_count == rejected0, repr(probe.adapter.note))
    probe.accel = 0.5
    guard.set(slow_active=False, long_override=False, speed_cap_kph=-1.0)
    wait_for(phase_is(probe, 1), 1.0)
    time.sleep(0.1)

    # ── 4. RACING 끊김: 기록·하트비트만 멈춤, 재개 ───────────
    probe.sending = False
    time.sleep(0.25)
    ad = probe.adapter
    _, c1 = guard.cmd.read()
    hb1 = guard.hb.age()
    check('끊김: writing=false·래치 없음', not ad.writing and ad.steer_armed and '끊김' in ad.note and '기록 멈춤' in ad.note, repr(ad.note))
    check('끊김: 명령·하트비트 멈춤(나이 ≥ 약 0.1 s)', c1 > 0.08 and hb1 > 0.08, (round(c1, 3), round(hb1, 3)))
    probe.sending = True
    t = wait_for(lambda: probe.adapter.writing, 1.0)
    check('입력 복귀: 기록 재개', t is not None, None if t is None else f'{t * 1000:.0f} ms')
    time.sleep(0.1)
    check('재개 뒤 note 비움', probe.adapter.note == '', repr(probe.adapter.note))

    # ── 5. RACING 범위 밖 → 래치, PRE_GO 면 D11 해제 ──────────
    probe.accel = 40.0
    t = wait_for(lambda: not probe.adapter.writing and probe.adapter.rejected_count > rejected0, 1.0)
    check('RACING 범위 밖: 래치', t is not None, repr(probe.adapter.note))
    probe.accel = 0.5
    time.sleep(1.3)
    check('RACING: 입력이 돌아와도 자동 해제 없음', not probe.adapter.writing)
    guard.set('PRE_GO')
    t0 = time.monotonic()
    t = wait_for(lambda: probe.adapter.writing, 2.0)
    check('D11: PRE_GO 정상 입력 약 1 s 뒤 자동 해제·재 arm', t is not None and 0.8 < t < 1.5
          and probe.adapter.steer_armed and probe.adapter.long_armed, None if t is None else f'{t:.2f} s')

    # ── 6. PRE_GO NaN → 항상 래치, 다시 D11 ───────────────────
    probe.accel = float('nan')
    t = wait_for(lambda: not probe.adapter.writing, 1.0)
    check('PRE_GO NaN: 래치', t is not None, repr(probe.adapter.note))
    probe.accel = 0.0
    t = wait_for(lambda: probe.adapter.writing, 2.0)
    check('NaN 뒤 D11 해제', t is not None, None if t is None else f'{t:.2f} s')

    # ── 7. StatusChannel 오래됨 → /race/state 멈춤, 어댑터 기존 규칙 ──
    guard.writing = False
    time.sleep(0.25)
    n = probe.race_count
    time.sleep(0.3)
    check('오래됨: /race/state 발행 안 함', probe.race_count == n, probe.race_count - n)
    probe.accel = 40.0
    t = wait_for(lambda: not probe.adapter.writing, 1.0)
    check('오래됨: 큰 가속 → 기존 규칙 래치(PRE_GO 값이어도)', t is not None, repr(probe.adapter.note))
    probe.accel = 0.0

    # ── 8. can_guard 재시작: 새 세그먼트 → 재연결, D11 ─────────
    guard.restart_status()
    n = probe.race_count
    t = wait_for(lambda: probe.race_count > n, 3.0)
    check('재시작: /race/state 재개', t is not None, None if t is None else f'{t:.2f} s')
    t = wait_for(lambda: probe.adapter.writing, 4.0)
    check('재시작: 어댑터도 재연결 → D11 해제', t is not None, None if t is None else f'{t:.2f} s')

    # ── 9. race_enabled=false: 발행 멈춤 + 150 ms 래치 ─────────
    guard.set(race_enabled=False)
    time.sleep(0.15)
    n = probe.race_count
    time.sleep(0.2)
    check('race_enabled=false: 발행 안 함', probe.race_count == n)
    probe.sending = False
    t = wait_for(lambda: not probe.adapter.writing, 1.0)
    time.sleep(0.25)
    check('race_enabled=false: 끊김 → 기존 고장 래치(입력 끊김 사유)', t is not None and '제어기 입력 끊김' in probe.adapter.note,
          repr(probe.adapter.note))
    probe.sending = True
    time.sleep(0.5)
    check('race_enabled=false: 입력 복귀해도 해제 없음', not probe.adapter.writing)

    # ── 10. 운영자 disarm 뒤 wait_restart: PRE_GO 라도 다시 안 씀 ──
    guard.set('PRE_GO', race_enabled=True)
    time.sleep(0.1)
    if not probe.disarm.wait_for_service(timeout_sec=5.0):
        check('disarm 서비스', False)
        return summary()
    fut = probe.disarm.call_async(Trigger.Request())
    wait_for(fut.done, 5.0)
    check('disarm 응답', fut.done() and fut.result().success, fut.result().message if fut.done() else '')
    time.sleep(1.5)
    ad = probe.adapter
    check('disarm 뒤: 기록 안 함(wait_restart)·재 arm 없음', not ad.writing and not ad.steer_armed and not ad.long_armed, repr(ad.note))
    return summary()


def summary():
    bad = [n for n, ok in results if not ok]
    print(f'\n{len(results) - len(bad)}/{len(results)} 통과' + (f' — 실패: {bad}' if bad else ''))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
