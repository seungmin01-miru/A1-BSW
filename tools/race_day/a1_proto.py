"""실차 USER_ 프로토콜(0x200 / 0x201 / 0x210) 인코더·디코더 — lift_tx.py · fake_a1_vehicle.py · 테스트 공용.

기준: `DBC/A1_dbc_fixed.dbc` (업체 DBC `A1_dbc.dbc` 의 오류 2곳을 고친 사본).
런타임에 DBC 를 읽지 않는다(can_guard 와 같은 원칙) — 정확성은 test_lift_tx.py 가 cantools + 수정본 DBC +
9/17 실제 프레임으로 대조해 보장한다. 의존성은 표준 라이브러리뿐.

⚠️ 0x210 steer_command 배율은 **1 deg/raw** 다. 업체 DBC 의 0.1 은 틀렸다(9/17 로그: 정착 구간 1,354개에서
조향 위치 / 명령 raw 비율 중앙값 1.000). 0.1 로 인코딩하면 의도한 각도의 10배로 핸들이 돈다.
"""

FRAME_CONTROL_INFO = 0x200    # 차량 → PC: 조향·브레이크 위치, 축별 auto, 축별 live_counter
FRAME_WHEEL_INFO = 0x201      # 차량 → PC: 좌우 바퀴 속도·rpm (12비트 쌍), live_counter 2개
FRAME_CONTROL_COMMAND = 0x210  # PC → 차량: 조향·브레이크·가속 명령 + 축별 auto (카운터 없음)

STEER_CMD_MIN, STEER_CMD_MAX = -150, 150   # deg (DBC 범위)
PCT_MIN, PCT_MAX = 0, 100                   # % (break_command / acc_command DBC 범위)


def encode_0x210(steer_deg, brake_pct, acc_pct, steer_auto, brake_auto, acc_auto):
    """명령 → USER_control_command 8바이트. 범위 밖이면 ValueError(조용히 자르지 않는다 — 한계는 호출자 책임).

    bit 0-15  steer_command  int16 LE, 1 deg/raw
    bit 16-31 break_command  uint16 LE, 1 %/raw
    bit 32-39 acc_command    uint8, 1 %/raw
    bit 40/41/42 steer/break/acc_is_auto_command
    """
    s = int(round(steer_deg))
    b = int(round(brake_pct))
    a = int(round(acc_pct))
    if not STEER_CMD_MIN <= s <= STEER_CMD_MAX:
        raise ValueError(f'steer {s} deg 가 DBC 범위 [{STEER_CMD_MIN}, {STEER_CMD_MAX}] 밖')
    if not PCT_MIN <= b <= PCT_MAX:
        raise ValueError(f'brake {b} % 가 [0, 100] 밖')
    if not PCT_MIN <= a <= PCT_MAX:
        raise ValueError(f'acc {a} % 가 [0, 100] 밖')
    d = bytearray(8)
    d[0:2] = s.to_bytes(2, 'little', signed=True)
    d[2:4] = b.to_bytes(2, 'little')
    d[4] = a
    d[5] = (1 if steer_auto else 0) | (2 if brake_auto else 0) | (4 if acc_auto else 0)
    return bytes(d)


def decode_0x210(data):
    d = bytes(data)
    return {
        'steer_deg': int.from_bytes(d[0:2], 'little', signed=True),
        'brake_pct': int.from_bytes(d[2:4], 'little'),
        'acc_pct': d[4],
        'steer_auto': d[5] & 1, 'brake_auto': (d[5] >> 1) & 1, 'acc_auto': (d[5] >> 2) & 1,
    }


def decode_0x200(data):
    d = bytes(data)
    return {
        'steer_pos_deg': int.from_bytes(d[0:2], 'little', signed=True) * 0.1,
        'brake_pos': int.from_bytes(d[2:4], 'little', signed=True) * 0.1,
        'steer_auto': d[4] & 1, 'brake_auto': (d[4] >> 1) & 1, 'acc_auto': (d[4] >> 2) & 1,
        'steer_cnt': d[5], 'brake_cnt': d[6], 'acc_cnt': d[7],
    }


def decode_0x201(data):
    d = bytes(data)
    w0 = int.from_bytes(d[0:3], 'little')
    w1 = int.from_bytes(d[3:6], 'little')
    return {
        'right_kph': (w0 & 0xFFF) * 0.1, 'right_rpm': w0 >> 12,
        'left_kph': (w1 & 0xFFF) * 0.1, 'left_rpm': w1 >> 12,
        'right_cnt': d[6], 'left_cnt': d[7],
    }


def encode_0x200(steer_pos_deg, brake_pos, steer_auto, brake_auto, acc_auto, steer_cnt, brake_cnt, acc_cnt):
    """가짜 차량(fake_a1_vehicle.py)·테스트용."""
    d = bytearray(8)
    d[0:2] = int(round(steer_pos_deg * 10)).to_bytes(2, 'little', signed=True)
    d[2:4] = int(round(brake_pos * 10)).to_bytes(2, 'little', signed=True)
    d[4] = (1 if steer_auto else 0) | (2 if brake_auto else 0) | (4 if acc_auto else 0)
    d[5], d[6], d[7] = steer_cnt & 0xFF, brake_cnt & 0xFF, acc_cnt & 0xFF
    return bytes(d)


def encode_0x201(right_kph, right_rpm, left_kph, left_rpm, right_cnt, left_cnt):
    """가짜 차량·테스트용."""
    def pack(kph, rpm):
        return (min(int(round(kph * 10)), 0xFFF) & 0xFFF) | ((min(int(rpm), 0xFFF) & 0xFFF) << 12)
    d = bytearray(8)
    d[0:3] = pack(right_kph, right_rpm).to_bytes(3, 'little')
    d[3:6] = pack(left_kph, left_rpm).to_bytes(3, 'little')
    d[6], d[7] = right_cnt & 0xFF, left_cnt & 0xFF
    return bytes(d)
