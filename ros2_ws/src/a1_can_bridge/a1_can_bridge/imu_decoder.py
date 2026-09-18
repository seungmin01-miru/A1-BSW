#!/usr/bin/env python3
"""
Decode EAIT_INFO_IMU (0x713) into /control/status/imu.

0x712 처럼 4 필드 전부 바이트 정렬(16비트씩, start 0/16/32/48)이라 struct 로 직접 푼다
(0x710/0x711 처럼 비트가 바이트 경계를 걸치지 않음 — dbc_bits 불필요). Alive_Cnt 가 없는
메시지(8바이트를 신호 4개가 정확히 채움) — §2 E2E(alive_count) 체크 대상이 아니다.

정확성은 test/test_imu_decode.py 가 cantools 정식 DBC 디코드와 대조해 검증한다.

  ros2 run a1_can_bridge imu_decoder
"""
import struct

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_msgs.msg import CanFrame, ImuStatus

FRAME_ID = 0x713


def decode_imu(data8):
    """Decode an 8-byte EAIT_INFO_IMU payload into an ImuStatus (header 미설정)."""
    lat_raw, long_raw, yaw_raw, brk_raw = struct.unpack_from('<hHhH', bytes(data8), 0)
    return ImuStatus(
        lat_accel=lat_raw * 0.01 - 10.23,
        long_accel=long_raw * 0.01 - 10.23,
        yaw_rate=yaw_raw * 0.01 - 40.95,
        brk_cylinder=brk_raw * 0.1,
    )


class ImuDecoder(Node):
    """0x713 필터 → ImuStatus 퍼블리셔."""

    def __init__(self):
        super().__init__('imu_decoder')
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST,
        )
        self._sub = self.create_subscription(
            CanFrame, '/interface/can/read/raw', self._on_frame, qos)
        self._pub = self.create_publisher(ImuStatus, '/control/status/imu', qos)
        self._n = 0
        self.get_logger().info(f'0x{FRAME_ID:03X}(EAIT_INFO_IMU) 필터 → /control/status/imu')

    def _on_frame(self, frame: CanFrame):
        if frame.id != FRAME_ID:
            return
        if frame.dlc < 8:
            self.get_logger().warn(f'EAIT_INFO_IMU DLC 부족: {frame.dlc} (무시)')
            return
        out = decode_imu(frame.data)
        out.header = frame.header
        self._pub.publish(out)
        self._n += 1


def main():
    """Run the imu_decoder node until interrupted."""
    rclpy.init()
    node = ImuDecoder()
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
