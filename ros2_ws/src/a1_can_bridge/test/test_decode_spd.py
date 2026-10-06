"""
Unit-test the EAIT_INFO_SPD (0x712) parsing rule against boundary values and the DBC.

§3 V-model "상세설계 → 컴포넌트 시험" 행: 파서 단위 테스트(경계값).
"""
import os
import struct

import cantools
import pytest

from a1_can_bridge.spd_decoder import decode_spd

DBC = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc'
)


def test_zero():
    """All-zero payload decodes to all-zero speeds."""
    assert decode_spd(bytes(8)) == (0.0, 0.0, 0.0, 0.0)


def test_max_raw():
    """
    16비트 최대값(0xFFFF) 은 그대로 풀린다 — 범위 클램프는 디코더가 아니라 상위 plausibility.

    DBC 최대 511.96875 는 센서·EAIT 보드가 강제하는 범위지 디코더가 강제하는 범위가 아니다
    (고장주입 5-4 대상은 여기가 아니라 상위 plausibility 층 §7.2).
    """
    data = struct.pack('<HHHH', 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF)
    fr, fl, rr, rl = decode_spd(data)
    assert fr == pytest.approx(2047.96875)
    assert (fr, fl, rr, rl) == (fr, fr, fr, fr)


def test_distinct_wheels():
    """Each wheel field decodes independently in the documented FR/FL/RR/RL order."""
    data = struct.pack('<HHHH', 100, 200, 300, 400)  # FR, FL, RR, RL
    fr, fl, rr, rl = decode_spd(data)
    assert (fr, fl, rr, rl) == pytest.approx((3.125, 6.25, 9.375, 12.5))


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_matches_dbc_cantools():
    """직접 파싱한 결과가 cantools 의 정식 DBC 디코드와 정확히 같은지 — 파싱 규칙의 정확성."""
    db = cantools.database.load_file(DBC)
    msg = db.get_message_by_frame_id(0x712)
    for raw in (0, 663, 1000, 16383, 65535):
        data = struct.pack('<HHHH', raw, raw, raw, raw)
        ref = db.decode_message(msg.frame_id, data, decode_choices=False)
        mine = decode_spd(data)
        ref_tuple = (ref['WHEEL_SPD_FR'], ref['WHEEL_SPD_FL'],
                     ref['WHEEL_SPD_RR'], ref['WHEEL_SPD_RL'])
        assert mine == pytest.approx(ref_tuple)
