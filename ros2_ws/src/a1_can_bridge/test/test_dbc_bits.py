"""
Cross-check the generic bit extractor against cantools' DBC decode for 0x710/0x711.

§3 V-model "상세설계 → 컴포넌트 시험" 행: 파서 단위 테스트. dbc_bits.unpack/unpack_raw 는
이 프로젝트의 모든 비트필드 메시지(0x710/0x711, 앞으로 확장될 메시지들)가 공유하는 코드라
여기서 한 번 제대로 검증해 두면 각 메시지 디코더는 신호표만 맞으면 된다.
"""
import os

import cantools
import pytest

from a1_can_bridge.dbc_bits import unpack

DBC = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc'
)

# 0, 전부 1, 그리고 몇 개의 서로 다른 비트 패턴 — 바이트 경계를 걸치는 필드(12비트, 11비트 등)가
# 실제로 정확히 뽑히는지 보려면 특정 바이트만 켜진 패턴들이 필요하다.
PATTERNS = [
    bytes(8),
    bytes([0xFF] * 8),
    bytes([0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]),
    bytes([0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55]),
    bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0]),
]


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
@pytest.mark.parametrize('frame_id', [0x710, 0x711, 0x712])
def test_all_signals_match_cantools(frame_id):
    db = cantools.database.load_file(DBC)
    can_msg = db.get_message_by_frame_id(frame_id)
    for data in PATTERNS:
        ref = db.decode_message(frame_id, data, decode_choices=False)
        for sig in can_msg.signals:
            mine = unpack(
                data, sig.start, sig.length,
                signed=sig.is_signed, scale=float(sig.scale), offset=float(sig.offset),
            )
            detail = (
                f'0x{frame_id:03X} {sig.name} data={data.hex()} '
                f'mine={mine} ref={ref[sig.name]}'
            )
            assert mine == pytest.approx(ref[sig.name]), detail
