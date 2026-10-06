"""Unit-test the EAIT_INFO_EPS/ACC (0x710/0x711) decoders against boundary values and the DBC."""
import os
import struct

import cantools
import pytest

from a1_can_bridge.acc_decoder import decode_acc
from a1_can_bridge.eps_decoder import decode_eps

DBC = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc'
)


def test_eps_zero():
    """All-zero payload decodes to the all-off/Normal state."""
    out = decode_eps(bytes(8))
    assert out.en is False
    assert out.control_board_status == 0
    assert out.err is False
    assert out.control_status == 0
    assert out.str_ang == pytest.approx(0.0)
    assert out.alive_cnt == 0


def test_acc_zero():
    """All-zero payload decodes to the all-off/Normal state."""
    out = decode_acc(bytes(8))
    assert out.en is False
    assert out.vs == pytest.approx(0.0)
    assert out.gear_sel == 0
    assert out.alive_cnt == 0


def test_eps_str_ang_negative():
    """The signed StrAng field decodes negative when its top bit is set."""
    # StrAng 는 bit 16(byte 2)부터 16비트 — byte 0~1 은 En/board/err/control/override 플래그.
    # raw -100(=0xFF9C) × 0.1 = -10.0 deg
    data = bytearray(8)
    struct.pack_into('<h', data, 2, -100)
    out = decode_eps(bytes(data))
    assert out.str_ang == pytest.approx(-10.0)


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_eps_matches_dbc_cantools():
    """모든 필드가 cantools 의 정식 DBC 디코드와 일치하는지(EPS_Alive_Cnt 포함)."""
    db = cantools.database.load_file(DBC)
    for raw in (bytes(8), bytes([0xFF] * 8), bytes.fromhex('1234567890ABCDEF')):
        ref = db.decode_message(0x710, raw, decode_choices=False)
        out = decode_eps(raw)
        assert out.en == bool(ref['EPS_En_Status'])
        assert out.control_board_status == ref['EPS_Control_Board_Status']
        assert out.control_status == ref['EPS_Control_Status']
        assert out.str_ang == pytest.approx(ref['StrAng'])
        assert out.str_drv_tq == pytest.approx(ref['Str_Drv_Tq'])
        assert out.str_out_tq == pytest.approx(ref['Str_Out_Tq'])
        assert out.alive_cnt == ref['EPS_Alive_Cnt']


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_acc_matches_dbc_cantools():
    """모든 필드가 cantools 의 정식 DBC 디코드와 일치하는지(ACC_Alive_Cnt 포함)."""
    db = cantools.database.load_file(DBC)
    for raw in (bytes(8), bytes([0xFF] * 8), bytes.fromhex('1234567890ABCDEF')):
        ref = db.decode_message(0x711, raw, decode_choices=False)
        out = decode_acc(raw)
        assert out.en == bool(ref['ACC_En_Status'])
        assert out.vs == pytest.approx(ref['VS'])
        assert out.long_accel == pytest.approx(ref['Long_Accel'])
        assert out.gear_sel == ref['G_SEL_DISP']
        assert out.alive_cnt == ref['ACC_Alive_Cnt']
