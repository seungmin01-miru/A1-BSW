#!/usr/bin/env python3
"""
Decode the real-vehicle USER_ protocol (0x200 / 0x201 / 0x210) into ROS2 topics + per-axis E2E 진단.

2026-10-06 실차 DBC(DBC/A1_dbc_fixed.dbc) 로 전환 — 이전 EAIT 디코더(spd/eps/acc/imu, 0x710~0x713)를 대체한다.
메시지마다 노드를 따로 두던 이전 구조와 달리 **노드 하나**가 /interface/can/read/raw 를 한 번만 구독해 세 토픽으로
나눈다 — §5.C 실측에서 이 경로의 지연은 DDS 구독·디스패치가 지배했으므로 구독 수를 줄이는 쪽이 낫다.

  /control/status/control_info   a1_can_msgs/ControlInfo     ← 0x200 (조향·브레이크 위치, 축별 auto, 카운터 3개)
  /control/status/wheel_info     a1_can_msgs/WheelInfo       ← 0x201 (좌우 바퀴 속도·rpm, 카운터 2개)
  /control/status/command_on_bus a1_can_msgs/ControlCommand  ← 0x210
      (버스 위 명령 — can_guard 든 다른 송신자든, 감시용)
  /diagnostics                   1 Hz, 카운터 축별 건너뜀 + 0x210 송신 여부

⚠️ 이 토픽들은 **감시 등급**(§5.C 설계 규칙): 10 ms 대 제어 루프가 직접 구독하지 말 것. 차량으로 명령을 보내는 건
can_guard(safety/can_guard/) 하나뿐이다 — 이 노드는 아무것도 송신하지 않는다.

파싱은 dbc_bits.unpack(비트 단위 추출, 런타임 DBC 없음). 정확성은 test/test_a1_decode.py 가 cantools + 수정본 DBC +
9/17 실차 프레임과 대조한다. 0x210 steer_command 배율은 ×1 deg(업체 DBC 의 ×0.1 은 오류).

  ros2 run a1_can_bridge a1_status_decoder
"""
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_bridge.dbc_bits import unpack, unpack_raw
from a1_can_bridge.e2e import AliveCounter
from a1_can_msgs.msg import CanFrame, ControlCommand, ControlInfo, WheelInfo

FRAME_CONTROL_INFO = 0x200
FRAME_WHEEL_INFO = 0x201
FRAME_CONTROL_COMMAND = 0x210


def decode_control_info(data8):
    """8바이트 USER_control_info → ControlInfo(header·alive_ok 미설정)."""
    d = data8
    return ControlInfo(
        steer_position_deg=unpack(d, 0, 16, signed=True, scale=0.1),
        brake_position=unpack(d, 16, 16, signed=True, scale=0.1),
        steer_auto=bool(unpack_raw(d, 32, 1)),
        brake_auto=bool(unpack_raw(d, 33, 1)),
        acc_auto=bool(unpack_raw(d, 34, 1)),
        steer_live_counter=unpack_raw(d, 40, 8),
        brake_live_counter=unpack_raw(d, 48, 8),
        acc_live_counter=unpack_raw(d, 56, 8),
    )


def decode_wheel_info(data8):
    """8바이트 USER_right_wheel_info → WheelInfo(header·alive_ok 미설정). 바퀴당 12비트 속도 + 12비트 rpm."""
    d = data8
    return WheelInfo(
        right_speed_kph=unpack(d, 0, 12, scale=0.1),
        right_rpm=unpack_raw(d, 12, 12),
        left_speed_kph=unpack(d, 24, 12, scale=0.1),
        left_rpm=unpack_raw(d, 36, 12),
        right_live_counter=unpack_raw(d, 48, 8),
        left_live_counter=unpack_raw(d, 56, 8),
    )


def decode_control_command(data8):
    """8바이트 USER_control_command → ControlCommand(header 미설정). 조향 ×1 deg."""
    d = data8
    return ControlCommand(
        steer_cmd_deg=int(unpack(d, 0, 16, signed=True)),
        brake_cmd_pct=unpack_raw(d, 16, 16),
        acc_cmd_pct=unpack_raw(d, 32, 8),
        steer_auto=bool(unpack_raw(d, 40, 1)),
        brake_auto=bool(unpack_raw(d, 41, 1)),
        acc_auto=bool(unpack_raw(d, 42, 1)),
    )


