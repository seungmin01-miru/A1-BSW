"""can_guard 가 읽는 RX 신호 — tx_encode.py 와 같은 원칙: 런타임에 DBC 를 읽지 않고 다른 디렉터리에 기대지
않는다(§5.D "최소 의존"). 정확성은 test/test_rx_decode.py 가 cantools + `DBC/A1_dbc_2_fixed.dbc` + 10-07 실차
프레임과 대조한다.

2026-10-06 실차 프로토콜로 전환(이전: EAIT 0x711 VS).
  - 0x201 USER_wheel_info → 차속(좌우 바퀴 속도 평균) — STOPPED 차속 조기 판정용(이전 VS 의 대체)
  - 0x200 USER_control_info     → 조향·브레이크 위치, 축별 auto — 판단에는 안 쓰고 로그·상태 표시용
  - 0x210 이 **수신**되면 그건 다른 송신자다(우리 소켓은 자기 송신을 받지 않음) — can_guard 가 경고한다
"""

FRAME_ID_CONTROL_INFO = 0x200
FRAME_ID_WHEEL_INFO = 0x201
FRAME_ID_CONTROL_COMMAND = 0x210


def decode_wheel_speeds_kph(data):
    """0x201 → (오른쪽, 왼쪽) km/h. 각 바퀴는 3바이트에 12비트 속도(×0.1 km/h) + 12비트 rpm."""
    d = bytes(data[:8])
    w0 = int.from_bytes(d[0:3], 'little')
    w1 = int.from_bytes(d[3:6], 'little')
    return (w0 & 0xFFF) * 0.1, (w1 & 0xFFF) * 0.1


def decode_vehicle_speed_kph(data):
    """0x201 → 좌우 평균 km/h(차속 대용 — 이 차는 별도 차속 신호가 없다)."""
    r, l_ = decode_wheel_speeds_kph(data)
    return (r + l_) / 2.0


def decode_control_info(data):
    """0x200 → dict(조향 위치 deg, 브레이크 위치 %, 축별 auto). 브레이크 위치는 raw = % (A1_dbc_2, D13). ROS 쪽 0~10 표기와 섞지 않는다."""
    d = bytes(data[:8])
    return {
        'steer_pos_deg': int.from_bytes(d[0:2], 'little', signed=True) * 0.1,
        'brake_pos_pct': int.from_bytes(d[2:4], 'little', signed=True),
        'steer_auto': d[4] & 1, 'brake_auto': (d[4] >> 1) & 1, 'acc_auto': (d[4] >> 2) & 1,
    }


# --- 원격(핸드폰 앱) 0x156/0x157 — 비상 정지 원천 (2026-11-04) -------------------------------------------------------
# 배치는 tools/signal_audit/dbc_lite.remote_messages() 와 같다(10-07 녹화로 확인, A1 DBC 에는 없음).
# 10-07 녹화(candump-2026-10-07_102708.log): 평소 0x156 En 000 + 0x157 ACC_Cmd 0.00, 앱 조작 시 En=1·ACC_Cmd 음수.
FRAME_ID_REMOTE_01 = 0x156
FRAME_ID_REMOTE_02 = 0x157


def decode_remote_156(data):
    d = bytes(data[:8])
    return {'eps_en': d[0] & 1, 'acc_en': d[2] & 1, 'aeb_en': (d[2] >> 6) & 1, 'alive': d[7]}


def decode_remote_157(data):
    d = bytes(data[:8])
    return {'eps_cmd_deg': int.from_bytes(d[0:2], 'little', signed=True) * 0.1,
            'acc_cmd_mps2': round(int.from_bytes(d[3:5], 'little') * 0.01 - 10.23, 2)}


class RemoteEstopDetector:
    """원격 프레임을 먹여서 "지금 정지 요청인가"를 낸다. 순수 로직(시각은 호출자가 넘김) — 테스트 가능.

    정지 조건(잠정, 앱의 비상 정지 버튼이 실제로 무엇을 보내는지 현장 확인 후 확정):
      0x156 EPS_En / ACC_En / AEB_En 중 하나라도 1   (앱이 제어권을 가져감 = 사람이 개입)
      또는 0x157 ACC_Cmd ≤ acc_threshold_mps2         (앱이 감속을 요구)
    `confirm_frames` 프레임 연속이어야 인정(단발 비트 오류로 급정지하지 않게, 10 ms 주기면 2 프레임 ≈ 10~20 ms).
    `timeout_s` > 0 이면 데드맨: 한 번이라도 원격 프레임을 본 뒤 그만큼 끊기면 정지 요청으로 본다(기본 끔)."""

    def __init__(self, acc_threshold_mps2=-1.0, confirm_frames=2, timeout_s=0.0):
        self.acc_threshold_mps2 = acc_threshold_mps2
        self.confirm_frames = max(1, confirm_frames)
        self.timeout_s = timeout_s
        self.last_156 = None
        self.last_157 = None
        self.last_seen_t = None
        self._run = 0
        self._run_start_t = None
        self.condition = False
        self.trigger_t = None       # 인정된 연속 구간의 첫 프레임 시각(감지 지연 계산용)
        self.reason = ''
        self.lost = False

    def _cond(self):
        reasons = []
        if self.last_156 and (self.last_156['eps_en'] or self.last_156['acc_en'] or self.last_156['aeb_en']):
            reasons.append('remote_en')
        if self.last_157 and self.last_157['acc_cmd_mps2'] <= self.acc_threshold_mps2:
            reasons.append(f'acc_cmd {self.last_157["acc_cmd_mps2"]:+.2f}')
        return reasons

    def feed(self, arbitration_id, data, t):
        if arbitration_id == FRAME_ID_REMOTE_01:
            self.last_156 = decode_remote_156(data)
        elif arbitration_id == FRAME_ID_REMOTE_02:
            self.last_157 = decode_remote_157(data)
        else:
            return
        self.last_seen_t = t
        self.lost = False
        reasons = self._cond()
        if reasons:
            if self._run == 0:
                self._run_start_t = t
            self._run += 1
            if self._run >= self.confirm_frames and not self.condition:
                self.condition = True
                self.trigger_t = self._run_start_t
                self.reason = ', '.join(reasons)
        else:
            self._run = 0
            self.condition = False

    def update(self, now):
        """데드맨 시간 초과 판정. 매 주기 호출."""
        if self.timeout_s > 0 and self.last_seen_t is not None and now - self.last_seen_t > self.timeout_s:
            if not self.lost:
                self.lost = True
                self.trigger_t = now
                self.reason = f'remote lost > {self.timeout_s:g}s'

    @property
    def remote_enabled(self):
        """앱이 지금 제어권을 쥐고 있나(0x156 EPS/ACC/AEB_En 중 하나라도 1) — 이때 차량은 0x210 을 무시한다(11-04)."""
        f = self.last_156
        return bool(f and (f['eps_en'] or f['acc_en'] or f['aeb_en'])) and not self.lost

    @property
    def asserted(self):
        return self.condition or self.lost

    @property
    def seen(self):
        return self.last_seen_t is not None
