"""기준 DBC(A1_dbc_2_fixed.dbc) 대조 — 고친 곳만 다른지, 버스 값의 뜻이 계획서 1.2절과 맞는지.

대회 배포 원본 A1_dbc_2.dbc 는 저장소에 올리지 않으므로, 원본 대조 시험은 파일이 있을 때만 돈다.
"""
import os

import cantools
import pytest

REPO = os.path.join(os.path.dirname(__file__), '..', '..', '..')
FIXED = os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc')
ORIG = os.path.join(REPO, 'DBC', 'A1_dbc_2.dbc')
DB = cantools.database.load_file(FIXED)

# (ID, 신호, 속성) — 원본과 달라도 되는 곳 전부(WP0 목록)
ALLOWED = {
    (0x100, 'steer_command', 'unit'),
    (0x200, 'steer_is_auto', 'scale'), (0x200, 'steer_is_auto', 'maximum'),
    (0x120, 'live_counter', 'maximum'),
    (0x125, 'PITZONE_LONG', 'unit'),
    (0x710, 'EPS_Alive_Cnt', 'maximum'),
    (0x711, 'ACC_Alive_Cnt', 'maximum'),
}
ATTRS = ('start', 'length', 'byte_order', 'is_signed', 'scale', 'offset', 'minimum', 'maximum', 'unit')


@pytest.mark.skipif(not os.path.exists(ORIG), reason='대회 배포 원본 없음(저장소에 안 올림)')
def test_only_listed_differences_from_original():
    orig = cantools.database.load_file(ORIG, encoding='utf-8', strict=False)
    assert sorted(m.frame_id for m in orig.messages) == sorted(m.frame_id for m in DB.messages)
    found = set()
    for m in orig.messages:
        g = DB.get_message_by_frame_id(m.frame_id)
        assert (m.name, m.length) == (g.name, g.length)
        assert [s.name for s in m.signals] == [s.name for s in g.signals]
        for s in m.signals:
            t = g.get_signal_by_name(s.name)
            found |= {(m.frame_id, s.name, a) for a in ATTRS if getattr(s, a) != getattr(t, a)}
    assert found == ALLOWED


def test_file_is_ascii_and_fixes_are_commented():
    with open(FIXED, 'rb') as f:
        f.read().decode('ascii')
    for fid, name, _ in ALLOWED:
        assert 'A1-BSW fix' in DB.get_message_by_frame_id(fid).get_signal_by_name(name).comment
    assert 'A1-BSW fix' in DB.get_message_by_frame_id(0x124).get_signal_by_name('SIG_GO').comment


@pytest.mark.parametrize('byte0, sig', [(0x80, 'SIG_GO'), (0x40, 'SIG_STOP'), (0x20, 'SIG_FINISH'),
                                        (0x10, 'SIG_SLOW_ON'), (0x08, 'SIG_SLOW_OFF')])
def test_race_signal_bits(byte0, sig):
    d = DB.decode_message(0x124, bytes([byte0]) + bytes(7), decode_choices=False)
    assert [k for k, v in d.items() if v] == [sig]


def test_units_that_changed_meaning():
    """break_postion 은 raw = %, 0x201 이름은 USER_wheel_info, 0x202 raw 300 = 30.0 km/h."""
    d = DB.decode_message(0x200, bytes([0, 0, 82, 0, 0, 0, 0, 0]), decode_choices=False)
    assert d['break_postion'] == 82
    assert DB.get_message_by_frame_id(0x201).name == 'USER_wheel_info'
    assert DB.decode_message(0x202, bytes.fromhex('2C01000000000000'))['speed_limit_info'] == pytest.approx(30.0)
    assert DB.decode_message(0x127, bytes([0x80]) + bytes(6) + bytes([7]), decode_choices=False) == {
        'RES_START': 1, 'alive_count': 7}
