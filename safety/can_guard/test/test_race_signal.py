"""race_signal — 1.3절 byte0 예시, cantools + A1_dbc_2_fixed.dbc 대조, 2프레임 확인·seen 래치, RES 1프레임 즉시, alive 경계."""
import math
import os
import random

import cantools
import pytest

import race_signal as rs

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
DB = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc'))

BYTE0 = {'go': 0x80, 'stop': 0x40, 'finish': 0x20, 'slow_on': 0x10, 'slow_off': 0x08}
SIGNAL = {'go': 'SIG_GO', 'stop': 'SIG_STOP', 'finish': 'SIG_FINISH', 'slow_on': 'SIG_SLOW_ON',
          'slow_off': 'SIG_SLOW_OFF'}
DT = 0.02


def f124(*flags):
    b0 = 0
    for name in flags:
        b0 |= BYTE0[name]
    return bytes([b0]) + bytes(7)


def feed_n(dec, data, n, t0=0.0):
    t = t0
    for _ in range(n):
        dec.feed(data, t)
        t += DT
    return t


def res_frame(start=0, alive=0):
    return bytes([0x80 if start else 0]) + bytes(6) + bytes([alive])


# --- 디코더 ↔ DBC -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('const,msg', [
    (rs.FRAME_ID_RACE_SIGNAL, 'KIAPI_Race_Signal'), (rs.FRAME_ID_PIT_ZONE, 'KIAPI_PIT_ZONE'),
    (rs.FRAME_ID_REPAIR, 'KIAPI_REPAIR'), (rs.FRAME_ID_RES, 'KIAPI_RES'),
    (rs.FRAME_ID_RES_FB, 'KIAPI_RES_FB'), (rs.FRAME_ID_SERVICE_INFO, 'USER_service_info'),
])
def test_frame_ids_match_dbc(const, msg):
    assert DB.get_message_by_name(msg).frame_id == const


@pytest.mark.parametrize('flag,byte0', list(BYTE0.items()))
def test_race_signal_byte0_examples_section_1_3(flag, byte0):
    d = rs.decode_race_signal(bytes([byte0]) + bytes(7))
    assert d[flag] == 1 and sum(d.values()) == 1


def test_race_signal_all_byte0_values_match_dbc():
    for b0 in range(256):
        data = bytes([b0]) + bytes(7)
        d = DB.decode_message(0x124, data, decode_choices=False)
        got = rs.decode_race_signal(data)
        assert {k: got[k] for k in SIGNAL} == {k: d[v] for k, v in SIGNAL.items()}


def test_other_decoders_match_dbc_random():
    rng = random.Random(1)
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(8))
        d = DB.decode_message(0x125, data, decode_choices=False)
        lat, lon = rs.decode_pit_zone(data)
        assert (lat, lon) == pytest.approx((d['PITZONE_LAT'], d['PITZONE_LONG']))
        assert rs.decode_repair(data) == DB.decode_message(0x126, data, decode_choices=False)['SIG_REPAIR']
        d = DB.decode_message(0x127, data, decode_choices=False)
        r = rs.decode_res(data)
        assert (r['res_start'], r['alive']) == (d['RES_START'], d['alive_count'])
        d = DB.decode_message(0x128, data, decode_choices=False)
        r = rs.decode_res_fb(data)
        assert (r['res_end'], r['res_error']) == (d['RES_END'], d['RES_ERROR'])
        d = DB.decode_message(0x202, data, decode_choices=False)
        assert rs.decode_speed_limit_info(data) == pytest.approx(d['speed_limit_info'])


def test_speed_limit_info_real_value_1104():
    """11-04 실측 `2C01...` = raw 300 = 30.0 km/h."""
    assert rs.decode_speed_limit_info(bytes.fromhex('2C01000000000000')) == pytest.approx(30.0)


def test_short_frames_do_not_raise():
    for fn in (rs.decode_race_signal, rs.decode_pit_zone, rs.decode_repair, rs.decode_res, rs.decode_res_fb,
               rs.decode_speed_limit_info):
        fn(b'\x80')
        fn(b'')
    assert rs.decode_race_signal(b'\x80')['go'] == 1


# --- 깃발: 2프레임 확인 + seen 래치 --------------------------------------------------------------------------------------

@pytest.mark.parametrize('flag', list(BYTE0))
def test_two_frames_confirm_and_seen_latches(flag):
    dec = rs.RaceSignalDecoder()
    dec.feed(f124(flag), 0.0)
    assert not dec.levels[flag] and not dec.seen_flags[flag] and dec.last_signal == 'NONE'
    dec.feed(f124(flag), DT)
    assert dec.levels[flag] and dec.seen_flags[flag]
    assert dec.last_signal == flag.upper() and dec.last_signal_t == 0.0     # 첫 프레임 시각
    feed_n(dec, f124(), 2, 2 * DT)
    assert not dec.levels[flag] and dec.seen_flags[flag]                    # 꺼져도 seen 유지
    assert dec.last_signal == flag.upper()


