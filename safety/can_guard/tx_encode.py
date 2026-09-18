"""EAIT_Control_01/02(0x156/0x157) 인코더 — can_guard 가 클램프를 통과한 `Command` 를 차량으로 낼 마지막 단계.
런타임에 DBC 를 읽지 않는다(RX 디코더들과 같은 원칙, ros2_ws 의 `dbc_bits.py` 와도 의도적으로 독립 —
can_guard 는 이 저장소의 다른 어떤 부분에도 기대지 않는다, §5.D "최소 의존" 원칙).

0x156 EAIT_Control_01: 1~8비트 필드가 바이트 경계 없이 섞여 있어(EPS_En bit0, EPS_Speed bit8 …) 64비트
정수에 비트를 직접 쌓는다. 0x157 EAIT_Control_02 는 EPS_Cmd(bit0)·ACC_Cmd(bit24) 둘 다 바이트 정렬이라
`struct` 로 바로 찍는다. 정확성은 test/test_tx_encode.py 가 `cantools` 디코드와 대조한다.

⚠️ `Aliv_Cnt` 는 can_guard 가 **소유**한다(제어 노드가 CommandChannel 에 쓰는 값과 무관) — 매 0x156 전송마다
호출자가 0~255 롤오버로 넘겨준다(can_guard.py 의 메인 루프가 카운터를 들고 있다). 0x157 에는 DBC 상
Alive_Cnt 가 없다 — §5.D 에 적어 둔 미확인 사실 참고.
"""
import struct

from plausibility import ACC_CMD_MAX, ACC_CMD_MIN, EPS_CMD_MAX, EPS_CMD_MIN

FRAME_ID_CONTROL_01 = 0x156
FRAME_ID_CONTROL_02 = 0x157

_ACC_CMD_OFFSET = -10.23


def encode_0x156(cmd, aliv_cnt):
    """`protocol.Command` + 카운터(0~255) → EAIT_Control_01 8바이트. 값은 호출 전에 이미 클램프돼 있다고
    가정하되(plausibility.clamp_range), 여기서도 비트폭을 넘는 값은 마스킹으로 잘라낸다(방어적, 최후의 보루)."""
    aeb_decel_raw = round(cmd.aeb_decel_value / 0.01) & 0xFF
    raw = 0
    raw |= (1 if cmd.eps_en else 0) << 0
    raw |= (1 if cmd.eps_override_ignore else 0) << 2
    raw |= (cmd.eps_speed & 0xFF) << 8
    raw |= (1 if cmd.acc_en else 0) << 16
    raw |= (1 if cmd.aeb_en else 0) << 22
    raw |= (cmd.turn_signal & 0x7) << 40
    raw |= aeb_decel_raw << 48
    raw |= (aliv_cnt & 0xFF) << 56
    return raw.to_bytes(8, 'little')


def encode_0x157(cmd):
    """`protocol.Command` → EAIT_Control_02 8바이트. 범위를 벗어나면 여기서도 한 번 더 클램프(방어적)."""
    eps = max(EPS_CMD_MIN, min(EPS_CMD_MAX, cmd.eps_cmd))
    acc = max(ACC_CMD_MIN, min(ACC_CMD_MAX, cmd.acc_cmd))
    eps_raw = round(eps / 0.1)
    acc_raw = round((acc - _ACC_CMD_OFFSET) / 0.01) & 0xFFFF
    return struct.pack('<hxH3x', eps_raw, acc_raw)
