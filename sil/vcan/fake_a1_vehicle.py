#!/usr/bin/env python3
"""가짜 실차(USER_ 프로토콜) — vcan0 리허설용. 0x200·0x201 을 21 ms 주기(10-07 실측 21.0 ms)로 보내고,
0x210 을 받으면 auto 비트가 켜진 축만 반응한다.

  - 조향 위치: 명령을 1차 지연(시정수 0.2 s)으로 추종 — 배율은 0.1 deg/raw(업체 DBC, 10-07 실차 확정)
  - 브레이크 위치(%): 명령 % 로 추종(10-07 실측: 위치 = 명령 %)
  - 바퀴 속도: 가속 % − 브레이크 % 로 단순 적분(물리 모델 아님, 숫자가 움직이는지만 보는 용도)
  - auto 비트(0x200 byte4): 마지막으로 받은 0x210 의 auto 비트를 그대로 보고, **0x210 이 끊겨도 유지**한다
    (10-07 실차: 0x210 없이 59.9 s 동안 auto 111 유지, auto 0 프레임이 와야 해제). 다른 동작을 시험하려면
    --cmd-timeout-s 로 타임아웃을 줄 수 있다(기본 0 = 없음).

  python3 sil/vcan/fake_a1_vehicle.py --channel vcan0
  python3 sil/vcan/fake_a1_vehicle.py --channel vcan0 --physics --pose-log /tmp/pose.csv   # 02 세션 SIL(속도 제어·U턴)

--physics (2026-11-04, bsw_test 02 세션 SIL): 가속 % → 가속도(불감대 5 %, track.yaml accel_map 비슷하게),
브레이크 % → 감속도(track.yaml brake_map), 주행 저항 0.15 + 0.01·v m/s², 구동·제동 1차 지연 0.15 s, 브레이크 위치는
82 % 에서 포화. 자세(x, y, 방위)는 자전거 모델(휠베이스 2.36 m, 조향비 --steer-ratio)로 적분해 --pose-log 에 남긴다.
**물리 모델이 실차와 같다는 뜻이 아니다** — 제어 로직이 닫히고 멈추는지만 본다(실차 판정에 쓰지 않음).

경기 깃발 확장(2026-11-06, 계획서 WP7):
  - 0x202: 현재 속도 제한을 주기마다 보고. 기본 30.0 km/h(11-04 실측 2C01). 생략 때는 보고만 하고 제한을 걸지 않는다.
  - 0x203: 받으면 --speed-limit-latency 뒤 0x202 에 반영, 끊겨도 값 유지(업체 답변). --physics 에서는 그 속도를 넘지 않는다.
  - 0x127 RES_START=1 동안: 0x210 무시 + 자체 감속(--res-decel). 해제 뒤 다시 0x210 을 따른다.
  - 0x128: 주기마다 송신(11-04 버스에도 늘 있었음). RES_END = RES 중 정지 완료, RES_ERROR = --res-error.
같은 프로세스에서 쓸 때(vcan 없는 PC): FakeVehicle(can.Bus(channel='r1', interface='virtual'), physics=True).step(now)
"""
import csv
import math
import argparse
import os
import signal
import sys
import time

import can

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'race_day'))
from a1_proto import (FRAME_CONTROL_COMMAND, FRAME_CONTROL_INFO, FRAME_WHEEL_INFO,  # noqa: E402
                      decode_0x210, encode_0x200, encode_0x201)

