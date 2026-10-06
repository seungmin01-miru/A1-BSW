#!/usr/bin/env python3
"""
Decode EAIT_INFO_ACC (0x711) into /control/status/acc + E2E alive-count 진단.

eps_decoder.py 와 같은 구조(§2 카테고리 C "E2E(alive_count) 체크") — 다른 메시지에 확장할 때도
이 패턴(비트 파싱 + AliveCounter + 1 Hz /diagnostics)을 그대로 따른다.

  ros2 run a1_can_bridge acc_decoder
"""
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_bridge.dbc_bits import unpack, unpack_raw
from a1_can_bridge.e2e import AliveCounter
from a1_can_msgs.msg import AccStatus, CanFrame

FRAME_ID = 0x711


def decode_acc(data8):
    """Decode an 8-byte EAIT_INFO_ACC payload into an AccStatus (header 미설정)."""
    d = data8
    return AccStatus(
        en=bool(unpack_raw(d, 0, 1)),
        control_board_status=unpack_raw(d, 1, 3),
        user_can_err=bool(unpack_raw(d, 4, 1)),
        veh_err=bool(unpack_raw(d, 6, 1)),
        err=bool(unpack_raw(d, 7, 1)),
        control_status=unpack_raw(d, 8, 4),
        vs=unpack(d, 16, 8, signed=False, scale=1.0),
        long_accel=unpack(d, 32, 11, signed=False, scale=0.01, offset=-10.23),
        turn_right_en=bool(unpack_raw(d, 48, 1)),
        hazard_en=bool(unpack_raw(d, 49, 1)),
        turn_left_en=bool(unpack_raw(d, 50, 1)),
        gear_sel=unpack_raw(d, 52, 4),
        alive_cnt=unpack_raw(d, 56, 8),
    )


class AccDecoder(Node):
    """0x711 필터 → AccStatus 퍼블리셔 + Alive_Cnt 진단."""

    def __init__(self):
        super().__init__('acc_decoder')
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST,
        )
        self._sub = self.create_subscription(
            CanFrame, '/interface/can/read/raw', self._on_frame, qos)
        self._pub = self.create_publisher(AccStatus, '/control/status/acc', qos)
        self._diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self._alive = AliveCounter()
        self._n = 0
        self.create_timer(1.0, self._on_diag_timer)
        self.get_logger().info(f'0x{FRAME_ID:03X}(EAIT_INFO_ACC) 필터 → /control/status/acc')

    def _on_frame(self, frame: CanFrame):
        if frame.id != FRAME_ID:
            return
        if frame.dlc < 8:
            self.get_logger().warn(f'EAIT_INFO_ACC DLC 부족: {frame.dlc} (무시)')
            return
        out = decode_acc(frame.data)
        out.header = frame.header
        out.alive_ok, gap = self._alive.update(out.alive_cnt)
        if not out.alive_ok:
            self.get_logger().warn(f'EAIT_INFO_ACC Alive_Cnt {gap}건 건너뜀 (last={self._alive.last})')
        self._pub.publish(out)
        self._n += 1

    def _on_diag_timer(self):
        st = DiagnosticStatus()
        st.hardware_id = 'EAIT'
        st.name = f'can/EAIT_INFO_ACC (0x{FRAME_ID:03X})'
        skips = self._alive.pop_interval_skips()
        if self._alive.frames == 0:
            st.level, st.message = DiagnosticStatus.STALE, '수신 없음'
        elif skips:
            st.level, st.message = DiagnosticStatus.WARN, f'Alive_Cnt {skips}건 건너뜀(최근 1s)'
        else:
            st.level, st.message = DiagnosticStatus.OK, 'ok'
        st.values = [
            KeyValue(key='frames', value=str(self._alive.frames)),
            KeyValue(key='total_skips', value=str(self._alive.total_skips)),
        ]
        arr = DiagnosticArray(status=[st])
        arr.header.stamp = self.get_clock().now().to_msg()
        self._diag_pub.publish(arr)


def main():
    """Run the acc_decoder node until interrupted."""
    rclpy.init()
    node = AccDecoder()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(f'종료 — 디코드 {node._n} 건')
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
