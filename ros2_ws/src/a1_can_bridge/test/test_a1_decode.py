"""Unit-test the real-vehicle USER_ decoders (0x200/0x201/0x210) against the fixed DBC."""
import csv
import os

import cantools
import pytest

from a1_can_bridge.a1_status_decoder import (decode_control_command, decode_control_info,
                                             decode_wheel_info)

REPO = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..')
DB = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc'))
DB_VENDOR = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc.dbc'), strict=False)
SAMPLES = os.path.join(REPO, 'tools', 'race_day', 'testdata', 'kf1600_20260917_samples.csv')


def _real(fid_hex):
    with open(SAMPLES) as f:
        next(f)
        return [bytes.fromhex(r['raw_hex']) for r in csv.DictReader(f)
                if r['arbitration_id_hex'] == fid_hex]


def test_control_info_real_frames():
    """Every sampled real 0x200 frame decodes identically to cantools + fixed DBC."""
    frames = _real('0x200')
    assert len(frames) >= 100
    for raw in frames:
        ref = DB.decode_message(0x200, raw, decode_choices=False)
        out = decode_control_info(raw)
        assert out.steer_position_deg == pytest.approx(ref['steer_postion'])
        assert out.brake_position == pytest.approx(ref['break_postion'])
        assert (out.steer_auto, out.brake_auto, out.acc_auto) == (
            bool(ref['steer_is_auto']), bool(ref['break_is_auto']), bool(ref['acc_is_auto']))
        assert (out.steer_live_counter, out.brake_live_counter, out.acc_live_counter) == (
            ref['steer_live_counter'], ref['break_live_counter'], ref['acc_live_counter'])


def test_wheel_info_real_frames():
    """Every sampled real 0x201 frame decodes identically (12-bit speed/rpm pairs)."""
    frames = _real('0x201')
    assert len(frames) >= 100
    for raw in frames:
        ref = DB.decode_message(0x201, raw)
        out = decode_wheel_info(raw)
        assert out.right_speed_kph == pytest.approx(ref['right_speed'])
        assert out.left_speed_kph == pytest.approx(ref['left_speed'])
        assert (out.right_rpm, out.left_rpm) == (ref['right_rpm'], ref['left_rpm'])
        assert (out.right_live_counter, out.left_live_counter) == (
            ref['right_live_counter'], ref['left_live_counter'])


def test_wheel_speed_above_8bit():
    """25.7 km/h (raw 257) must survive — the old 8-bit reading broke here."""
    raw = DB.encode_message(0x201, {
        'right_speed': 25.7, 'right_rpm': 243, 'left_speed': 25.1, 'left_rpm': 238,
        'right_live_counter': 0, 'left_live_counter': 0})
    out = decode_wheel_info(raw)
    assert out.right_speed_kph == pytest.approx(25.7)
    assert out.left_speed_kph == pytest.approx(25.1)


def test_control_command_real_frames():
    """Every sampled real 0x210 frame decodes identically, steer at 1 deg/raw."""
    frames = _real('0x210')
    assert len(frames) >= 100
    for raw in frames:
        ref = DB.decode_message(0x210, raw, decode_choices=False)
        out = decode_control_command(raw)
        assert out.steer_cmd_deg == ref['steer_command']
        assert out.brake_cmd_pct == ref['break_command']
        assert out.acc_cmd_pct == ref['acc_command']
        assert (out.steer_auto, out.brake_auto, out.acc_auto) == (
            bool(ref['steer_is_auto_command']), bool(ref['break_is_auto_command']),
            bool(ref['acc_is_auto_command']))


def test_steer_command_scale_not_vendor():
    """Vendor DBC would read 15 deg as 1.5 deg — we must read 15."""
    raw = DB.encode_message(0x210, {'steer_command': 15, 'break_command': 0, 'acc_command': 0,
                                    'steer_is_auto_command': 1, 'break_is_auto_command': 1,
                                    'acc_is_auto_command': 1})
    assert decode_control_command(raw).steer_cmd_deg == 15
    assert DB_VENDOR.decode_message(0x210, raw)['steer_command'] == pytest.approx(1.5)


def test_negative_steer_position():
    """Signed steer position decodes negative."""
    raw = DB.encode_message(0x200, {
        'steer_postion': -151.1, 'break_postion': 0, 'steer_is_auto': 1, 'break_is_auto': 0,
        'acc_is_auto': 1, 'steer_live_counter': 255, 'break_live_counter': 0,
        'acc_live_counter': 7})
    out = decode_control_info(raw)
    assert out.steer_position_deg == pytest.approx(-151.1)
    assert (out.steer_auto, out.brake_auto, out.acc_auto) == (True, False, True)
    assert out.steer_live_counter == 255