FRAME_SERVICE_INFO = 0x202     # 차량 → PC: speed_limit_info
FRAME_SERVICE_COMMAND = 0x203  # PC → 차량: speed_limit
FRAME_RES = 0x127              # 대회 서버 → 차량: RES_START
FRAME_RES_FB = 0x128           # 차량 → 대회 서버: RES_END, RES_ERROR
LIMIT_RAW_MAX = 1000           # DBC 범위 0~100 km/h
DEFAULT_REPORTED_LIMIT_KPH = 30.0   # 11-04 실측 0x202 = 2C01 (원격 조종 제한, 자율 상한 아님 — 2026-11-06 사용자 확인)
DEFAULT_RES_DECEL_MPS2 = 0.87  # ★ 잠정: 11-04 앱 인수 15 km/h → 정지 4.85 s 를 빌려 씀(RES 실측 아님)
DEFAULT_RES_BRAKE_PCT = 82.0   # 잠정: RES 중 브레이크 보고값(포화점), 실물 미확인

_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def _interp(v, xs, ys):
    if v <= xs[0]:
        return ys[0]
    for j in range(1, len(xs)):
        if v <= xs[j]:
            return ys[j - 1] + (ys[j] - ys[j - 1]) * (v - xs[j - 1]) / (xs[j] - xs[j - 1])
    return ys[-1]


def encode_0x202(limit_raw):
    """USER_service_info: speed_limit_info 0/10 (0.1 km/h/raw)."""
    return (int(limit_raw) & 0x3FF).to_bytes(2, 'little') + bytes(6)


def decode_0x203_raw(data):
    """USER_service_command: speed_limit 0/10 → raw(0.1 km/h)."""
    return int.from_bytes(bytes(data)[0:2], 'little') & 0x3FF


def encode_0x128(res_end, res_error):
    """KIAPI_RES_FB: RES_END 7/1, RES_ERROR 0/3."""
    return bytes([(0x80 if res_end else 0) | (int(res_error) & 0x07)]) + bytes(7)


