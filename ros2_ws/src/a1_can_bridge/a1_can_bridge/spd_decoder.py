#!/usr/bin/env python3
"""
Decode EAIT_INFO_SPD (0x712) into /control/status/wheel.

/interface/can/read/raw 를 구독해 이 ID 만 걸러 물리값으로 풀고 WheelSpeeds 로 퍼블리시한다.
§6 걷기골격 4단계 — 첫 슬라이스가 여기서 "끝까지 됨"이 검증된다.

파싱 규칙(직접 하드코딩 — 런타임에 DBC 파일을 읽지 않는다. 상세설계 산출물을 코드로 고정,
성능·결정성 우선):
  WHEEL_SPD_FR/FL/RR/RL: 각 16비트 부호없음 리틀엔디언, start 0/16/32/48비트,
  scale 0.03125 km/h, offset 0.
  원본: DBC/EAIT_CAN(AVANTE_CN7).dbc. cantools 로 재검증하려면
  sil/vcan/eait_rx.py --msg EAIT_INFO_SPD 와 대조.

  ros2 run a1_can_bridge spd_decoder
"""
import struct

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_msgs.msg import CanFrame, WheelSpeeds

FRAME_ID = 0x712
SCALE = 0.03125


def decode_spd(data8):
    """Decode an 8-byte EAIT_INFO_SPD payload into (fr, fl, rr, rl) km/h."""
    fr, fl, rr, rl = struct.unpack_from('<HHHH', bytes(data8), 0)
    return fr * SCALE, fl * SCALE, rr * SCALE, rl * SCALE


class SpdDecoder(Node):
    """0x712 필터 → WheelSpeeds 퍼블리셔."""

    def __init__(self):
        super().__init__('spd_decoder')
        # can_raw_bridge 와 같은 QoS(BEST_EFFORT)를 요청해야 매칭된다 — RELIABLE 구독은
        # BEST_EFFORT 퍼블리셔와 DDS 상 호환되지 않아 조용히 안 붙는다(디스커버리는 되지만
        # 메시지가 안 옴) → 걷기골격에서 자주 겪는 함정.
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST,
        )
        self._sub = self.create_subscription(
            CanFrame, '/interface/can/read/raw', self._on_frame, qos)
        self._pub = self.create_publisher(WheelSpeeds, '/control/status/wheel', qos)
        self._n = 0
        self.get_logger().info(f'0x{FRAME_ID:03X}(EAIT_INFO_SPD) 필터 → /control/status/wheel')

    def _on_frame(self, frame: CanFrame):
        if frame.id != FRAME_ID:
            return
        if frame.dlc < 8:
            self.get_logger().warn(f'EAIT_INFO_SPD DLC 부족: {frame.dlc} (무시)')  # 경계값 처리
            return
        fr, fl, rr, rl = decode_spd(frame.data)
        out = WheelSpeeds()
        out.header = frame.header  # 재스탬프하지 않는다 — 종단 지연은 이 stamp(수신 시각) 기준
        out.fr, out.fl, out.rr, out.rl = fr, fl, rr, rl
        self._pub.publish(out)
        self._n += 1


def main():
    """Run the spd_decoder node until interrupted."""
    rclpy.init()
    node = SpdDecoder()
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
