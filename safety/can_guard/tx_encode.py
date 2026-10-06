"""USER_control_command(0x210) 인코더 — can_guard 가 클램프를 통과한 `Command` 를 차량으로 낼 마지막 단계.
런타임에 DBC 를 읽지 않는다(RX 디코더들과 같은 원칙) — 정확성은 test/test_tx_encode.py 가 cantools +
`DBC/A1_dbc_fixed.dbc` + 2026-09-17 실차 프레임(다른 팀 PC 가 실제로 보낸 0x210)과 비트 단위로 대조해 보장한다.

2026-10-06 실차 프로토콜로 전환(이전: EAIT 0x156/0x157 — git 기록에 남아 있음).

0x210 배치(전부 Intel/리틀엔디언, 바이트 정렬):
  bit 0-15  steer_command          int16, **1 deg/raw** ⚠️ 업체 DBC 의 0.1 은 오류(9/17 로그로 확인 —
                                    0.1 로 인코딩하면 의도한 각도의 10배로 핸들이 돈다)
  bit 16-31 break_command          uint16, 1 %/raw, 0~100
  bit 32-39 acc_command            uint8,  1 %/raw, 0~100
  bit 40/41/42 steer/break/acc_is_auto_command
  나머지(byte3·6·7, byte5 상위 5비트) 0 — 9/17 로그 전 프레임에서 항상 0

명령 메시지에 alive counter 가 **없다**(DBC 확인) — EAIT 시절의 Aliv_Cnt 소유 로직은 대상이 없어 제거했다.
차량이 송신자 생존을 수신 간격만으로 판단하는지는 업체 확인 대기(계획서 §7).
"""

FRAME_ID_CONTROL_COMMAND = 0x210

STEER_MIN, STEER_MAX = -150, 150   # deg, DBC 범위
PCT_MIN, PCT_MAX = 0, 100


def _clip_int(v, lo, hi):
    return max(lo, min(hi, int(round(v))))


def encode_0x210(cmd):
    """`protocol.Command` → 8바이트. 값은 호출 전에 이미 클램프돼 있다고 가정하지만, DBC 범위 밖이면 여기서도
    한 번 더 자른다(방어적 — 메인 루프에서 예외로 죽는 것보다 안전한 값으로 자르는 쪽이 낫다)."""
    s = _clip_int(cmd.steer_cmd_deg, STEER_MIN, STEER_MAX)
    b = _clip_int(cmd.brake_cmd_pct, PCT_MIN, PCT_MAX)
    a = _clip_int(cmd.acc_cmd_pct, PCT_MIN, PCT_MAX)
    d = bytearray(8)
    d[0:2] = s.to_bytes(2, 'little', signed=True)
    d[2:4] = b.to_bytes(2, 'little')
    d[4] = a
    d[5] = (1 if cmd.steer_auto else 0) | (2 if cmd.brake_auto else 0) | (4 if cmd.acc_auto else 0)
    return bytes(d)
