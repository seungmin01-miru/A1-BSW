#!/usr/bin/env python3
"""
제어기 대역 — /control/target 에 시험 패턴을 낸다(제어기가 완성되기 전 SIL·현장 shadow 점검용).

  ros2 run a1_control_adapter test_target_pub                                   # 기본: 사인 조향 + 가감속 계단
  ros2 run a1_control_adapter test_target_pub --ros-args -p mode:=const -p accel_mps2:=-1.0 -p steer_tire_deg:=2.0
  ros2 run a1_control_adapter test_target_pub --ros-args -p rate_hz:=10.0       # 느린 제어기 흉내

mode
  pattern : 조향 = steer_amp_deg·sin(2πt/steer_period_s), 가감속 = accel_steps 를 step_s 마다 순환
  const   : 조향 steer_tire_deg, 가감속 accel_mps2 고정(ros2 param set 으로 실행 중 변경 가능)
"""
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_msgs.msg import ControllerOutput


class TestTargetPub(Node):
    """시험 패턴 발행기."""

    def __init__(self):
        super().__init__('a1_test_target_pub')
        p = self.declare_parameter
        self.mode = p('mode', 'pattern').value
        self.steer_amp = p('steer_amp_deg', 5.0).value
        self.steer_period = p('steer_period_s', 4.0).value
        self.steps = list(p('accel_steps', [0.0, 0.5, 0.0, -1.0, -2.0, 0.0]).value)
        self.step_s = p('step_s', 2.0).value
        p('accel_mps2', 0.0)
        p('steer_tire_deg', 0.0)
        qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE)
        self.pub = self.create_publisher(ControllerOutput, '/control/target', qos)
        self.t0 = time.monotonic()
        self.create_timer(1.0 / p('rate_hz', 50.0).value, self.tick)

    def tick(self):
        t = time.monotonic() - self.t0
        m = ControllerOutput()
        m.header.stamp = self.get_clock().now().to_msg()
        if self.mode == 'const':   # ros2 param set 으로 실행 중에 바꿀 수 있게 매번 읽는다
            m.accel_mps2 = float(self.get_parameter('accel_mps2').value)
            m.steer_tire_deg = float(self.get_parameter('steer_tire_deg').value)
        else:
            m.steer_tire_deg = self.steer_amp * math.sin(2 * math.pi * t / self.steer_period)
            m.accel_mps2 = float(self.steps[int(t / self.step_s) % len(self.steps)])
        self.pub.publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = TestTargetPub()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