def test_one_frame_is_ignored_and_recorded():
    dec = rs.RaceSignalDecoder()
    dec.feed(f124('go'), 0.0)
    dec.feed(f124(), DT)
    assert not dec.seen_flags['go'] and dec.last_signal == 'NONE'
    assert ('race_signal_ignored', 'go frames=1 suppressed=0') in dec.take_records()


def test_flicker_never_confirms_and_log_is_rate_limited():
    dec = rs.RaceSignalDecoder()
    t = 0.0
    for _ in range(10):
        dec.feed(f124('go'), t)
        dec.feed(f124(), t + DT)
        t += 2 * DT
    assert not dec.seen_flags['go']
    assert sum(1 for n, _ in dec.take_records() if n == 'race_signal_ignored') == 1


def test_two_frame_pulse_is_seen_then_level_falls():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124('go'), 2)
    assert dec.levels['go'] and dec.seen_flags['go']
    feed_n(dec, f124(), 2, t)
    assert not dec.levels['go']
    names = [d for n, d in dec.take_records() if n == 'race_signal']
    assert names == ['go on', 'go off']


def test_single_zero_frame_does_not_drop_level_or_new_edge():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124('go'), 2)
    first = dec.last_signal_t
    dec.feed(f124(), t)
    assert dec.levels['go']
    feed_n(dec, f124('go'), 2, t + DT)
    assert dec.last_signal_t == first


def test_new_rise_updates_last_signal_stamp():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124('go'), 2)
    t = feed_n(dec, f124(), 2, t)
    t2 = t
    feed_n(dec, f124('go'), 2, t)
    assert dec.last_signal_t == t2


def test_last_signal_follows_latest_rise():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124('go'), 2)
    t = feed_n(dec, f124('go', 'slow_on'), 2, t)
    assert dec.last_signal == 'SLOW_ON'
    feed_n(dec, f124('go', 'slow_on', 'slow_off'), 2, t)
    assert dec.last_signal == 'SLOW_OFF'
    assert all(dec.levels[f] for f in ('go', 'slow_on', 'slow_off'))   # 해제 규칙 없음 — 해석은 모델


def test_go_already_on_at_start_is_passed_as_is():
    """전달만: 시작 때 이미 켜져 있어도 비트대로 올린다. 재시작 후 옛 Go 를 구분하는 일은 모델(M-09)."""
    dec = rs.RaceSignalDecoder()
    feed_n(dec, f124('go'), 2)
    assert dec.seen_flags['go'] and dec.levels['go']


@pytest.mark.parametrize('data', [b'\xC0' + bytes(7), b'\x81' + bytes(7), b'\x00\x01' + bytes(6)])
def test_undefined_pattern_recorded_once_until_it_changes(data):
    dec = rs.RaceSignalDecoder()
    feed_n(dec, data, 3)
    assert [n for n, _ in dec.take_records()].count('race_signal_unknown') == 1


def test_empty_frame_ignored_and_recorded():
    dec = rs.RaceSignalDecoder()
    dec.feed(b'', 0.0)
    assert dec.age_s(1.0) is None
    assert dec.take_records() == [('race_signal_unknown', 'empty_frame')]


def test_age():
    dec = rs.RaceSignalDecoder()
    assert dec.age_s(5.0) is None
    dec.feed(f124(), 5.0)
    assert dec.age_s(5.5) == pytest.approx(0.5)


# --- RES: 1 프레임 즉시, alive ----------------------------------------------------------------------------------------

def test_res_active_on_first_frame_and_off_on_first_frame():
    m = rs.ResMonitor()
    m.feed(res_frame(1, 1), 0.0)
    assert m.active
    m.feed(res_frame(0, 2), DT)
    assert not m.active
    assert [d.split()[0] for n, d in m.take_records() if n == 'res'] == ['start', 'end']


def test_alive_ok_needs_moving_counter():
    m = rs.ResMonitor(alive_timeout_s=0.5)
    assert not m.alive_ok
    m.feed(res_frame(0, 0), 0.0)
    m.feed(res_frame(0, 1), DT)
    assert m.alive_ok


def test_alive_wrap_255_to_0_is_normal():
    m = rs.ResMonitor()
    m.feed(res_frame(0, 255), 0.0)
    m.feed(res_frame(0, 0), DT)
    assert m.alive_ok
    assert not [r for r in m.take_records() if r[0] == 'res_alive_jump']


def test_alive_jump_is_recorded_only():
    m = rs.ResMonitor()
    m.feed(res_frame(0, 1), 0.0)
    m.feed(res_frame(0, 5), DT)
    assert m.alive_ok
    assert ('res_alive_jump', '1->5') in m.take_records()