class FakeVehicle:
    """차량 한 대의 상태 + 한 주기 동작. 버스를 받으므로 socketcan·virtual 어디서나 돈다.

    tick(now): 수신을 비우고 정확히 한 주기를 진행·송신한다(main 이 벽시계 주기로 부른다).
    step(now): now 까지 밀린 주기를 모두 tick 한다(가상 시계·호출 빈도 무관). 진행한 주기 수를 돌려준다.
    force_speed_limit(kph): 차량 쪽 값이 저절로 바뀐 상황 흉내(S16 어긋남).
    읽기: kph, steer, brake(보고값), speed_limit_kph, res_active, res_end, x, y, yaw.

    speed_limit_kph: None = 0x202 에 30.0 을 보고만 하고 제한은 걸지 않는다(0x203 을 받기 전까지) — 30.0 은 11-04 실측
      값이지만 원격 조종 제한이고 자율 상한이 아니다(2026-11-06 사용자 확인). 숫자를 주면 처음부터 그 값으로 제한(S17).
    """

    def __init__(self, bus, period_s=0.021, cmd_timeout_s=0.0, physics=False, steer_ratio=7.5, pose_writer=None,
                 speed_limit_kph=None, speed_limit_latency_s=0.0, initial_kph=0.0,
                 res_decel_mps2=DEFAULT_RES_DECEL_MPS2, res_brake_pct=DEFAULT_RES_BRAKE_PCT, res_error=0,
                 res_steer_deg=None, send_service_info=True, ignore_speed_limit_cmd=False):
        self.bus = bus
        self.dt = period_s
        self.cmd_timeout_s = cmd_timeout_s
        self.physics = physics
        self.steer_ratio = steer_ratio
        self.pose_writer = pose_writer
        self.steer = self._brake_cmd = 0.0
        self.kph = float(initial_kph)
        self.a_drive = self.a_brake = 0.0
        self.x = self.y = self.yaw = 0.0
        self.cmd = None
        self.cmd_t = None
        self.cnt = 0
        self._next_k = 0
        self._t0 = None
        # 0x202/0x203
        shown = DEFAULT_REPORTED_LIMIT_KPH if speed_limit_kph is None else speed_limit_kph
        self.limit_raw = self._kph_to_raw(shown)
        self.limit_enforced = speed_limit_kph is not None
        self.limit_latency_s = speed_limit_latency_s
        self.send_service_info = send_service_info
        self.ignore_speed_limit_cmd = ignore_speed_limit_cmd
        self._limit_pending = []        # (적용 시각, raw)
        self.limit_rejected = 0         # DBC 범위(0~100 km/h) 밖 0x203 수
        # RES
        self.res_active = False
        self.res_end = False
        self.res_decel_mps2 = res_decel_mps2
        self.res_brake_pct = res_brake_pct
        self.res_error = int(res_error)
        self.res_steer_deg = res_steer_deg
        self._res_brake = 0.0           # 차량 스스로 건 브레이크(0x210 경로의 값과 따로 둔다)

    @staticmethod
    def _kph_to_raw(kph):
        raw = int(round(kph * 10))
        if not 0 <= raw <= LIMIT_RAW_MAX:
            raise ValueError(f'속도 제한 {kph} km/h 가 DBC 범위 0~100 밖')
        return raw

    @property
    def speed_limit_kph(self):
        return self.limit_raw * 0.1

    @property
    def brake(self):
        return max(self._brake_cmd, self._res_brake)

    def force_speed_limit(self, kph):
        self.limit_raw = self._kph_to_raw(kph)
        self.limit_enforced = True

    def _drain(self, now):
        while True:
            m = self.bus.recv(timeout=0)
            if m is None:
                return
            if len(m.data) != 8:
                continue
            if m.arbitration_id == FRAME_CONTROL_COMMAND:
                # RES 중에도 최신값은 받아 둔다(적용만 안 함) — 해제 순간 RES 전의 옛 명령을 따르면 안 된다
                self.cmd, self.cmd_t = decode_0x210(m.data), now
            elif m.arbitration_id == FRAME_SERVICE_COMMAND:
                raw = decode_0x203_raw(m.data)
                if raw > LIMIT_RAW_MAX:
                    self.limit_rejected += 1
                elif not self.ignore_speed_limit_cmd:
                    self._limit_pending.append((now + self.limit_latency_s, raw))
            elif m.arbitration_id == FRAME_RES:
                self.res_active = bool(m.data[0] & 0x80)   # 프레임이 끊기면 마지막 값 유지(끊김 동작 정의 없음)

    def _apply_limit(self, now):
        while self._limit_pending and self._limit_pending[0][0] <= now + 1e-9:
            self.limit_raw = self._limit_pending.pop(0)[1]
            self.limit_enforced = True

    def _cap_speed(self, v_prev, v_new):
        """--physics 제한: 제한 이하에서는 넘지 않고, 이미 넘었으면 구동만 끊겨 관성·브레이크로 내려온다.
        제한보다 빠를 때 차량이 스스로 브레이크를 거는지는 업체 확인 대기라 걸지 않는다(계획서 9.3)."""
        if not self.limit_enforced:
            return v_new
        cap = self.limit_raw * 0.1 / 3.6
        if v_prev <= cap:
            return min(v_new, cap)
        res = (0.15 + 0.01 * v_prev) if v_prev > 0 else 0.0
        return max(0.0, min(v_new, v_prev - (self.a_brake + res) * self.dt))

    def step(self, now):
        if self._t0 is None:
            self._t0 = now
        n = 0
        while self._t0 + self._next_k * self.dt <= now + 1e-9:
            self.tick(self._t0 + self._next_k * self.dt)
            self._next_k += 1
            n += 1
        return n

    def tick(self, now):
        dt = self.dt
        self._drain(now)
        self._apply_limit(now)
        cmd = self.cmd
        live = cmd is not None and (self.cmd_timeout_s <= 0 or now - self.cmd_t < self.cmd_timeout_s)
        sa = ba = aa = 0
        acc = bpct = 0
        if live:
            sa, ba, aa = cmd['steer_auto'], cmd['brake_auto'], cmd['acc_auto']
        if self.res_active:
            self._res_motion(dt)
        else:
            self._res_brake += (0.0 - self._res_brake) * min(1.0, dt / 0.2)
            if self._res_brake < 0.5:
                self._res_brake = 0.0
            self.res_end = False
            if live:
                if sa:
                    self.steer += (cmd['steer_deg'] - self.steer) * min(1.0, dt / 0.2)
                if ba:
                    self._brake_cmd += (cmd['brake_pct'] - self._brake_cmd) * min(1.0, dt / 0.2)
                acc = cmd['acc_pct'] if aa else 0
                bpct = cmd['brake_pct'] if ba else 0
                if not self.physics:
                    self.kph = max(0.0, self.kph + (acc * 0.5 - bpct * 1.0 - 0.5) * dt)
            elif not self.physics:
                self.kph = max(0.0, self.kph - 0.5 * dt)
        if self.physics:
            if not self.res_active:
                if ba and live:
                    self._brake_cmd = min(self._brake_cmd, 82.0)
                k = min(1.0, dt / 0.15)
                self.a_drive += (_interp(acc, [0, 5, 30, 100], [0, 0, 1.3, 3.0]) - self.a_drive) * k
                self.a_brake += (_interp(min(bpct, 82), [0, 40, 70, 82], [0, 1.0, 2.0, 2.8]) - self.a_brake) * k
                v_prev = v = self.kph / 3.6
                res = (0.15 + 0.01 * v) if v > 0 else 0.0
                v = max(0.0, v + (self.a_drive - self.a_brake - res) * dt)
                v = self._cap_speed(v_prev, v)
                self.kph = v * 3.6
            v = self.kph / 3.6
            tire = math.radians(self.steer / self.steer_ratio)
            self.yaw += v * math.tan(tire) / 2.36 * dt
            self.x += v * math.cos(self.yaw) * dt
            self.y += v * math.sin(self.yaw) * dt
            if self.pose_writer:
                self.pose_writer.writerow([f'{now:.4f}', f'{self.x:.3f}', f'{self.y:.3f}',
                                           f'{math.degrees(self.yaw):.2f}', f'{self.kph:.2f}',
                                           f'{self.steer:.1f}', f'{self.brake:.1f}'])
        rpm = int(self.kph * 9.5)   # 속도·rpm 비율은 미확인(10-07 은 바퀴 0) — 숫자 확인용 임의값
        cnt = self.cnt
        self._send(FRAME_CONTROL_INFO, encode_0x200(self.steer, self.brake, sa, ba, aa, cnt, cnt, cnt))
        self._send(FRAME_WHEEL_INFO, encode_0x201(self.kph, rpm, self.kph, rpm, cnt, cnt))
        if self.send_service_info:
            self._send(FRAME_SERVICE_INFO, encode_0x202(self.limit_raw))
        self._send(FRAME_RES_FB, encode_0x128(self.res_end, self.res_error if self.res_active else 0))
        self.cnt = (cnt + 1) & 0xFF

    def _res_motion(self, dt):
        """RES 중: 0x210 무시, 차량 스스로 감속. res_error ≠ 0 이면 제동 실패(감속 없음, RES_END 안 뜸)."""
        self.a_drive = self.a_brake = 0.0
        if self.res_steer_deg is not None:
            self.steer += (self.res_steer_deg - self.steer) * min(1.0, dt / 0.2)
        if self.res_error:
            return
        self._res_brake += (self.res_brake_pct - self._res_brake) * min(1.0, dt / 0.2)
        self.kph = max(0.0, self.kph - self.res_decel_mps2 * 3.6 * dt)
        self.res_end = self.kph <= 0.0

    def _send(self, arb_id, data):
        self.bus.send(can.Message(arbitration_id=arb_id, is_extended_id=False, data=data))


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--interface', default='socketcan', choices=('socketcan', 'kvaser'),
                    help='kvaser = CANlib 가상 채널로 리허설(Kvaser 설치 후, 채널 번호를 --channel 로)')
    ap.add_argument('--bitrate', type=int, default=500000)
    ap.add_argument('--period-ms', type=float, default=21.0, help='0x200/0x201 주기 (10-07 실측 21.0 ms)')
    ap.add_argument('--cmd-timeout-s', type=float, default=0.0,
                    help='0x210 이 이 시간 끊기면 auto 0 (기본 0 = 타임아웃 없음, 10-07 실차 동작)')
    ap.add_argument('--physics', action='store_true', help='변환표 기반 가감속·자세 모델(02 세션 SIL)')
    ap.add_argument('--steer-ratio', type=float, default=7.5)
    ap.add_argument('--pose-log', default='', help='--physics: 자세 CSV (t, x, y, yaw_deg, kph, steer_deg)')
    ap.add_argument('--speed-limit-kph', type=float, default=None,
                    help=f'0x202 초기값. 생략 = {DEFAULT_REPORTED_LIMIT_KPH} 보고만(11-04 실측, 자율 상한 아님), '
                         '0x203 을 받기 전까지 제한 없음. 주면 --physics 에서 처음부터 그 값으로 제한')
    ap.add_argument('--speed-limit-latency', type=float, default=0.0, help='0x203 수신 → 값 반영까지 지연 s')
    ap.add_argument('--initial-kph', type=float, default=0.0, help='시작 차속')
    ap.add_argument('--res-decel', type=float, default=DEFAULT_RES_DECEL_MPS2,
                    help='RES 중 자체 감속 m/s² (★ 잠정, 11-04 앱 인수 실측 0.87 을 빌려 씀. RES 실측 아님)')
    ap.add_argument('--res-brake-pct', type=float, default=DEFAULT_RES_BRAKE_PCT,
                    help='RES 중 0x200 브레이크 보고값 %% (잠정)')
    ap.add_argument('--res-error', type=int, default=0, help='0이 아니면 RES 제동 실패 흉내(RES_ERROR 값, 1~7)')
    ap.add_argument('--res-steer-deg', type=float, default=None, help='RES 중 핸들각 목표 (기본: 그 자리 유지)')
    return ap