class A1StatusDecoder(Node):
    """raw 1회 구독 → 0x200/0x201/0x210 세 토픽 + 축별 카운터 진단."""

    def __init__(self):
        super().__init__('a1_status_decoder')
        qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST)
        self._sub = self.create_subscription(
            CanFrame, '/interface/can/read/raw', self._on_frame, qos)
        self._pub_info = self.create_publisher(ControlInfo, '/control/status/control_info', qos)
        self._pub_wheel = self.create_publisher(WheelInfo, '/control/status/wheel_info', qos)
        self._pub_cmd = self.create_publisher(
            ControlCommand, '/control/status/command_on_bus', qos)
        self._diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self._alive = {k: AliveCounter() for k in ('steer', 'brake', 'acc', 'right', 'left')}
        self._n = {FRAME_CONTROL_INFO: 0, FRAME_WHEEL_INFO: 0, FRAME_CONTROL_COMMAND: 0}
        self._n_cmd_interval = 0
        self.create_timer(1.0, self._on_diag_timer)
        self.get_logger().info(
            '0x200/0x201/0x210 → /control/status/{control_info,wheel_info,command_on_bus}')

    def _check(self, axis, cnt):
        ok, gap = self._alive[axis].update(cnt)
        if not ok:
            self.get_logger().warn(
                f'{axis} live_counter {gap}건 건너뜀 (last={self._alive[axis].last})')
        return ok

    def _on_frame(self, frame: CanFrame):
        fid = frame.id
        if fid not in self._n:
            return
        if frame.dlc < 8:
            self.get_logger().warn(f'0x{fid:03X} DLC 부족: {frame.dlc} (무시)')
            return
        if fid == FRAME_CONTROL_INFO:
            out = decode_control_info(frame.data)
            out.header = frame.header
            out.steer_alive_ok = self._check('steer', out.steer_live_counter)
            out.brake_alive_ok = self._check('brake', out.brake_live_counter)
            out.acc_alive_ok = self._check('acc', out.acc_live_counter)
            self._pub_info.publish(out)
        elif fid == FRAME_WHEEL_INFO:
            out = decode_wheel_info(frame.data)
            out.header = frame.header
            out.right_alive_ok = self._check('right', out.right_live_counter)
            out.left_alive_ok = self._check('left', out.left_live_counter)
            self._pub_wheel.publish(out)
        else:
            out = decode_control_command(frame.data)
            out.header = frame.header
            self._pub_cmd.publish(out)
            self._n_cmd_interval += 1
        self._n[fid] += 1

    def _status(self, name, keys):
        st = DiagnosticStatus(hardware_id='A1_USER', name=name)
        skips = {k: self._alive[k].pop_interval_skips() for k in keys}
        if self._alive[keys[0]].frames == 0:
            st.level, st.message = DiagnosticStatus.STALE, '수신 없음'
        elif any(skips.values()):
            st.level = DiagnosticStatus.WARN
            st.message = ', '.join(f'{k} {v}건 건너뜀' for k, v in skips.items() if v) + '(최근 1s)'
        else:
            st.level, st.message = DiagnosticStatus.OK, 'ok'
        st.values = [KeyValue(key='frames', value=str(self._alive[keys[0]].frames))] + [
            KeyValue(key=f'{k}_total_skips', value=str(self._alive[k].total_skips)) for k in keys]
        return st

    def _on_diag_timer(self):
        cmd = DiagnosticStatus(hardware_id='A1_USER',
                               name='can/USER_control_command (0x210) on bus')
        n, self._n_cmd_interval = self._n_cmd_interval, 0
        cmd.level = DiagnosticStatus.OK
        cmd.message = f'최근 1s {n}건' if n else '송신자 없음(최근 1s)'
        cmd.values = [KeyValue(key='frames', value=str(self._n[FRAME_CONTROL_COMMAND]))]
        arr = DiagnosticArray(status=[
            self._status('can/USER_control_info (0x200)', ('steer', 'brake', 'acc')),
            self._status('can/USER_right_wheel_info (0x201)', ('right', 'left')),
            cmd,
        ])
        arr.header.stamp = self.get_clock().now().to_msg()
        self._diag_pub.publish(arr)


def main():
    """Run the a1_status_decoder node until interrupted."""
    rclpy.init()
    node = A1StatusDecoder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(f'종료 — 0x200 {node._n[FRAME_CONTROL_INFO]}건, '
                               f'0x201 {node._n[FRAME_WHEEL_INFO]}건, '
                               f'0x210 {node._n[FRAME_CONTROL_COMMAND]}건')
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
