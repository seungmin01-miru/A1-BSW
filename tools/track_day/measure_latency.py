#!/usr/bin/env python3
"""제어기 출력 → 버스 0x210 종단 지연 측정 — /control/target 발행 시각 vs 그 값이 실린 첫 0x210 의 수신 시각.

경로: 이 스크립트(제어기 대역) → DDS → a1_cmd_adapter(50 Hz 기록) → 공유메모리 → can_guard(20 ms 주기) → CAN.
브레이크 명령을 −1.0 / −2.0 m/s²(track.yaml 기준 40 % / 70 %)로 번갈아 바꾸고, 바뀐 % 가 0x210 에 처음 보이는
시각과의 차이를 잰다. 두 시각 모두 CLOCK_REALTIME(rclpy 발행 직전 time.time(), SocketCAN 커널 수신 타임스탬프).

  # 먼저 can_guard + 브리지 + 어댑터가 떠 있어야 한다(어댑터 disarm 상태여도 된다 — 이 스크립트가 arm_long 을 부르고
  # 끝나면 disarm 한다). 조향은 건드리지 않는다.
  python3 tools/track_day/measure_latency.py --channel vcan0 --steps 200

⚠️ 실차 버스에서는 브레이크 명령이 실제로 나간다 — vcan* 가 아니면 --vehicle-stationary 를 줘야 실행된다
(리프트 위 또는 정지·주차 상태에서만).
"""
import argparse
import os
import statistics
import sys
import threading
import time

import can
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import SetBool, Trigger

from a1_can_msgs.msg import ControllerOutput

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'race_day'))
from a1_proto import decode_0x210  # noqa: E402

LEVELS = ((-1.0, 40), (-2.0, 70))   # (가감속 m/s², track.yaml 잠정 표에서의 브레이크 %)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--steps', type=int, default=200)
    ap.add_argument('--step-s', type=float, default=0.25, help='값을 바꾸는 간격(초) — 20 ms 주기들과 맞물리지 않게')
    ap.add_argument('--rate-hz', type=float, default=50.0, help='/control/target 발행 주기')
    ap.add_argument('--vehicle-stationary', action='store_true', help='실차 버스에서 실행 허용(정지·리프트 확인 후)')
    ap.add_argument('--csv', default='', help='지연 샘플을 저장할 경로')
    a = ap.parse_args()
    if not a.channel.startswith('vcan') and not a.vehicle_stationary:
        sys.exit('실차 버스로 보이는 채널 — 차량 정지를 확인했으면 --vehicle-stationary')

    rclpy.init()
    node = Node('a1_latency_probe')
    qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE)
    pub = node.create_publisher(ControllerOutput, '/control/target', qos)
    arm = node.create_client(SetBool, '/control/adapter/arm_long')
    disarm = node.create_client(Trigger, '/control/adapter/disarm')

    seen = []          # (can 수신 시각, brake %)
    stop = threading.Event()
    bus = can.Bus(channel=a.channel, interface='socketcan', receive_own_messages=True,
                  can_filters=[{'can_id': 0x210, 'can_mask': 0x7FF}])

    def rx():
        while not stop.is_set():
            m = bus.recv(timeout=0.1)
            if m is not None and len(m.data) == 8:
                d = decode_0x210(m.data)
                if d['brake_auto']:
                    seen.append((m.timestamp, d['brake_pct']))
    threading.Thread(target=rx, daemon=True).start()

    def publish(accel):
        msg = ControllerOutput()
        msg.header.stamp = node.get_clock().now().to_msg()
        msg.accel_mps2, msg.steer_tire_deg = accel, 0.0
        t = time.time()
        pub.publish(msg)
        return t

    def call(cli, req, what):
        if not cli.wait_for_service(timeout_sec=5.0):
            sys.exit(f'{what} 서비스 없음 — 어댑터가 떠 있는지 확인')
        fut = cli.call_async(req)
        rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
        return fut.result()

    period = 1.0 / a.rate_hz
    t_end = time.monotonic() + 1.0                  # 1초 워밍업(어댑터가 입력을 신선하게 보게)
    while time.monotonic() < t_end:
        publish(LEVELS[0][0])
        time.sleep(period)
    r = call(arm, SetBool.Request(data=True), 'arm_long')
    if not r or not r.success:
        sys.exit(f'arm_long 거부: {r.message if r else "응답 없음"}')

    changes = []       # (발행 시각, 기대 brake %)
    try:
        for i in range(a.steps + 1):
            accel, expect = LEVELS[(i + 1) % 2]
            t0 = publish(accel)
            changes.append((t0, expect))
            nxt = time.monotonic() + a.step_s
            while time.monotonic() < nxt - period:
                time.sleep(period)
                publish(accel)
            time.sleep(max(0.0, nxt - time.monotonic()))
    finally:
        call(disarm, Trigger.Request(), 'disarm')
        time.sleep(0.2)
        stop.set()
        bus.shutdown()
        node.destroy_node()
        rclpy.shutdown()

    lat = []
    j = 0
    for t0, expect in changes[1:]:                  # 첫 변경은 arm 직후라 제외
        while j < len(seen) and seen[j][0] < t0:
            j += 1
        k = j
        while k < len(seen) and seen[k][1] != expect:
            k += 1
        if k < len(seen):
            lat.append((seen[k][0] - t0) * 1000)
    if not lat:
        sys.exit('측정값 없음 — 0x210 이 안 보임(can_guard·채널 확인)')
    if a.csv:
        with open(a.csv, 'w') as f:
            f.write('latency_ms\n' + ''.join(f'{x:.3f}\n' for x in lat))
    print(f'/control/target → 0x210 지연  n={len(lat)}/{len(changes) - 1}  '
          f'최소 {min(lat):.1f}  중앙값 {statistics.median(lat):.1f}  p99 {pct(lat, 99):.1f}  최대 {max(lat):.1f} ms')


if __name__ == '__main__':
    main()
