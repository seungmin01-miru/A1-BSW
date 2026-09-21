"""VS(0x711) 디코더를 cantools(DBC 정식 디코드)와 대조 — test_tx_encode.py 와 같은 방식."""
import os

import cantools
import pytest

from rx_decode import decode_vs_kph

DBC = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc')


def _encode_0x711(db, vs, **overrides):
    msg = db.get_message_by_name('EAIT_INFO_ACC')
    vals = {sg.name: 0 for sg in msg.signals}
    vals['VS'] = vs
    vals.update(overrides)
    return msg.encode(vals, strict=True)


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_decode_vs_matches_dbc_encode_zero():
    db = cantools.database.load_file(DBC)
    data = _encode_0x711(db, vs=0)
    assert decode_vs_kph(data) == 0.0


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_decode_vs_matches_dbc_encode_typical():
    db = cantools.database.load_file(DBC)
    data = _encode_0x711(db, vs=42)
    ref = db.decode_message(0x711, data, decode_choices=False)
    assert decode_vs_kph(data) == pytest.approx(ref['VS'])
    assert decode_vs_kph(data) == 42.0


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_decode_vs_matches_dbc_encode_max():
    db = cantools.database.load_file(DBC)
    data = _encode_0x711(db, vs=255)
    assert decode_vs_kph(data) == 255.0


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_decode_vs_ignores_other_fields():
    """VS 이외의 필드(ACC_En_Status, Long_Accel 등)가 뭐든 VS 추출에 영향 없어야 한다."""
    db = cantools.database.load_file(DBC)
    data = _encode_0x711(db, vs=17, ACC_En_Status=1, Long_Accel=-5.0, Turn_Left_En=1)
    assert decode_vs_kph(data) == 17.0


def test_decode_vs_length_independent_of_dbc():
    """DBC 가 없어도(팀 파일 미제공 환경) 최소한 동작은 해야 한다 — 순수 비트 추출이라 의존성 없음."""
    assert decode_vs_kph(bytes(8)) == 0.0
    assert decode_vs_kph((0).to_bytes(8, 'little')) == 0.0
