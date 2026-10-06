"""0x200/0x201 디코더 — cantools + DBC/A1_dbc_fixed.dbc + 2026-09-17 실차 프레임과 대조."""
import csv
import os

import cantools
import pytest

from rx_decode import (FRAME_ID_CONTROL_INFO, FRAME_ID_WHEEL_INFO, decode_control_info,
                       decode_vehicle_speed_kph, decode_wheel_speeds_kph)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
DB = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc'))
SAMPLES = os.path.join(REPO, 'tools', 'race_day', 'testdata', 'kf1600_20260917_samples.csv')


def _real(fid_hex):
    with open(SAMPLES) as f:
        next(f)
        return [bytes.fromhex(r['raw_hex']) for r in csv.DictReader(f) if r['arbitration_id_hex'] == fid_hex]


def test_ids():
    assert FRAME_ID_CONTROL_INFO == 0x200 and FRAME_ID_WHEEL_INFO == 0x201


def test_wheel_speeds_match_dbc_on_real_frames():
    frames = _real('0x201')
    assert len(frames) >= 100
    for raw in frames:
        ref = DB.decode_message(0x201, raw)
        r, l_ = decode_wheel_speeds_kph(raw)
        assert r == pytest.approx(ref['right_speed']) and l_ == pytest.approx(ref['left_speed'])
        assert decode_vehicle_speed_kph(raw) == pytest.approx((ref['right_speed'] + ref['left_speed']) / 2)


def test_wheel_speed_above_8bit_range():
    """9월 말 정정 사례: 25.7 km/h(raw 257)가 8비트 해석이면 깨진다 — 12비트로 읽혀야 한다."""
    raw = DB.encode_message(0x201, {'right_speed': 25.7, 'right_rpm': 243, 'left_speed': 25.1, 'left_rpm': 238,
                                    'right_live_counter': 1, 'left_live_counter': 2})
    assert decode_wheel_speeds_kph(raw) == (pytest.approx(25.7), pytest.approx(25.1))


def test_control_info_matches_dbc_on_real_frames():
    frames = _real('0x200')
    assert len(frames) >= 100
    for raw in frames:
        ref = DB.decode_message(0x200, raw, decode_choices=False)
        mine = decode_control_info(raw)
        assert mine['steer_pos_deg'] == pytest.approx(ref['steer_postion'])
        assert mine['brake_pos'] == pytest.approx(ref['break_postion'])
        assert (mine['steer_auto'], mine['brake_auto'], mine['acc_auto']) == \
            (ref['steer_is_auto'], ref['break_is_auto'], ref['acc_is_auto'])
