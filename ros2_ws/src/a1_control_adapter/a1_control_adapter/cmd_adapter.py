#!/usr/bin/env python3
"""
제어기 출력(/control/target, a1_can_msgs/ControllerOutput) → can_guard 공유메모리 어댑터.

  ros2 run a1_control_adapter cmd_adapter --ros-args --params-file config/track.yaml
  ros2 service call /control/adapter/arm_steer std_srvs/srv/SetBool "{data: true}"
  ros2 service call /control/adapter/arm_long  std_srvs/srv/SetBool "{data: true}"
  ros2 service call /control/adapter/disarm    std_srvs/srv/Trigger

제어기는 목표 가감속(m/s²)과 타이어각(°)만 낸다. 이 노드가 conversion.py 표로 핸들각·브레이크 %·가속 % 로 바꿔
50 Hz 로 can_guard 의 CommandChannel(safety/can_guard/protocol.py, 이 저장소 코드를 그대로 import)에 쓴다.
차량으로 0x210 을 보내는 건 여전히 can_guard 하나뿐 — 이 노드는 CAN 을 열지 않는다.

동작 규칙(안전 쪽으로):
  - 시작 = disarm. disarm 동안은 auto 0·명령 0 을 계속 쓴다(차량은 무시 = 사람/원격이 운전, shadow 모드).
    변환 결과는 /control/adapter/status 로만 나간다.
  - arm 은 축별(조향 / 종방향=브레이크+가속). 신선한 제어기 입력이 있을 때만 받아준다.
  - arm 중 입력이 stale_timeout_s(기본 150 ms) 넘게 끊기거나 NaN·범위 밖 값이 오면 **고장 래치**: 공유메모리 기록과
    하트비트를 멈춘다 → can_guard 가 50 ms 뒤 HOLDING(조향 고정·가속 0·브레이크 램프) → 2 s 뒤 STOPPED.
    입력이 돌아와도 자동 복귀하지 않는다. 운영자가 disarm 을 불러야 풀린다(STOPPED 였다면 can_guard 재시작 필요).
  - 인지 하트비트: 인지 프로세스가 아직 없으므로(can_guard README) 이 노드가 대신 보낸다 — disarm 중엔 항상,
    arm 중엔 입력이 신선할 때만. perception_heartbeat:=false 로 끌 수 있다(진짜 인지 프로세스가 생기면).
"""
import os
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_srvs.srv import SetBool, Trigger

from a1_can_msgs.msg import AdapterStatus, ControlInfo, ControllerOutput
from a1_control_adapter.conversion import InputLimits, LongMap, SlewLimiter, SteerMap, validate


def _import_protocol(can_guard_dir):
    """safety/can_guard/protocol.py 를 그대로 쓴다(구조체 정의를 복사하면 두 쪽이 어긋날 위험)."""
    d = os.path.expanduser(can_guard_dir)
    if not os.path.isfile(os.path.join(d, 'protocol.py')):
        raise RuntimeError(f'can_guard_dir 에 protocol.py 없음: {d}')
    if d not in sys.path:
        sys.path.insert(0, d)
    import protocol
    return protocol


