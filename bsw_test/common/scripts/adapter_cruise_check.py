#!/usr/bin/env python3
"""가속 히스테리시스 SIL(지시서 WP0 관문): 실제 cmd_adapter 에 20 km/h 순항 목표 흉내를 넣고 가속 % 전환 횟수를 센다.

  source /opt/ros/humble/setup.bash && source ros2_ws/install/setup.bash
  python3 bsw_test/common/scripts/adapter_cruise_check.py --out <폴더> [--seconds 60]

목표 = 0.03~0.18 m/s² 잡음(11-04 bsw5 순항 구간 범위, 50 Hz). 비교: track.yaml 그대로(히스테리시스) vs 이탈값 0.1(예전 동작).
can_guard 대신 이 스크립트가 공유메모리를 만들어 어댑터 출력을 읽는다 → **CAN·차량에 닿지 않는다**. 합격: 전환 ≤ 5 회/분.
결과: cruise_<label>.csv(t, in_accel, acc_pct, brake_pct), 표준 출력 요약.
"""
import argparse
import csv
import os
import random
import subprocess
import sys
import time
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
GUARD = os.path.join(REPO, 'safety', 'can_guard')
sys.path.insert(0, GUARD)
from protocol import CommandChannel, HeartbeatChannel  # noqa: E402

import rclpy  # noqa: E402
from a1_can_msgs.msg import ControlInfo, ControllerOutput  # noqa: E402


def run(label, seconds, off, out, rng):
    tag = uuid.uuid4().hex[:6]
    cmd_ch = CommandChannel.create(name='cc' + tag)
    hb_ch = HeartbeatChannel.create(name='ch' + tag)
    params = os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')
    argv = [sys.executable, '-m', 'a1_control_adapter.cmd_adapter', '--ros-args', '--params-file', params,
            '-p', f'can_guard_dir:={GUARD}', '-p', f'cmd_shm:=cc{tag}', '-p', f'hb_shm:=ch{tag}', '-p', 'auto_arm:=true',
            '--log-level', 'warn']
    if off is not None:
        argv += ['-p', f'long_deadband_off_mps2:={off}']
    proc = subprocess.Popen(argv, stderr=subprocess.DEVNULL)
    node = rclpy.create_node('cruise_check_' + tag)
    pub_t = node.create_publisher(ControllerOutput, '/control/target', 10)
    pub_i = node.create_publisher(ControlInfo, '/control/status/control_info', 10)
    rows = []
    try:
        t_arm = time.time() + 30
        armed = False
        t0 = None
        accel = 0.1
        next_tick = time.time()
        while True:
            now = time.time()
            if now >= next_tick:
                accel = rng.uniform(0.03, 0.18)
                next_tick += 0.02
                m = ControllerOutput()
                m.accel_mps2 = accel
                m.steer_tire_deg = 0.0
                pub_t.publish(m)
                pub_i.publish(ControlInfo())
                c = cmd_ch.read()[0]
                if c is not None and c.acc_auto and not armed:
                    armed, t0 = True, now
                if armed:
                    rows.append((now - t0, accel, c.acc_cmd_pct, c.brake_cmd_pct))
                    if now - t0 >= seconds:
                        break
                elif now > t_arm:
                    raise RuntimeError('어댑터가 arm 하지 않음')
            time.sleep(0.001)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        node.destroy_node()
        cmd_ch.close()
        hb_ch.close()
    with open(os.path.join(out, f'cruise_{label}.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['t', 'in_accel_mps2', 'acc_pct', 'brake_pct'])
        w.writerows(rows)
    sw = sum(1 for a, b in zip(rows, rows[1:]) if (a[2] > 0) != (b[2] > 0))
    return sw / (rows[-1][0] / 60.0), sw, rows[-1][0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--seconds', type=float, default=60.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.environ['ROS_DOMAIN_ID'] = str(100 + os.getpid() % 30)
    rclpy.init()
    rc = 0
    with open(os.path.join(a.out, 'cruise_summary.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['label', 'switch_per_min', 'switches', 'seconds'])
        for label, off in (('hysteresis', None), ('old', 0.1)):
            per_min, sw, dur = run(label, a.seconds, off, a.out, random.Random(4))
            w.writerow([label, f'{per_min:.1f}', sw, f'{dur:.1f}'])
            verdict = 'PASS' if per_min <= 5 else 'FAIL'
            if label == 'hysteresis':
                rc = 0 if per_min <= 5 else 1
            print(f'{verdict if label == "hysteresis" else "참고"}  {label}: 가속 전환 {per_min:.1f} 회/분 ({sw} 회, {dur:.0f} s)')
    rclpy.shutdown()
    return rc


if __name__ == '__main__':
    sys.exit(main())
