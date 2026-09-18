"""Unit-test the EAIT_INFO_IMU (0x713) decoder against boundary values and the DBC."""
import os
import struct

import cantools
import pytest

from a1_can_bridge.imu_decoder import decode_imu

DBC = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..', 'DBC', 'EAIT_CAN(AVANTE_CN7).dbc'
)


def test_zero():
    """All-zero raw decodes to each field's offset (raw 0 means "offset" physical value)."""
    out = decode_imu(bytes(8))
    assert out.lat_accel == pytest.approx(-10.23)
    assert out.long_accel == pytest.approx(-10.23)
    assert out.yaw_rate == pytest.approx(-40.95)
    assert out.brk_cylinder == pytest.approx(0.0)


def test_lat_accel_signed_negative():
    """LAT_ACCEL is signed on top of its offset — a negative raw value shifts it further down."""
    data = bytearray(8)
    struct.pack_into('<h', data, 0, -100)   # raw -100 × 0.01 - 10.23 = -11.23
    out = decode_imu(bytes(data))
    assert out.lat_accel == pytest.approx(-11.23)


def test_long_accel_unsigned_offset():
    """Long_ACCEL is unsigned, shifted negative by its offset (same pattern as 0x711)."""
    # raw 2046 × 0.01 - 10.23 = 10.23 (documented max)
    data = bytearray(8)
    struct.pack_into('<H', data, 2, 2046)
    out = decode_imu(bytes(data))
    assert out.long_accel == pytest.approx(10.23)


@pytest.mark.skipif(not os.path.exists(DBC), reason='팀 DBC 없음')
def test_matches_dbc_cantools():
    """모든 필드가 cantools 의 정식 DBC 디코드와 일치하는지."""
    db = cantools.database.load_file(DBC)
    for raw in (bytes(8), bytes([0xFF] * 8), bytes.fromhex('1234567890ABCDEF')):
        ref = db.decode_message(0x713, raw, decode_choices=False)
        out = decode_imu(raw)
        assert out.lat_accel == pytest.approx(ref['LAT_ACCEL'])
        assert out.long_accel == pytest.approx(ref['Long_ACCEL'])
        assert out.yaw_rate == pytest.approx(ref['YAW_RATE'])
        assert out.brk_cylinder == pytest.approx(ref['BRK_CYLINDER'])
