"""0x210 인코더 — cantools + DBC/A1_dbc_fixed.dbc + 2026-09-17 실차 프레임(다른 팀 PC 가 실제로 보낸 0x210)과 대조."""
import csv
import os

import cantools
import pytest

from protocol import Command
from tx_encode import FRAME_ID_CONTROL_COMMAND, encode_0x210

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
DB = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc'))
DB_VENDOR = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc.dbc'), strict=False)
SAMPLES = os.path.join(REPO, 'tools', 'race_day', 'testdata', 'kf1600_20260917_samples.csv')


def _real_0x210():
    with open(SAMPLES) as f:
        next(f)
        return [bytes.fromhex(r['raw_hex']) for r in csv.DictReader(f) if r['arbitration_id_hex'] == '0x210']


def test_frame_id():
    assert FRAME_ID_CONTROL_COMMAND == 0x210 == DB.get_message_by_name('USER_control_command').frame_id


@pytest.mark.parametrize('cmd', [
    Command(),
    Command(steer_auto=True, brake_auto=True, acc_auto=True),
    Command(steer_auto=True, steer_cmd_deg=-30.0, brake_cmd_pct=60.0),
    Command(acc_auto=True, acc_cmd_pct=10.0),
    Command(steer_auto=True, brake_auto=True, acc_auto=True, steer_cmd_deg=150.0, brake_cmd_pct=100.0,
            acc_cmd_pct=100.0),
    Command(steer_cmd_deg=-150.0),
    Command(steer_cmd_deg=12.4),   # 반올림 → 12
])
def test_matches_fixed_dbc(cmd):
    d = DB.decode_message(0x210, encode_0x210(cmd), decode_choices=False)
    assert d['steer_command'] == round(cmd.steer_cmd_deg)
    assert d['break_command'] == round(cmd.brake_cmd_pct)
    assert d['acc_command'] == round(cmd.acc_cmd_pct)
    assert (d['steer_is_auto_command'], d['break_is_auto_command'], d['acc_is_auto_command']) == \
        (int(cmd.steer_auto), int(cmd.brake_auto), int(cmd.acc_auto))


def test_steer_scale_is_1_deg_not_vendor_0p1():
    data = encode_0x210(Command(steer_cmd_deg=15.0))
    assert DB.decode_message(0x210, data)['steer_command'] == 15
    assert DB_VENDOR.decode_message(0x210, data)['steer_command'] == pytest.approx(1.5)


def test_reproduces_real_frames_bit_exact():
    frames = _real_0x210()
    assert len(frames) >= 100
    for raw in frames:
        d = DB.decode_message(0x210, raw, decode_choices=False)
        cmd = Command(steer_auto=bool(d['steer_is_auto_command']), brake_auto=bool(d['break_is_auto_command']),
                      acc_auto=bool(d['acc_is_auto_command']), steer_cmd_deg=d['steer_command'],
                      brake_cmd_pct=d['break_command'], acc_cmd_pct=d['acc_command'])
        assert encode_0x210(cmd) == raw, raw.hex()


def test_out_of_range_is_clipped_not_raised():
    """메인 루프에서 예외로 죽는 것보다 안전한 값으로 자르는 쪽이 낫다(방어적 2차 클램프)."""
    d = DB.decode_message(0x210, encode_0x210(Command(steer_cmd_deg=999, brake_cmd_pct=-5, acc_cmd_pct=500)),
                          decode_choices=False)
    assert (d['steer_command'], d['break_command'], d['acc_command']) == (150, 0, 100)


def test_unused_bits_zero():
    data = encode_0x210(Command(steer_auto=True, brake_auto=True, acc_auto=True, steer_cmd_deg=-1,
                                brake_cmd_pct=100, acc_cmd_pct=100))
    assert data[3] == 0 and data[6] == 0 and data[7] == 0 and data[5] & 0xF8 == 0
