#!/usr/bin/env python3
"""떠 있는 SIL BSW(bsw_up --sil)에 모델처럼 20 Hz 로 /control/target 을 넣어 전체 경로를 확인한다(run_verify.sh 의 bsw 단계).

  python3 bsw_test/09_ENGINE_ROOM/scripts/e2e_bsw_up.py --session <세션 폴더>

자동 arm → 제동·가속 변환 → 깃발 전달(가짜 대회 서버) → 입력 끊김 → 고장 래치·STOPPED 를 본다.
**can_guard 가 vcan 채널이 아니면 시작을 거부한다.** 가짜 차량은 물리 모델이 없어 차속 값은 의미 없다(가속 시간을 짧게 둔다).
"""
import argparse
import os
import subprocess
import sys
import threading
import time

import rclpy
from a1_can_msgs.msg import AdapterStatus, ControllerOutput, RaceState
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
SERVER = os.path.join(REPO, 'sil', 'vcan', 'fake_race_server.py')
SCENARIO = os.path.join(REPO, 'sil', 'vcan', 'race_scenarios', 'S02_go.yaml')


def guard_on_vcan():
    r = subprocess.run(['pgrep', '-af', 'safety/can_guard/can_guard.py'], capture_output=True, text=True).stdout
    return '--channel vcan' in r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--session', required=True)
    a = ap.parse_args()
    if not guard_on_vcan():
        print('can_guard 가 vcan 채널이 아니다 — 시험 거부', file=sys.stderr)
        return 2
    rclpy.init()
    node = rclpy.create_node('e2e_bsw_up')
    pub = node.create_publisher(ControllerOutput, '/control/target', 10)
    st, race = {}, {}
    node.create_subscription(AdapterStatus, '/control/adapter/status', lambda m: st.update(m=m), 10)
    qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(RaceState, '/race/state', lambda m: race.update(m=m), qos)
    target = {'a': -3.0}
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            m = ControllerOutput()
            m.accel_mps2 = float(target['a'])
            pub.publish(m)
            time.sleep(0.05)     # 모델 실제 주기 20 Hz

    def wait(sec):
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.02)

    def wait_until(fn, sec):
        """조건이 될 때까지. 새 노드가 서로 찾는 데 몇 초 걸린다(ROS_DOMAIN 분리 시 2 s 넘게 — 판정을 먼저 하면 가짜 실패)."""
        end = time.time() + sec
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.02)
            if fn():
                return True
        return False

    checks = []

    def check(what, ok, detail=''):
        checks.append(ok)
        print(f'{"PASS" if ok else "FAIL"}  {what}  {detail}', flush=True)

    threading.Thread(target=loop, daemon=True).start()
    try:
        if not wait_until(lambda: 'm' in st and 'm' in race, 15.0):
            check('어댑터·/race/state 수신', False, f'어댑터 {"m" in st}, race {"m" in race}')
            return 1
        armed = wait_until(lambda: st['m'].steer_armed and st['m'].long_armed, 10.0)
        wait(1.0)
        m = st['m']
        check('자동 arm(정지 목표 20 Hz)', armed and m.writing, f'입력 나이 {m.input_age_s * 1e3:.0f} ms')
        check('입력 나이 < 끊김 기준 150 ms', 0 <= m.input_age_s < 0.15)
        check('−3 m/s² → 브레이크만', m.out_brake_pct > 0 and m.out_acc_pct == 0, f'브레이크 {m.out_brake_pct:.1f} %')
        check('타이어 0° → 표 영점', abs(m.out_steer_wheel_deg - (-9.0)) < 0.5, f'핸들 {m.out_steer_wheel_deg:.1f}°')
        check('표 버전 표시', bool(m.long_map_version) and m.note.startswith('map'), f'v{m.long_map_version}, {m.note}')
        target['a'] = 0.3
        wait(2.0)
        m = st['m']
        check('+0.3 m/s² → 가속만', m.out_acc_pct > 0 and m.out_brake_pct == 0, f'가속 {m.out_acc_pct:.1f} %')
        target['a'] = -3.0
        wait(4.0)
        m = st['m']
        check('제동으로 복귀', m.out_brake_pct > 0 and m.out_acc_pct == 0, f'브레이크 {m.out_brake_pct:.1f} %')
        srv = subprocess.Popen([sys.executable, SERVER, SCENARIO, '--channel', 'vcan0', '--quiet'])
        wait(4.5)
        r = race['m']
        check('깃발 전달', r.seen_go and r.sig_go and r.last_signal == 'GO', f'seen_go {r.seen_go}, last {r.last_signal}, 나이 {r.race_signal_age_s:.3f} s')
        srv.wait(timeout=10)
        stop.set()               # 입력 끊김
        wait(3.5)
        m = st['m']
        check('입력 끊김 → 고장 래치', not m.writing and '끊김' in m.note, m.note)
    finally:
        stop.set()
        node.destroy_node()
        rclpy.shutdown()
    ev = os.path.join(a.session, 'can_guard_events.csv')
    text = open(ev, encoding='utf-8', errors='replace').read() if os.path.exists(ev) else ''
    check('can_guard STOPPED', '->STOPPED' in text)
    check('깃발은 이벤트 로그에만', 'race_signal' in text and 'ESTOP' not in text)
    return 0 if all(checks) else 1


if __name__ == '__main__':
    sys.exit(main())