class CmdAdapter(Node):
    """/control/target → can_guard CommandChannel."""

    def __init__(self):
        super().__init__('a1_cmd_adapter')
        p = self.declare_parameter
        default_cg = os.path.join(os.environ.get('A1_BSW_ROOT', '~/git/A1-BSW'), 'safety', 'can_guard')
        self.proto = _import_protocol(p('can_guard_dir', default_cg).value)
        self.cmd_shm = p('cmd_shm', self.proto.SHM_NAME_DEFAULT).value
        self.hb_shm = p('hb_shm', self.proto.HEARTBEAT_SHM_NAME_DEFAULT).value
        self.period_s = p('period_s', 0.02).value
        self.stale_s = p('stale_timeout_s', 0.15).value
        self.hb_enabled = p('perception_heartbeat', True).value
        self.steer = SteerMap(
            list(p('steer_map.tire_deg', [-20.0, 0.0, 20.0]).value),
            list(p('steer_map.wheel_deg', [-150.0, 0.0, 150.0]).value),
            sign=float(p('steer_sign', 1.0).value),
            wheel_limit_deg=p('steer_wheel_limit_deg', 150.0).value)
        self.long = LongMap(
            list(p('accel_map.accel_mps2', [0.1, 1.3]).value),
            list(p('accel_map.acc_pct', [0.0, 30.0]).value),
            list(p('brake_map.decel_mps2', [0.1, 4.0]).value),
            list(p('brake_map.brake_pct', [0.0, 100.0]).value),
            deadband_mps2=p('long_deadband_mps2', 0.1).value,
            acc_max_pct=p('acc_max_pct', 30.0).value,
            brake_max_pct=p('brake_max_pct', 100.0).value)
        self.lim = InputLimits(p('input_accel_abs_mps2', 15.0).value, p('input_steer_tire_abs_deg', 45.0).value)
        self.slew = SlewLimiter(p('steer_rate_limit_deg_s', 120.0).value)

        self.cmd_ch = self.hb_ch = None
        self.cmd_ino = None
        self.last_open_try = -1e9
        self.last = None            # (accel, steer_tire) 마지막 유효 입력
        self.last_rx = None         # 그 수신 시각(monotonic)
        self.steer_armed = self.long_armed = False
        self.fault = ''             # 비어 있지 않으면 고장 래치
        self.rejected = 0
        self.note = ''
        self.meas_wheel_deg = None  # 0x200 실측 핸들각 — arm 순간 속도 제한 시작점
        self.meas_rx = None
        self.last_tick = None

        # BEST_EFFORT 구독은 RELIABLE·BEST_EFFORT 발행 둘 다 받는다(반대는 QoS 불일치로 조용히 안 붙음) —
        # 제어기 쪽 QoS 를 강제하지 않으려고. 상태 브리지(a1_status_decoder)도 BEST_EFFORT 로 낸다.
        qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(ControllerOutput, '/control/target', self.on_target, qos)
        self.create_subscription(ControlInfo, '/control/status/control_info', self.on_info, qos)
        self.pub = self.create_publisher(AdapterStatus, '/control/adapter/status', 10)
        self.create_service(SetBool, '/control/adapter/arm_steer', lambda q, r: self.on_arm('steer', q, r))
        self.create_service(SetBool, '/control/adapter/arm_long', lambda q, r: self.on_arm('long', q, r))
        self.create_service(Trigger, '/control/adapter/disarm', self.on_disarm)
        self.create_timer(self.period_s, self.tick)
        self.get_logger().info(
            f'시작(disarm). shm={self.cmd_shm}, 주기 {self.period_s * 1000:.0f} ms, stale {self.stale_s * 1000:.0f} ms, '
            f'조향 표 {self.steer.tire_deg}→{self.steer.wheel_deg} sign {self.steer.sign:g}, '
            f'가속 상한 {self.long.acc_max_pct:g} %, 하트비트 {"대신 보냄" if self.hb_enabled else "끔"}')

    # ── 입력 ───────────────────────────────────────────────
    def on_target(self, msg):
        why = validate(msg.accel_mps2, msg.steer_tire_deg, self.lim)
        if why:
            self.rejected += 1
            self.last = self.last_rx = None   # 마지막 유효값도 버린다 — 고장 난 제어기의 이전 값을 믿지 않는다
            self._set_note(why)
            if self.steer_armed or self.long_armed:
                self._latch(f'입력 거부: {why}')
            return
        self.last = (msg.accel_mps2, msg.steer_tire_deg)
        self.last_rx = time.monotonic()

    def on_info(self, msg):
        self.meas_wheel_deg = msg.steer_position_deg
        self.meas_rx = time.monotonic()

    def _age(self, now):
        return None if self.last_rx is None else now - self.last_rx

    def _fresh(self, now):
        age = self._age(now)
        return age is not None and age <= self.stale_s

    # ── 서비스 ─────────────────────────────────────────────
    def on_arm(self, axis, req, res):
        if not req.data:
            setattr(self, f'{axis}_armed', False)
            res.success, res.message = True, f'{axis} disarm'
            self.get_logger().warn(res.message)
            return res
        if self.fault:
            res.success, res.message = False, f'고장 래치 중({self.fault}) — disarm 으로 먼저 해제'
        elif self.cmd_ch is None:
            res.success, res.message = False, 'can_guard 공유메모리 없음 — can_guard 를 먼저 띄울 것'
        elif not self._fresh(time.monotonic()):
            res.success, res.message = False, '제어기 입력이 없거나 오래됨 — /control/target 확인'
        elif axis == 'steer' and (self.meas_rx is None or time.monotonic() - self.meas_rx > 0.5):
            # 현재 핸들 위치를 모르면 속도 제한의 출발점이 없다(목표로 바로 튐) — 상태 브리지가 떠 있어야 arm
            res.success, res.message = False, '0x200 조향 위치 없음 — a1_status_decoder(브리지) 확인'
        else:
            if axis == 'steer' and not self.steer_armed:
                self.slew.reset(self.meas_wheel_deg)   # 지금 핸들 위치에서 목표까지 속도 제한으로 이동
            setattr(self, f'{axis}_armed', True)
            res.success, res.message = True, f'{axis} arm'
        if res.success:   # rclpy 는 같은 호출 위치에서 심각도를 바꾸면 ValueError — 호출을 나눠 둔다
            self.get_logger().warn(res.message)
        else:
            self.get_logger().error(res.message)
        return res

    def on_disarm(self, req, res):
        was = self.fault
        self.steer_armed = self.long_armed = False
        self.fault = ''
        res.success = True
        res.message = 'disarm' + (f' (고장 래치 해제: {was} — can_guard 가 STOPPED 면 재시작 필요)' if was else '')
        self.get_logger().warn(res.message)
        return res

    # ── 주기 ───────────────────────────────────────────────
    def _latch(self, why):
        if not self.fault:
            self.fault = why
            self.get_logger().error(f'고장 래치 — 기록 중단, can_guard HOLDING 으로: {why}')
        self._set_note(why)

    def _set_note(self, why):
        if why != self.note:
            self.get_logger().warn(why)
        self.note = why

    def _shm_inode(self):
        try:
            return os.stat(f'/dev/shm/{self.cmd_shm}').st_ino
        except FileNotFoundError:
            return None

    def _ensure_open(self, now):
        """1초에 한 번만 확인. can_guard 가 재시작하면 세그먼트를 새로 만든다(이전 것은 unlink) — 열린 핸들은 옛
        메모리에 계속 써지고 에러도 안 나므로 inode 를 비교해 바뀌었으면 다시 연다."""
        if now - self.last_open_try < 1.0:
            return self.cmd_ch is not None
        self.last_open_try = now
        if self.cmd_ch is not None:
            if self._shm_inode() == self.cmd_ino:
                return True
            self._set_note('can_guard 공유메모리가 바뀜(재시작?) — 다시 연결')
            self._close()
        try:
            self.cmd_ino = self._shm_inode()
            self.cmd_ch = self.proto.CommandChannel.open(self.cmd_shm)
            self.hb_ch = self.proto.HeartbeatChannel.open(self.hb_shm) if self.hb_enabled else None
        except FileNotFoundError:
            self._close()
            self._set_note('can_guard 공유메모리 없음 — can_guard 대기 중')
            return False
        self.get_logger().info(f'can_guard 공유메모리 연결: {self.cmd_shm}')
        self.note = ''
        return True

    def tick(self):
        now = time.monotonic()
        dt = 0.0 if self.last_tick is None else now - self.last_tick
        self.last_tick = now
        armed = self.steer_armed or self.long_armed
        fresh = self._fresh(now)
        if armed and not fresh and not self.fault:
            age = self._age(now)
            self._latch('제어기 입력 끊김' + (f' ({age * 1000:.0f} ms)' if age is not None else ''))

        accel, tire = self.last if self.last is not None else (0.0, 0.0)
        wheel_target = self.steer.to_wheel(tire)
        acc, brake = self.long.to_pedals(accel)
        if self.steer_armed:
            wheel = self.slew.step(wheel_target, dt)
        else:
            self.slew.reset(None)
            wheel = wheel_target       # disarm 중에는 표시용으로 속도 제한 없이

        writing = False
        if not self.fault and self._ensure_open(now):
            cmd = self.proto.Command(
                steer_auto=self.steer_armed, brake_auto=self.long_armed, acc_auto=self.long_armed,
                steer_cmd_deg=wheel if self.steer_armed else 0.0,
                brake_cmd_pct=brake if self.long_armed else 0.0,
                acc_cmd_pct=acc if self.long_armed else 0.0)
            try:
                self.cmd_ch.write(cmd)
                writing = True
                if self.hb_ch is not None and (not armed or fresh):
                    self.hb_ch.beat()
            except (ValueError, OSError) as e:   # can_guard 재시작으로 세그먼트가 바뀌면 다시 연다
                self._set_note(f'공유메모리 쓰기 실패({e}) — 다시 연결')
                self._close()

        st = AdapterStatus()
        st.header.stamp = self.get_clock().now().to_msg()
        st.steer_armed, st.long_armed, st.writing = self.steer_armed, self.long_armed, writing
        age = self._age(now)
        st.input_age_s = -1.0 if age is None else age
        st.in_accel_mps2, st.in_steer_tire_deg = float(accel), float(tire)
        st.out_steer_wheel_deg, st.out_brake_pct, st.out_acc_pct = float(wheel), float(brake), float(acc)
        st.rejected_count = self.rejected
        st.note = self.fault or self.note
        self.pub.publish(st)

    def _close(self):
        for ch in (self.cmd_ch, self.hb_ch):
            if ch is not None:
                try:
                    ch.close()
                except Exception:
                    pass
        self.cmd_ch = self.hb_ch = None

    def destroy_node(self):
        self._close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CmdAdapter()
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
