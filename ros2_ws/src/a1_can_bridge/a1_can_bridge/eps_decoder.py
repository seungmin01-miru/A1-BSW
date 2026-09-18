#!/usr/bin/env python3
"""
Decode EAIT_INFO_EPS (0x710) into /control/status/eps + E2E alive-count 진단.

/interface/can/read/raw 를 구독해 이 ID 만 걸러 EpsStatus 로 디코드하고, Alive_Cnt 연속성을
추적해 1 Hz 로 /diagnostics(diagnostic_msgs/DiagnosticArray) 에 보고한다 — §2 카테고리 C 의
"E2E(alive_count) 체크"를 여기서 처음 적용한다.

파싱 규칙은 dbc_bits.py 의 비트 단위 추출을 쓴다(0x710 은 1~16비트 필드가 바이트 경계 없이
섞여 있어 0x712 처럼 struct 로 바로 풀 수 없음). 정확성은 test/test_eps_acc_decode.py 가
cantools 정식 DBC 디코드와 대조해 검증한다.

  ros2 run a1_can_bridge eps_decoder
"""
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_bridge.dbc_bits import unpack, unpack_raw
from a1_can_bridge.e2e import AliveCounter
from a1_can_msgs.msg import CanFrame, EpsStatus

FRAME_ID = 0x710


def decode_eps(data8):
    """Decode an 8-byte EAIT_INFO_EPS payload into an EpsStatus (header 미설정)."""
    d = data8
    return EpsStatus(
        en=bool(unpack_raw(d, 0, 1)),
        control_board_status=unpack_raw(d, 1, 3),
        user_can_err=bool(unpack_raw(d, 4, 1)),
        err=bool(unpack_raw(d, 5, 1)),
        veh_can_err=bool(unpack_raw(d, 6, 1)),
        sas_err=bool(unpack_raw(d, 7, 1)),
        control_status=unpack_raw(d, 8, 4),
        ignore_override=bool(unpack_raw(d, 12, 1)),
        override=bool(unpack_raw(d, 13, 1)),
        str_ang=unpack(d, 16, 16, signed=True, scale=0.1),
        str_drv_tq=unpack(d, 32, 12, signed=False, scale=0.01, offset=-20.48),
        str_out_tq=unpack(d, 44, 12, signed=False, scale=0.1, offset=-204.8),
        alive_cnt=unpack_raw(d, 56, 8),
    )


class EpsDecoder(Node):
    """0x710 필터 → EpsStatus 퍼블리셔 + Alive_Cnt 진단."""

    def __init__(self):
        super().__init__('eps_decoder')
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST,
        )
        self._sub = self.create_subscription(
            CanFrame, '/interface/can/read/raw', self._on_frame, qos)
        self._pub = self.create_publisher(EpsStatus, '/control/status/eps', qos)
        self._diag_pub = self.create_publisher(DiagnosticArray, '/diagnostics', 10)
        self._alive = AliveCounter()
        self._n = 0
        self.create_timer(1.0, self._on_diag_timer)
        self.get_logger().info(f'0x{FRAME_ID:03X}(EAIT_INFO_EPS) 필터 → /control/status/eps')

    def _on_frame(self, frame: CanFrame):
        if frame.id != FRAME_ID:
            return
        if frame.dlc < 8:
            self.get_logger().warn(f'EAIT_INFO_EPS DLC 부족: {frame.dlc} (무시)')
            return
        out = decode_eps(frame.data)
        out.header = frame.header
        out.alive_ok, gap = self._alive.update(out.alive_cnt)
        if not out.alive_ok:
            self.get_logger().warn(f'EAIT_INFO_EPS Alive_Cnt {gap}건 건너뜀 (last={self._alive.last})')
        self._pub.publish(out)
        self._n += 1

    def _on_diag_timer(self):
        st = DiagnosticStatus()
        st.hardware_id = 'EAIT'
        st.name = f'can/EAIT_INFO_EPS (0x{FRAME_ID:03X})'
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
    """Run the eps_decoder node until interrupted."""
    rclpy.init()
    node = EpsDecoder()
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
