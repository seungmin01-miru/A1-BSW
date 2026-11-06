#!/usr/bin/env python3
"""
can_guard StatusChannel(공유메모리) → /race/state (a1_can_msgs/RaceState). 모델 팀이 Finish 처리에 쓰는 유일한 경기 상태 경로(D6).

  ros2 run a1_can_bridge race_state_publisher --ros-args -p can_guard_dir:=~/git/A1-BSW/safety/can_guard

판단하지 않는다 — can_guard 가 쓴 값을 옮기기만 한다(D5). 차량으로도 아무것도 보내지 않는다.
  - poll_period_s(10 ms)마다 읽어 값이 바뀌면 바로, 안 바뀌어도 publish_period_s(20 ms = 50 Hz)마다 낸다.
  - QoS RELIABLE + TRANSIENT_LOCAL, depth 1 — 늦게 켠 노드도 최신값을 받는다.
  - StatusChannel 이 없음·오래됨·race_enabled=false 이면 **발행하지 않고** /diagnostics(1 Hz)로만 알린다.
    TRANSIENT_LOCAL 이라 늦게 켠 구독자는 마지막 값을 받을 수 있다 → 구독 쪽은 header.stamp 로 신선도를 본다.
  - can_guard 가 재시작하면 세그먼트를 다시 연다(race_status.StatusReader).
"""
import os
import sys
import time

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_bridge.race_status import PublishGate, RESULT_NO_DATA, RESULT_NO_SEGMENT, status_to_fields, StatusReader
from a1_can_msgs.msg import RaceState


def _import_protocol(can_guard_dir):
    """safety/can_guard/protocol.py 를 그대로 쓴다(cmd_adapter 와 같은 방식)."""
    d = os.path.expanduser(can_guard_dir)
    if not os.path.isfile(os.path.join(d, 'protocol.py')):
        raise RuntimeError(f'can_guard_dir 에 protocol.py 없음: {d}')
    if d not in sys.path:
        sys.path.insert(0, d)
    import protocol
    return protocol


def race_state_qos():
    return QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


class RaceStatePublisher(Node):
    """StatusChannel → /race/state."""

    def __init__(self):
        super().__init__('a1_race_state_publisher')
        p = self.declare_parameter
        default_cg = os.path.join(os.environ.get('A1_BSW_ROOT', '~/git/A1-BSW'), 'safety', 'can_guard')
        self.proto = _import_protocol(p('can_guard_dir', default_cg).value)
        name = p('status_shm', getattr(self.proto, 'STATUS_SHM_NAME_DEFAULT', 'a1_can_guard_status')).value
        self.reader = StatusReader(self.proto, name)
        self.gate = PublishGate(p('publish_period_s', 0.02).value)
        self.pub = self.create_publisher(RaceState, '/race/state', race_state_qos())
        self.diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self.published = 0
        self.state = ''     # 지난 주기의 "발행 안 함" 사유. 빈 문자열 = 발행 중
        self.create_timer(p('poll_period_s', 0.01).value, self.on_poll)
        self.create_timer(1.0, self.on_diag)
        self.get_logger().info(f'시작. StatusChannel={name} → /race/state')

    def _why_not(self, st):
        """발행하지 않는 이유(없으면 빈 문자열)."""
        if st is None:
            if self.reader.result == RESULT_NO_SEGMENT:
                return 'StatusChannel 없음 — can_guard 미실행 또는 구버전'
            if self.reader.result == RESULT_NO_DATA:
                return 'StatusChannel 오래됨(0.1 s 초과) — can_guard 멈춤?'
            return 'StatusChannel 없음'
        if not st.race_enabled:
            return '경기 모드 꺼짐(can_guard --race 아님)'
        return ''

    def on_poll(self):
        now = time.monotonic()
        st = self.reader.poll(now)
        why = self._why_not(st)
        if why != self.state:
            self.get_logger().warn(f'/race/state 발행 중단: {why}' if why else '/race/state 발행 시작')
            self.state = why
        if why:
            return
        fields = status_to_fields(st, self.proto)
        if not self.gate.should_publish(fields, now):
            return
        msg = RaceState()
        msg.header.stamp = self.get_clock().now().to_msg()
        for k, v in fields.items():
            setattr(msg, k, v)
        self.pub.publish(msg)
        self.published += 1

    def on_diag(self):
        d = DiagnosticStatus(hardware_id='A1_USER', name='a1_race_state')
        d.level = DiagnosticStatus.WARN if self.state and not self.state.startswith('경기 모드') else DiagnosticStatus.OK
        d.message = self.state or '발행 중'
        d.values = [KeyValue(key='published', value=str(self.published)),
                    KeyValue(key='status_read', value=self.reader.result),
                    KeyValue(key='torn_reads', value=str(self.reader.torn_count))]
        arr = DiagnosticArray(status=[d])
        arr.header.stamp = self.get_clock().now().to_msg()
        self.diag_pub.publish(arr)

    def destroy_node(self):
        self.reader.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RaceStatePublisher()
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
