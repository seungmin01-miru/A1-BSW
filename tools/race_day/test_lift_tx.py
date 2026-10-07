"""lift_tx.py / a1_proto.py 유닛테스트 — 수정본 DBC(cantools) + 9/17 실차 프레임으로 대조.

  cd tools/race_day && python3 -m pytest -q test_lift_tx.py
"""
import csv
import os

import cantools
import pytest

import a1_proto as p
import lift_tx

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
DB_FIXED = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc'))
DB_VENDOR = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc.dbc'), strict=False)
SAMPLES = os.path.join(HERE, 'testdata', 'kf1600_20260917_samples.csv')


def _samples(fid_hex):
    with open(SAMPLES) as f:
        next(f)  # 주석 줄
        return [bytes.fromhex(r['raw_hex']) for r in csv.DictReader(f) if r['arbitration_id_hex'] == fid_hex]


# --- 0x210 인코더 -------------------------------------------------------------------------------

@pytest.mark.parametrize('steer,brake,acc,sa,ba,aa', [
    (0, 0, 0, 0, 0, 0), (1, 0, 0, 1, 1, 1), (-1, 0, 0, 1, 0, 0), (30, 60, 0, 1, 1, 0), (-30, 0, 10, 0, 1, 1),
    (150, 100, 100, 1, 1, 1), (-150, 0, 0, 1, 1, 1), (15, 20, 5, 1, 1, 1), (8.8, 0, 0, 1, 0, 0), (-3.5, 0, 0, 1, 1, 1),
])
def test_encode_0x210_matches_fixed_dbc(steer, brake, acc, sa, ba, aa):
    data = p.encode_0x210(steer, brake, acc, sa, ba, aa)
    d = DB_FIXED.decode_message(0x210, data, decode_choices=False)
    assert d['steer_command'] == pytest.approx(steer)
    assert d['break_command'] == brake
    assert d['acc_command'] == acc
    assert (d['steer_is_auto_command'], d['break_is_auto_command'], d['acc_is_auto_command']) == (sa, ba, aa)
    # 정의되지 않은 바이트(3, 6, 7)와 byte5 상위 비트는 0 — 9/17 로그 전 프레임에서 항상 0 이었다
    assert data[3] == 0 and data[6] == 0 and data[7] == 0 and data[5] & 0xF8 == 0


def test_encode_0x210_steer_scale_is_vendor_0p1():
    """업체 DBC 배율 ×0.1 이 맞다(2026-10-07 실차: raw 100 → 위치 +11.0°, 원격 raw −35 → −3.5°)."""
    data = p.encode_0x210(10, 0, 0, 1, 1, 1)
    assert data[0:2] == (100).to_bytes(2, 'little', signed=True)
    assert DB_VENDOR.decode_message(0x210, data)['steer_command'] == pytest.approx(10.0)
    assert DB_FIXED.decode_message(0x210, data)['steer_command'] == pytest.approx(10.0)
    assert p.decode_0x210(data)['steer_raw'] == 100 and p.decode_0x210(data)['steer_deg'] == 10.0


def test_encode_0x210_roundtrip_real_frames():
    """9/17 실차 0x210 프레임(다른 팀 PC 가 실제로 보낸 것)을 디코드 → 다시 인코드하면 바이트가 완전히 같다."""
    frames = _samples('0x210')
    assert len(frames) >= 100
    for raw in frames:
        d = p.decode_0x210(raw)
        again = p.encode_0x210(d['steer_deg'], d['brake_pct'], d['acc_pct'],
                               d['steer_auto'], d['brake_auto'], d['acc_auto'])
        assert again == raw, raw.hex()


@pytest.mark.parametrize('args', [(151, 0, 0, 0, 0, 0), (-151, 0, 0, 0, 0, 0), (0, 101, 0, 0, 0, 0),
                                  (0, -1, 0, 0, 0, 0), (0, 0, 101, 0, 0, 0), (0, 0, -1, 0, 0, 0)])
def test_encode_0x210_rejects_out_of_dbc_range(args):
    with pytest.raises(ValueError):
        p.encode_0x210(*args)


# --- 0x200 / 0x201 디코더 ----------------------------------------------------------------------

def test_decode_0x200_matches_fixed_dbc_on_real_frames():
    frames = _samples('0x200')
    assert len(frames) >= 100
    for raw in frames:
        mine = p.decode_0x200(raw)
        ref = DB_FIXED.decode_message(0x200, raw, decode_choices=False)
        assert mine['steer_pos_deg'] == pytest.approx(ref['steer_postion'])
        assert mine['brake_pos'] == pytest.approx(ref['break_postion'])
        assert (mine['steer_auto'], mine['brake_auto'], mine['acc_auto']) == \
            (ref['steer_is_auto'], ref['break_is_auto'], ref['acc_is_auto'])
        assert (mine['steer_cnt'], mine['brake_cnt'], mine['acc_cnt']) == \
            (ref['steer_live_counter'], ref['break_live_counter'], ref['acc_live_counter'])


def test_decode_0x201_matches_fixed_dbc_on_real_frames():
    frames = _samples('0x201')
    assert len(frames) >= 100
    for raw in frames:
        mine = p.decode_0x201(raw)
        ref = DB_FIXED.decode_message(0x201, raw)
        assert mine['right_kph'] == pytest.approx(ref['right_speed'])
        assert mine['left_kph'] == pytest.approx(ref['left_speed'])
        assert mine['right_rpm'] == ref['right_rpm'] and mine['left_rpm'] == ref['left_rpm']
        assert mine['right_cnt'] == ref['right_live_counter'] and mine['left_cnt'] == ref['left_live_counter']


