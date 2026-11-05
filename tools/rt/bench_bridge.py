#!/usr/bin/env python3
"""can_raw_bridge 발행 루프 처리량 벤치(CAN 장치 불필요) — 11-04 bag 52 s 밀림의 원인 확인용.

  source /opt/ros/humble/setup.bash [&& source ros2_ws/install/setup.bash]
  python3 tools/rt/bench_bridge.py [--n 5000]

두 루프를 같은 메시지로 잰다.
  old: 프레임마다 publish + rclpy.spin_once(timeout=0)   (11-04 까지의 can_raw_bridge)
  new: 프레임마다 publish, spin_once 는 0.2 s 에 한 번     (2026-11-05 수정)
결과의 "최대 프레임/s" 가 버스 부하(11-04 약 580 /s, 필터 후 0x200/0x201/0x210/0x156/0x157 약 300 /s)보다 커야
큐가 안 쌓인다. 메시지는 a1_can_msgs/CanFrame(빌드돼 있으면), 없으면 같은 크기의 std_msgs/UInt8MultiArray.
구독자(ros2 bag record)가 붙으면 비용이 더 든다 — --sub 로 같은 프로세스 구독자를 붙여 볼 수 있다.
"""
import argparse
import time

import rclpy
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

try:
    from a1_can_msgs.msg import CanFrame as Msg
    KIND = 'a1_can_msgs/CanFrame'
except ImportError:
    from std_msgs.msg import UInt8MultiArray as Msg
    KIND = 'std_msgs/UInt8MultiArray(대용)'


def make():
    m = Msg()
    if KIND.startswith('a1'):
        m.header.frame_id = 'can'
        m.id, m.dlc, m.data = 0x200, 8, list(range(8))
    else:
        m.data = bytes(range(8))
    return m


def run(node, pub, n, spin_every):
    m = make()
    t0 = time.perf_counter()
    nxt = t0
    for _ in range(n):
        pub.publish(m)
        if spin_every is None:
            rclpy.spin_once(node, timeout_sec=0.0)
        else:
            now = time.perf_counter()
            if now >= nxt:
                rclpy.spin_once(node, timeout_sec=0.0)
                nxt = now + spin_every
    return (time.perf_counter() - t0) / n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n', type=int, default=5000)
    ap.add_argument('--sub', action='store_true', help='같은 노드에 구독자 하나를 붙인다')
    a = ap.parse_args()
    rclpy.init()
    node = rclpy.create_node('bench_bridge')
    qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST)
    pub = node.create_publisher(Msg, '/bench/can/raw', qos)
    if a.sub:
        node.create_subscription(Msg, '/bench/can/raw', lambda _m: None, qos)
    run(node, pub, 500, 0.2)   # 예열
    print(f'메시지 {KIND}, n={a.n}, 구독자 {"있음" if a.sub else "없음"}')
    for name, se in (('old (프레임마다 spin_once)', None), ('new (spin_once 0.2 s 마다)', 0.2)):
        dt = run(node, pub, a.n, se)
        print(f'  {name:28s} 프레임당 {dt * 1e6:7.1f} µs → 최대 {1 / dt:8.0f} 프레임/s')
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
