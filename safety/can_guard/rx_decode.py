"""can_guard 가 필요한 RX 신호 하나만 읽는다 — EAIT_INFO_ACC(0x711) 의 VS(차속). tx_encode.py 와 같은
원칙: 런타임에 DBC 를 읽지 않고, 이 저장소의 다른 어떤 부분(ros2_ws 의 디코더 등)에도 기대지 않는다
(§5.D "최소 의존"). STOPPED 의 "속도 0 수렴" 판정을 시간 근사 대신 실측으로 바꾸기 위한 용도 하나뿐이라
그 신호 하나만 뽑는다 — 그 외 0x711 필드(ACC_En_Status 등)는 can_guard 의 관심사가 아니다.

정확성은 test/test_rx_decode.py 가 cantools 디코드와 대조한다.
"""

FRAME_ID_INFO_ACC = 0x711


def decode_vs_kph(data):
    """8바이트 페이로드에서 VS(bit 16, 8비트, unsigned, scale 1, offset 0, 단위 km/h) 만 뽑는다.
    DBC 그대로 [0,255] 범위라 마스킹만으로 항상 유효값이 나온다 — "무효값" 심볼은 DBC 에 없다."""
    raw = int.from_bytes(bytes(data[:8]), 'little')
    return float((raw >> 16) & 0xFF)
