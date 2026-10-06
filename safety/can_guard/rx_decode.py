"""can_guard 가 읽는 RX 신호 — tx_encode.py 와 같은 원칙: 런타임에 DBC 를 읽지 않고 다른 디렉터리에 기대지
않는다(§5.D "최소 의존"). 정확성은 test/test_rx_decode.py 가 cantools + `DBC/A1_dbc_fixed.dbc` + 9/17 실차
프레임과 대조한다.

2026-10-06 실차 프로토콜로 전환(이전: EAIT 0x711 VS).
  - 0x201 USER_right_wheel_info → 차속(좌우 바퀴 속도 평균) — STOPPED 차속 조기 판정용(이전 VS 의 대체)
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
    """0x200 → dict(조향 위치 deg, 브레이크 위치, 축별 auto). 브레이크 위치 단위는 DBC 에 없음(9/17: 0~10)."""
    d = bytes(data[:8])
    return {
        'steer_pos_deg': int.from_bytes(d[0:2], 'little', signed=True) * 0.1,
        'brake_pos': int.from_bytes(d[2:4], 'little', signed=True) * 0.1,
        'steer_auto': d[4] & 1, 'brake_auto': (d[4] >> 1) & 1, 'acc_auto': (d[4] >> 2) & 1,
    }
