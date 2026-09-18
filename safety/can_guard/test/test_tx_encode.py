"""0x156/0x157 인코더를 cantools(DBC 정식 디코드)와 대조 — RX 디코더 때와 같은 방식(직접 파싱 vs DBC 대조)."""
import os

import cantools
import pytest

from protocol import Command
from tx_encode import encode_0x156, encode_0x157

DBC = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc')


def test_encode_0x156_length():
    assert len(encode_0x156(Command(), aliv_cnt=0)) == 8


def test_encode_0x157_length():
    assert len(encode_0x157(Command())) == 8


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_encode_0x156_matches_dbc_decode():
    db = cantools.database.load_file(DBC)
    cmd = Command(eps_en=True, eps_override_ignore=True, acc_en=True, aeb_en=True,
                  eps_speed=150, turn_signal=2, aeb_decel_value=0.42)
    data = encode_0x156(cmd, aliv_cnt=77)
    ref = db.decode_message(0x156, data, decode_choices=False)
    assert ref['EPS_En'] == 1
    assert ref['EPS_Override_Ignore'] == 1
    assert ref['ACC_En'] == 1
    assert ref['AEB_En'] == 1
    assert ref['EPS_Speed'] == 150
    assert ref['Turn_Signal'] == 2
    assert ref['AEB_decel_value'] == pytest.approx(0.42)
    assert ref['Aliv_Cnt'] == 77


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_encode_0x156_all_flags_off():
    db = cantools.database.load_file(DBC)
    data = encode_0x156(Command(), aliv_cnt=0)
    ref = db.decode_message(0x156, data, decode_choices=False)
    assert ref['EPS_En'] == 0
    assert ref['ACC_En'] == 0
    assert ref['AEB_En'] == 0
    assert ref['Turn_Signal'] == 0


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_encode_0x156_aliv_cnt_rollover_byte():
    db = cantools.database.load_file(DBC)
    for n in (0, 1, 254, 255):
        ref = db.decode_message(0x156, encode_0x156(Command(), aliv_cnt=n), decode_choices=False)
        assert ref['Aliv_Cnt'] == n


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_encode_0x157_matches_dbc_decode_positive():
    db = cantools.database.load_file(DBC)
    cmd = Command(eps_cmd=45.0, acc_cmd=0.8)
    ref = db.decode_message(0x157, encode_0x157(cmd), decode_choices=False)
    assert ref['EPS_Cmd'] == pytest.approx(45.0)
    assert ref['ACC_Cmd'] == pytest.approx(0.8)


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_encode_0x157_matches_dbc_decode_negative():
    """음수 EPS_Cmd(부호 있는 필드) 와 ACC_Cmd 의 감속 쪽(offset 으로 음수를 표현하는 부호 없는 필드) 둘 다 확인."""
    db = cantools.database.load_file(DBC)
    cmd = Command(eps_cmd=-123.4, acc_cmd=-2.5)
    ref = db.decode_message(0x157, encode_0x157(cmd), decode_choices=False)
    assert ref['EPS_Cmd'] == pytest.approx(-123.4, abs=0.1)
    assert ref['ACC_Cmd'] == pytest.approx(-2.5, abs=0.01)


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_encode_0x157_range_boundaries():
    db = cantools.database.load_file(DBC)
    cmd = Command(eps_cmd=-500.0, acc_cmd=-3.0)
    ref = db.decode_message(0x157, encode_0x157(cmd), decode_choices=False)
    assert ref['EPS_Cmd'] == pytest.approx(-500.0, abs=0.1)
    assert ref['ACC_Cmd'] == pytest.approx(-3.0, abs=0.01)

    cmd = Command(eps_cmd=500.0, acc_cmd=1.0)
    ref = db.decode_message(0x157, encode_0x157(cmd), decode_choices=False)
    assert ref['EPS_Cmd'] == pytest.approx(500.0, abs=0.1)
    assert ref['ACC_Cmd'] == pytest.approx(1.0, abs=0.01)


def test_encode_0x157_defends_out_of_range_input():
    """plausibility.clamp_range 를 거치지 않은 값이 실수로 들어와도(방어적 이중 클램프) 죽지 않고 잘린다."""
    data = encode_0x157(Command(eps_cmd=99999.0, acc_cmd=-999.0))
    assert len(data) == 8   # 예외 없이 인코딩됨(값은 tx_encode 내부에서 한 번 더 클램프됨)
