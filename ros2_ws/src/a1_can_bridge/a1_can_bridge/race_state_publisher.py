#!/usr/bin/env python3
"""
대회 신호 CAN → /race/state (a1_can_msgs/RaceState). 전달만 하고 판단하지 않는다.

  ros2 run a1_can_bridge race_state_publisher
  (파라미터 can_guard_dir 기본 = $A1_BSW_ROOT/safety/can_guard)

/interface/can/read/raw 를 구독해 safety/can_guard/race_signal.py 로 해석한다
(can_guard 이벤트 로그와 같은 코드). 값이 바뀌면 곧바로, 안 바뀌어도 publish_period_s
(20 ms = 50 Hz)마다 낸다. 차량으로는 아무것도 보내지 않는다.
QoS RELIABLE + TRANSIENT_LOCAL, depth 1 — 늦게 켠 노드도 최신값을 받는다.
프레임이 하나도 없어도 발행한다(나이 −1).
"""
import math
import os
import sys
import time

from a1_can_msgs.msg import CanFrame, RaceState
from builtin_interfaces.msg import Time
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy


def _import_race_signal(can_guard_dir):
    """safety/can_guard/race_signal.py 를 그대로 쓴다(cmd_adapter 의 protocol.py 방식)."""
    d = os.path.expanduser(can_guard_dir)
    if not os.path.isfile(os.path.join(d, 'race_signal.py')):
        raise RuntimeError(f'can_guard_dir 에 race_signal.py 없음: {d}')
    if d not in sys.path:
        sys.path.insert(0, d)
    import race_signal
    return race_signal


def race_state_qos():
    """/race/state QoS — 구독 쪽도 같은 값을 쓸 것."""
    return QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                      reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


def _to_time(t):
    """초(float) 또는 None → builtin_interfaces/Time. None = 0."""
    if t is None:
        return Time()
    sec = int(math.floor(t))
    return Time(sec=sec, nanosec=min(int((t - sec) * 1e9), 999999999))


class RaceStatePublisher(Node):
    """/interface/can/read/raw → /race/state."""

    def __init__(self):
        super().__init__('a1_race_state_publisher')
        p = self.declare_parameter
        root = os.environ.get('A1_BSW_ROOT', '~/git/A1-BSW')
        default_cg = os.path.join(root, 'safety', 'can_guard')
        rs = _import_race_signal(p('can_guard_dir', default_cg).value)
        self.ids = frozenset(rs.RACE_FRAME_IDS)
        alive_s = p('res_alive_timeout_s', rs.DEFAULT_RES_ALIVE_TIMEOUT_S).value
        self.tracker = rs.RaceTracker(alive_timeout_s=alive_s)
        self.pub = self.create_publisher(RaceState, '/race/state', race_state_qos())
        self.published = 0
        self._key = None
        raw_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT,
                             history=HistoryPolicy.KEEP_LAST)
        self.create_subscription(CanFrame, '/interface/can/read/raw', self.on_frame, raw_qos)
        self.create_timer(p('publish_period_s', 0.02).value, self.on_timer)
        self.get_logger().info('시작. /interface/can/read/raw → /race/state (전달만)')

    def on_frame(self, frame):
        """프레임마다: 대회 신호면 해석, 값이 바뀌었으면 즉시 발행."""
        if frame.id not in self.ids:
            return
        t = frame.header.stamp.sec + frame.header.stamp.nanosec * 1e-9
        self.tracker.feed(frame.id, bytes(frame.data), t if t > 0 else time.time())
        self.tracker.update(time.time())
        self._log_records()
        if self.tracker.state_key() != self._key:
            self._publish()

    def on_timer(self):
        """주기 발행. alive 끊김은 프레임이 없을 때 판정되므로 여기서도 본다."""
        self.tracker.update(time.time())
        self._log_records()
        self._publish()

    def _log_records(self):
        for name, detail in self.tracker.take_records():
            self.get_logger().info(f'{name}: {detail}')

    def _publish(self):
        f = self.tracker.fields(time.time())
        msg = RaceState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.last_signal_stamp = _to_time(f.pop('last_signal_t'))
        for k, v in f.items():
            setattr(msg, k, v)
        self.pub.publish(msg)
        self._key = self.tracker.state_key()
        self.published += 1


def main(args=None):
    """Run the node until interrupted."""
    rclpy.init(args=args)
    node = RaceStatePublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(f'종료 — 발행 {node.published}건')
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