def test_fake_vehicle_encoders_roundtrip():
    raw = p.encode_0x200(-12.3, 4.5, 1, 0, 1, 7, 8, 9)
    d = p.decode_0x200(raw)
    assert d['steer_pos_deg'] == pytest.approx(-12.3) and d['brake_pos'] == pytest.approx(4.5)
    assert (d['steer_auto'], d['brake_auto'], d['acc_auto'], d['steer_cnt'], d['acc_cnt']) == (1, 0, 1, 7, 9)
    raw = p.encode_0x201(25.7, 243, 3.1, 30, 1, 2)
    d = p.decode_0x201(raw)
    assert (d['right_kph'], d['right_rpm'], d['left_kph'], d['left_rpm']) == (pytest.approx(25.7), 243,
                                                                              pytest.approx(3.1), 30)


# --- lift_tx 로직 --------------------------------------------------------------------------------

IP_UP = '8: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 qdisc pfifo_fast state UP mode DEFAULT\n' \
        '    link/can  promiscuity 0\n    can state ERROR-ACTIVE (berr-counter tx 0 rx 0) restart-ms 0\n'
IP_LO = IP_UP.replace('can state', 'can <LISTEN-ONLY> state')
IP_DOWN = '8: can0: <NOARP,ECHO> mtu 16 qdisc noop state DOWN mode DEFAULT\n    can state STOPPED\n'


def test_iface_parser():
    assert lift_tx.iface_problem_from_ip_output(IP_UP, 'can0') is None
    assert 'listen-only' in lift_tx.iface_problem_from_ip_output(IP_LO, 'can0')
    assert 'DOWN' in lift_tx.iface_problem_from_ip_output(IP_DOWN, 'can0')
    assert '없습니다' in lift_tx.iface_problem_from_ip_output('', 'can0')


def _cmd():
    return lift_tx.Command({'steer': 5.0, 'brake': 5.0, 'acc': 2.0})


LIM = {'steer': (-30, 30), 'brake': (0, 60), 'acc': (0, 10)}


def _log(msg):
    pass


def test_limits_reject_not_clamp():
    c = _cmd()
    lift_tx.handle_line('steer 31', c, None, LIM, _log)
    lift_tx.handle_line('acc 11', c, None, LIM, _log)
    lift_tx.handle_line('brake 61', c, None, LIM, _log)
    (s, b, a, *_), _ = c.snapshot(0)
    assert (s, b, a) == (0, 0, 0)
    lift_tx.handle_line('steer -30', c, None, LIM, _log)
    assert c.snapshot(0)[0][0] == -30


def test_brake_and_acc_interlock():
    c = _cmd()
    lift_tx.handle_line('acc 5', c, None, LIM, _log)
    lift_tx.handle_line('brake 20', c, None, LIM, _log)      # 브레이크가 가속을 0 으로
    (s, b, a, *_), _ = c.snapshot(c.set_at['brake'])
    assert (b, a) == (20, 0)
    lift_tx.handle_line('acc 5', c, None, LIM, _log)         # 브레이크 걸린 동안 가속 거부
    assert c.snapshot(c.set_at['brake'])[0][2] == 0


def test_auto_return_to_zero():
    c = _cmd()
    lift_tx.handle_line('acc 5', c, None, LIM, _log)
    lift_tx.handle_line('steer 10', c, None, LIM, _log)
    t0 = c.set_at['acc']
    (s, b, a, *_), exp = c.snapshot(t0 + 1.0)
    assert (s, a, exp) == (10, 5, [])
    (s, b, a, *_), exp = c.snapshot(t0 + 2.5)               # 가속 2초 원위치
    assert (s, a, exp) == (10, 0, ['acc'])
    (s, b, a, *_), exp = c.snapshot(t0 + 5.5)               # 조향 5초 원위치
    assert (s, exp) == (0, ['steer'])


def test_auto_commands_and_safe():
    c = _cmd()
    lift_tx.handle_line('auto on', c, None, LIM, _log)
    assert c.snapshot(0)[0][3:] == (True, True, True)
    lift_tx.handle_line('auto brake off', c, None, LIM, _log)
    assert c.snapshot(0)[0][3:] == (True, False, True)
    lift_tx.handle_line('steer 10', c, None, LIM, _log)
    c.safe()
    assert c.snapshot(0)[0] == (0, 0, 0, False, False, False)
    assert lift_tx.handle_line('q', c, None, LIM, _log) is True


def test_line_reader_handles_multiple_lines_in_one_chunk():
    """2026-10-06 리허설 버그: 여러 줄이 한꺼번에 오면(붙여넣기) 두 번째 줄부터 갇히던 문제의 회귀 방지."""
    import os as _os
    r, w = _os.pipe()
    try:
        _os.write(w, b'auto on\nsteer 12\nacc 10\n')     # 한 번에 세 줄, 쓰기 쪽은 열어 둔 채(EOF 아님)
        rd = lift_tx.LineReader(r)
        assert [rd.readline(0.1) for _ in range(3)] == ['auto on\n', 'steer 12\n', 'acc 10\n']
        assert rd.readline(0.05) is None                 # 더 없으면 타임아웃(None)
        _os.write(w, '한글 명령\n'.encode())
        assert rd.readline(0.1) == '한글 명령\n'
        _os.close(w)
        w = None
        assert rd.readline(0.1) == ''                    # 입력 끝
    finally:
        _os.close(r)
        if w is not None:
            _os.close(w)
