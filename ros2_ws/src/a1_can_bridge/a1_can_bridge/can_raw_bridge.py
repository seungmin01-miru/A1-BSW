#!/usr/bin/env python3
"""
Bridge vcan0/can0 to /interface/can/read/raw (CanFrame).

§6 걷기골격 4단계, 첫 수직 슬라이스.

  ros2 run a1_can_bridge can_raw_bridge --ros-args -p channel:=vcan0
  ros2 run a1_can_bridge can_raw_bridge --ros-args -p channel:=can0 \
      -p cpu_affinity:=8 -p rt_priority:=80

디코딩은 하지 않는다(그건 a1_status_decoder 의 몫) — 이 노드는 SocketCAN → CanFrame
변환만 한다. 물리값 파싱 규칙이 바뀌어도 이 노드는 재빌드할 필요가 없도록 분리했다
(§3 상세설계 행).

2026-11-05 지연 수정(11-04 주행: 버스 약 580 프레임/s 를 504/s 밖에 못 내보내 큐가 쌓여 bag 의 수신 − 헤더
시각이 최대 52 s, measurements/2026-11-04_drive_log/README.md 2.7절):
  ① 프레임마다 부르던 rclpy.spin_once(timeout=0) 를 0.2 s 에 한 번으로 — 매번 wait set 을 만들고 지워서
     발행 루프 비용의 대부분이었다(tools/rt/bench_bridge.py, 개발 PC 30 → 1.8 µs/프레임)
  ② `ids` 파라미터(launch 기본 "0x200,0x201,0x210") → 커널 CAN 필터. 0x128·0x202·0x710~0x712·앱 0x156/0x157
     (각 100 Hz)을 파이썬·DDS·디코더까지 올리지 않는다(약 580 → 145 /s). 빈 문자열 = 전부(예전 동작).
     원본 전체는 candump/Kvaser 로그가 남긴다. 대회 PC 는 브리지·디코더·어댑터가 코어 하나(taskset -c 10)를 같이 쓴다
  ③ 5 s 마다 "발행 시각 − 헤더 stamp" 최대값을 재고 0.2 s 를 넘으면 경고 — 밀림을 현장에서 바로 본다
헤더 stamp 는 커널 수신 시각(SO_TIMESTAMP)이라 밀려도 정확하다 → 분석은 bag 시각이 아니라 헤더 stamp 를 쓴다.
"""
import time

import can
import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy

from a1_can_bridge.rt_utils import apply as apply_rt
from a1_can_bridge.rt_utils import parse_cpulist
from a1_can_msgs.msg import CanFrame