def main():
    a = build_parser().parse_args()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    bus_kw = {} if a.interface == 'socketcan' else {'bitrate': a.bitrate}
    if a.interface == 'kvaser':
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'safety', 'can_guard'))
        import kvaser_compat
        kvaser_compat.apply()
    bus = can.Bus(channel=a.channel, interface=a.interface, **bus_kw)
    dt = a.period_ms / 1000.0
    pose_w = None
    if a.pose_log:
        pose_f = open(a.pose_log, 'w', newline='', buffering=1)
        pose_w = csv.writer(pose_f)
        pose_w.writerow(['mono_s', 'x_m', 'y_m', 'yaw_deg', 'kph', 'steer_deg', 'brake_pos_pct'])
    veh = FakeVehicle(bus, period_s=dt, cmd_timeout_s=a.cmd_timeout_s, physics=a.physics, steer_ratio=a.steer_ratio,
                      pose_writer=pose_w, speed_limit_kph=a.speed_limit_kph,
                      speed_limit_latency_s=a.speed_limit_latency, initial_kph=a.initial_kph,
                      res_decel_mps2=a.res_decel, res_brake_pct=a.res_brake_pct, res_error=a.res_error,
                      res_steer_deg=a.res_steer_deg)
    next_t = time.monotonic()
    print(f'[fake_a1_vehicle] {a.channel} 송신 시작(0x200/0x201 {a.period_ms:g} ms)', file=sys.stderr, flush=True)
    while not _stop:
        veh.tick(time.monotonic())
        next_t += dt
        d = next_t - time.monotonic()
        if d > 0:
            time.sleep(d)
        else:
            next_t = time.monotonic()
    bus.shutdown()
    print('[fake_a1_vehicle] 종료', file=sys.stderr)


if __name__ == '__main__':
    main()
