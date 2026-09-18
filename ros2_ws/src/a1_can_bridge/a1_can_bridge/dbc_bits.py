"""
Generic little-endian (Intel) DBC bit-field extraction.

DBC 의 "little_endian" 신호는 8바이트 페이로드 전체를 하나의 64비트 리틀엔디언 정수로 보고
[start, start+length) 비트 구간을 뽑는 것과 같다(Vector DBC 관례) — 이 프로젝트 DBC 신호는
전부 little_endian(=Intel) 이므로(EAIT_INFO_EPS/ACC/SPD 확인) 이 하나의 함수로 전부 처리한다.
0x712(EAIT_INFO_SPD) 처럼 바이트 정렬된 신호만 있는 메시지는 struct 로 더 빠르게 풀 수도 있지만,
0x710/0x711 처럼 1~16비트가 바이트 경계 없이 섞인 메시지는 이 비트 단위 추출이 정확하고 일반적이다.
정확성은 test/test_dbc_bits.py 가 cantools(DBC 정식 디코드)와 다수의 합성 값으로 대조해 검증한다.
"""


def unpack_raw(data8, start, length):
    """8바이트 페이로드에서 [start, start+length) 비트를 뽑은 원시 정수(부호 없음)."""
    raw = int.from_bytes(bytes(data8), 'little')
    return (raw >> start) & ((1 << length) - 1)


def unpack(data8, start, length, signed=False, scale=1.0, offset=0.0):
    """unpack_raw 결과에 부호 확장(2의 보수) + scale/offset 을 적용한 물리값."""
    val = unpack_raw(data8, start, length)
    if signed and val & (1 << (length - 1)):
        val -= 1 << length
    return val * scale + offset