class CanRawBridge(Node):
    """SocketCAN → /interface/can/read/raw 퍼블리셔."""

    def __init__(self):
        super().__init__('can_raw_bridge')
        self.declare_parameter('channel', 'vcan0')
        self.declare_parameter('interface', 'socketcan')
        self.declare_parameter('cpu_affinity', '')  # 예: "8" 또는 "8-15" — 격리 코어 배치(A-3)
        self.declare_parameter('rt_priority', 0)     # SCHED_FIFO 우선순위(A-3/A-4). 권한 없으면
        self.declare_parameter('recv_timeout_s', 0.5)  # 경고만 남기고 계속 동작
        self.declare_parameter('ids', '')               # 발행할 CAN ID(쉼표), 빈 값 = 전부. 커널 필터로 건다
        self.declare_parameter('lag_warn_s', 0.2)       # 발행 − 헤더 stamp 가 이보다 크면 5 s 마다 경고

        channel = self.get_parameter('channel').value
        interface = self.get_parameter('interface').value
        cpu_txt = self.get_parameter('cpu_affinity').value
        rt_prio = self.get_parameter('rt_priority').value
        self._timeout = self.get_parameter('recv_timeout_s').value
        ids = parse_ids(self.get_parameter('ids').value)
        self._lag_warn = float(self.get_parameter('lag_warn_s').value)

        cpus = parse_cpulist(cpu_txt) if cpu_txt else None
        notes = apply_rt(cpus, rt_prio)
        self.get_logger().info(f"실행 환경: {', '.join(notes) if notes else '기본값'}")

        # BEST_EFFORT: 차량 상태는 계속 갱신되는 스트림이라 재전송보다 최신값 유지가 우선
        # (§3 콜백그룹/QoS 설계 행). can_guard(Phase D)는 이 토픽을 구독하지 않는다 —
        # 안전 TX 경로는 raw SocketCAN 만 쓴다(§7.2, DDS 미개입).
        qos = QoSProfile(
            depth=100, reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST)
        self._pub = self.create_publisher(CanFrame, '/interface/can/read/raw', qos)

        try:
            filters = [{'can_id': i, 'can_mask': 0x7FF, 'extended': False} for i in ids] if ids else None
            self._bus = can.Bus(channel=channel, interface=interface, can_filters=filters)
        except Exception as e:
            self.get_logger().fatal(f'CAN 버스 열기 실패 channel={channel} interface={interface}: {e}')
            raise
        self.get_logger().info(f'연결: {interface}:{channel} → /interface/can/read/raw, '
                               f'ID {"전부" if not ids else ",".join(f"0x{i:03X}" for i in ids)}')
        self._n = 0
        self._lag_max = 0.0

    def spin_recv(self):
        """
        Drive the node off bus.recv() instead of rclpy.spin().

        구독이 없어 콜백 실행기가 불필요 — sil/vcan 도구들과 같은 폴링 방식. §7.3 P-6(버스오프)
        에서도 노드가 죽지 않고 재시도하도록 예외를 삼킨다.
        """
        next_spin = next_report = time.monotonic()
        while rclpy.ok():
            msg = None
            try:
                msg = self._bus.recv(timeout=self._timeout)
            except can.CanError as e:
                self.get_logger().warn(f'버스 오류(재시도): {e}')
            now = time.monotonic()
            if now >= next_spin:   # 파라미터·rosout 등 대기 콜백 처리 — 프레임마다 하면 이게 병목(11-04)
                rclpy.spin_once(self, timeout_sec=0.0)
                next_spin = now + 0.2
            if now >= next_report:
                if self._lag_max > self._lag_warn:
                    self.get_logger().warn(f'발행 지연 최대 {self._lag_max:.3f} s (> {self._lag_warn} s) — '
                                           f'버스를 못 따라감, ids 필터를 좁히거나 bag 분석은 헤더 stamp 로')
                self._lag_max = 0.0
                next_report = now + 5.0
            if msg is None:
                continue
            self.publish_frame(msg)

    def publish_frame(self, msg):
        """can.Message 하나를 CanFrame 으로 발행."""
        if msg.timestamp:
            self._lag_max = max(self._lag_max, time.time() - msg.timestamp)
        out = CanFrame()
        out.header.stamp = self._stamp_from(msg.timestamp)
        out.header.frame_id = 'can'
        out.id = msg.arbitration_id
        out.dlc = msg.dlc if msg.dlc is not None else len(msg.data)
        data8 = bytes(msg.data) + b'\x00' * 8
        out.data = list(data8[:8])
        out.is_extended = bool(msg.is_extended_id)
        out.is_error = bool(msg.is_error_frame)
        self._pub.publish(out)
        self._n += 1

    @staticmethod
    def _stamp_from(can_ts):
        """
        Convert python-can's SO_TIMESTAMP float (CLOCK_REALTIME) into a ROS Time.

        종단 지연(§3 통합 시험) 측정의 기준 시각이 된다.
        """
        from builtin_interfaces.msg import Time
        t = Time()
        t.sec = int(can_ts)
        t.nanosec = int(round((can_ts - int(can_ts)) * 1e9))
        return t


def parse_ids(txt):
    """"0x200, 0x201,513" → [0x200, 0x201, 0x201]. 빈 값 → [] (필터 없음)."""
    return [int(x, 0) for x in str(txt).replace(' ', '').split(',') if x]


def main():
    """Run the can_raw_bridge node until interrupted."""
    rclpy.init()
    node = CanRawBridge()
    try:
        node.spin_recv()
    except KeyboardInterrupt:
        pass
    finally:
        node.get_logger().info(f'종료 — 수신 {node._n} 프레임')
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