def test_frames_lost_times_out():
    m = rs.ResMonitor(alive_timeout_s=0.5)
    m.feed(res_frame(0, 1), 0.0)
    m.feed(res_frame(0, 2), DT)
    m.update(DT + 0.49)
    assert m.alive_ok
    m.update(DT + 0.51)
    assert not m.alive_ok
    assert ('res_alive_lost', 'timeout') in m.take_records()
    assert m.age_s(1.0) == pytest.approx(1.0 - DT)


def test_alive_stall_with_frames_still_coming():
    m = rs.ResMonitor(alive_timeout_s=0.5)
    m.feed(res_frame(0, 1), 0.0)
    m.feed(res_frame(0, 2), DT)
    t = DT
    while t < 0.6:
        t += DT
        m.feed(res_frame(0, 2), t)
    assert not m.alive_ok
    assert ('res_alive_lost', 'alive_stall') in m.take_records()
    m.feed(res_frame(0, 3), t + DT)
    assert m.alive_ok
    assert ('res_alive_restored', 'alive=3') in m.take_records()


def test_alive_never_changing_reports_false_and_records_once():
    m = rs.ResMonitor(alive_timeout_s=0.5)
    t = 0.0
    while t < 1.5:
        m.feed(res_frame(0, 7), t)
        t += DT
    assert not m.alive_ok
    assert [n for n, _ in m.take_records()].count('res_alive_never_changed') == 1


# --- RaceTracker: /race/state 필드 --------------------------------------------------------------------------------------

def test_tracker_defaults_mean_no_data():
    f = rs.RaceTracker().fields(10.0)
    assert f['race_signal_age_s'] == -1.0 and f['res_age_s'] == -1.0 and f['vehicle_speed_limit_kph'] == -1.0
    assert f['last_signal'] == 'NONE' and f['last_signal_t'] is None
    assert not f['res_active'] and not f['res_alive_ok'] and not f['remote_app_active']
    assert math.isnan(f['pit_lat_deg']) and math.isnan(f['pit_lon_deg'])


def test_tracker_routes_every_id():
    tr = rs.RaceTracker()
    tr.feed(0x124, f124('finish'), 1.0)
    tr.feed(0x124, f124('finish'), 1.02)
    tr.feed(0x127, res_frame(1, 3), 1.02)
    tr.feed(0x128, bytes([0x80 | 0x05]) + bytes(7), 1.03)
    tr.feed(0x202, bytes.fromhex('2C01000000000000'), 1.03)
    tr.feed(0x156, bytes([1]) + bytes(7), 1.04)
    lat = int(37.5 / 1e-7).to_bytes(4, 'little')
    lon = int(127.25 / 1e-7).to_bytes(4, 'little')
    tr.feed(0x125, lat + lon, 1.04)
    f = tr.fields(1.1)
    assert f['sig_finish'] and f['seen_finish'] and f['last_signal'] == 'FINISH' and f['last_signal_t'] == 1.0
    assert f['res_active'] and f['res_end'] and f['res_error'] == 5
    assert f['vehicle_speed_limit_kph'] == pytest.approx(30.0)
    assert f['remote_app_active']
    assert f['pit_lat_deg'] == pytest.approx(37.5) and f['pit_lon_deg'] == pytest.approx(127.25)
    assert f['race_signal_age_s'] == pytest.approx(0.08)


def test_tracker_ignores_other_ids():
    tr = rs.RaceTracker()
    key = tr.state_key()
    assert tr.feed(0x200, bytes(8), 1.0) is False
    assert tr.state_key() == key


def test_state_key_changes_on_values_not_on_age():
    tr = rs.RaceTracker()
    tr.feed(0x124, f124(), 1.0)
    key = tr.state_key()
    tr.feed(0x124, f124(), 1.02)
    assert tr.state_key() == key
    tr.feed(0x127, res_frame(1, 1), 1.04)
    assert tr.state_key() != key


def test_state_key_with_nan_is_stable():
    tr = rs.RaceTracker()
    assert tr.state_key() == tr.state_key()


def test_records_cover_can_guard_events():
    tr = rs.RaceTracker()
    tr.feed(0x202, bytes.fromhex('2C01000000000000'), 1.0)
    tr.feed(0x202, bytes.fromhex('2C01000000000000'), 1.02)
    tr.feed(0x127, res_frame(1, 1), 1.02)
    names = [n for n, _ in tr.take_records()]
    assert names.count('speed_limit_info') == 1 and 'res' in names
    assert tr.take_records() == []


def test_guard_log_ids_exclude_app():
    assert 0x156 not in rs.GUARD_LOG_FRAME_IDS and 0x127 in rs.GUARD_LOG_FRAME_IDS
