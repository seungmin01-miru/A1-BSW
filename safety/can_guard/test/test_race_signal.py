"""race_signal·encode_speed_limit_command — 1.2절 예시, cantools + A1_dbc_2_fixed.dbc 대조, 디바운스, A1, A7, alive 경계."""
import os
import random

import cantools
import pytest

import race_signal as rs
from tx_encode import FRAME_ID_SERVICE_COMMAND, encode_speed_limit_command

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
    (rs.FRAME_ID_FB_STEER, 'KIAPI_FB_Steer'), (rs.FRAME_ID_FB_ACC, 'KIAPI_FB_Acc'),
    (FRAME_ID_SERVICE_COMMAND, 'USER_service_command'),
])
def test_frame_ids_match_dbc(const, msg):
    assert DB.get_message_by_name(msg).frame_id == const


@pytest.mark.parametrize('flag,byte0', sorted(BYTE0.items()))
def test_race_signal_byte0_examples_section_1_2(flag, byte0):
    """계획서 1.2절 byte0 예시 그대로 + cantools 도 같은 값."""
    data = bytes([byte0]) + bytes(7)
    got = rs.decode_race_signal(data)
    assert got == {k: 1 if k == flag else 0 for k in BYTE0}
    dbc = DB.decode_message(0x124, data, decode_choices=False)
    assert {k: dbc[SIGNAL[k]] for k in BYTE0} == got


def test_race_signal_all_byte0_values_match_dbc():
    for b0 in range(256):
        data = bytes([b0]) + bytes(7)
        dbc = DB.decode_message(0x124, data, decode_choices=False)
        assert {k: dbc[SIGNAL[k]] for k in BYTE0} == rs.decode_race_signal(data)


def test_other_decoders_match_dbc_random():
    rnd = random.Random(20261106)
    for _ in range(300):
        data = bytes(rnd.randrange(256) for _ in range(8))
        d = DB.decode_message(0x125, data, decode_choices=False)
        lat, lon = rs.decode_pit_zone(data)
        assert lat == pytest.approx(d['PITZONE_LAT'])
        assert lon == pytest.approx(d['PITZONE_LONG'])
        assert rs.decode_repair(data) == DB.decode_message(0x126, data, decode_choices=False)['SIG_REPAIR']
        d = DB.decode_message(0x127, data, decode_choices=False)
        assert rs.decode_res(data) == {'res_start': d['RES_START'], 'alive': d['alive_count']}
        d = DB.decode_message(0x128, data, decode_choices=False)
        assert rs.decode_res_fb(data) == {'res_end': d['RES_END'], 'res_error': d['RES_ERROR']}
        d = DB.decode_message(0x202, data, decode_choices=False)
        assert rs.decode_speed_limit_info(data) == pytest.approx(d['speed_limit_info'])
        d = DB.decode_message(0x710, data, decode_choices=False)
        g = rs.decode_fb_steer(data)
        assert g['str_ang_deg'] == pytest.approx(d['StrAng'])
        assert (g['eps_user_can_err'], g['eps_err'], g['eps_sas_err']) == \
               (d['EPS_USER_CAN_ERR'], d['EPS_ERR'], d['EPS_SAS_ERR'])
        assert (g['acc_mode'], g['brake_mode'], g['steer_mode'], g['alive']) == \
               (d['acc_mode'], d['brake_mode'], d['steer_mode'], d['EPS_Alive_Cnt'])
        d = DB.decode_message(0x711, data, decode_choices=False)
        g = rs.decode_fb_acc(data)
        assert g['vs_kph'] == pytest.approx(d['VS'])
        assert (g['acc_user_can_err'], g['acc_err'], g['alive']) == \
               (d['ACC_USER_CAN_ERR'], d['ACC_ERR'], d['ACC_Alive_Cnt'])


def test_speed_limit_info_real_value_a5():
    """A5: 11-04 실측 `2C01...` = raw 300 = 30.0 km/h."""
    data = bytes.fromhex('2C01000000000000')
    assert rs.decode_speed_limit_info_raw(data) == 300
    assert rs.decode_speed_limit_info(data) == pytest.approx(30.0)


def test_short_frames_do_not_raise():
    for fn in (rs.decode_race_signal, rs.decode_pit_zone, rs.decode_repair, rs.decode_res, rs.decode_res_fb,
               rs.decode_speed_limit_info, rs.decode_fb_steer, rs.decode_fb_acc):
        fn(b'\x80')
        fn(b'')
    assert rs.decode_race_signal(b'\x80')['go'] == 1


@pytest.mark.parametrize('kph,raw', [(0, 0), (9.3, 93), (30.0, 300), (100.0, 1000), (0.1, 1)])
def test_encode_speed_limit_matches_dbc(kph, raw):
    data = encode_speed_limit_command(kph)
    assert len(data) == 8
    assert int.from_bytes(data[:2], 'little') == raw and not any(data[2:])
    assert DB.decode_message(0x203, data, decode_choices=False)['speed_limit'] == pytest.approx(kph)
    assert data == DB.encode_message(0x203, {'speed_limit': kph})


@pytest.mark.parametrize('kph,clamped', [(-5.0, 0.0), (100.1, 100.0), (250.0, 100.0)])
def test_encode_speed_limit_clamps(kph, clamped):
    assert DB.decode_message(0x203, encode_speed_limit_command(kph), decode_choices=False)['speed_limit'] == \
           pytest.approx(clamped)


# --- RaceSignalDecoder ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('flag', sorted(BYTE0))
def test_two_frames_needed_then_one_edge(flag):
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 3)
    assert dec.take_events() == ()
    dec.feed(f124(flag), t)
    assert dec.take_events() == () and not dec.levels[flag]
    dec.feed(f124(flag), t + DT)
    assert dec.take_events() == (flag,) and dec.levels[flag]
    feed_n(dec, f124(flag), 10, t + 2 * DT)
    assert dec.take_events() == ()   # 계속 켜져 있어도 이벤트는 한 번


def test_single_frame_is_rejected_d12():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 3)
    dec.feed(f124('slow_off'), t)
    feed_n(dec, f124(), 3, t + DT)
    assert dec.take_events() == () and not dec.levels['slow_off']


def test_single_zero_frame_does_not_make_new_edge():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 2)
    t = feed_n(dec, f124('go'), 2, t)
    assert dec.take_events() == ('go',)
    dec.feed(f124(), t)
    feed_n(dec, f124('go'), 2, t + DT)
    assert dec.take_events() == () and dec.levels['go']


def test_level_falls_after_two_zero_frames_and_new_edge_after():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 2)
    t = feed_n(dec, f124('go'), 2, t)
    dec.take_events()
    t = feed_n(dec, f124(), 2, t)
    assert not dec.levels['go']
    feed_n(dec, f124('go'), 2, t)
    assert dec.take_events() == ('go',)


def test_custom_confirm_frames():
    dec = rs.RaceSignalDecoder(confirm_frames=3)
    t = feed_n(dec, f124(), 3)
    t = feed_n(dec, f124('stop'), 2, t)
    assert dec.take_events() == ()
    dec.feed(f124('stop'), t)
    assert dec.take_events() == ('stop',)


def test_events_accumulate_until_taken():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 2)
    t = feed_n(dec, f124('go'), 2, t)
    feed_n(dec, f124('slow_on'), 2, t)
    assert dec.take_events() == ('go', 'slow_on')
    assert dec.take_events() == ()


def test_trigger_t_is_first_frame_of_run():
    dec = rs.RaceSignalDecoder()
    feed_n(dec, f124(), 2)
    dec.feed(f124('stop'), 1.00)
    dec.feed(f124('stop'), 1.02)
    assert dec.trigger_t('stop') == pytest.approx(1.00)


# A1 비대칭
@pytest.mark.parametrize('flag', rs.RELEASE_FLAGS)
def test_a1_release_flag_on_at_start_is_no_event(flag):
    dec = rs.RaceSignalDecoder()
    feed_n(dec, f124(flag), 20)
    assert dec.take_events() == ()
    assert dec.levels[flag]
    assert dec.take_records() == [('race_signal', f'{flag}_level_at_start')]   # 한 번만 기록


def test_a1_go_at_start_single_zero_glitch_does_not_arm():
    """옛 Go 위의 단발 0 + 복귀를 새 Go 로 보면 R-01 위반 — 0 도 2프레임 확인."""
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124('go'), 5)
    dec.feed(f124(), t)
    feed_n(dec, f124('go'), 5, t + DT)
    assert dec.take_events() == ()


def test_a1_go_after_seen_zero_is_event():
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124('go'), 5)
    t = feed_n(dec, f124(), 2, t)
    assert dec.take_events() == ()
    feed_n(dec, f124('go'), 2, t)
    assert dec.take_events() == ('go',)


@pytest.mark.parametrize('flag', ['stop', 'finish', 'slow_on'])
def test_a1_restrictive_flag_on_at_start_is_event(flag):
    dec = rs.RaceSignalDecoder()
    dec.feed(f124(flag), 0.0)
    assert dec.take_events() == ()
    dec.feed(f124(flag), DT)
    assert dec.take_events() == (flag,)
    assert dec.take_records() == []


# 정의 밖
@pytest.mark.parametrize('data', [f124('slow_on', 'slow_off'), f124('go', 'stop'), bytes([0x01]) + bytes(7),
                                  bytes([0, 0, 0, 0, 0, 0, 0, 1])])
def test_undefined_pattern_recorded_once_until_it_changes(data):
    dec = rs.RaceSignalDecoder()
    feed_n(dec, data, 5)
    recs = dec.take_records()
    assert [r for r in recs if r[0] == 'race_signal_unknown'] == [('race_signal_unknown', f'raw={data.hex()}')]


def test_undefined_pattern_still_gives_flag_events():
    """둘 다 올리는 건 맞고 우선순위는 race_state 가 정한다(stop 을 잃지 않음)."""
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 2)
    feed_n(dec, f124('go', 'stop'), 2, t)
    assert set(dec.take_events()) == {'go', 'stop'}


@pytest.mark.parametrize('flag', sorted(BYTE0))
def test_defined_patterns_not_unknown(flag):
    dec = rs.RaceSignalDecoder()
    t = feed_n(dec, f124(), 2)
    feed_n(dec, f124(flag), 2, t)
    assert not [r for r in dec.take_records() if r[0] == 'race_signal_unknown']


def test_empty_frame_is_ignored_and_recorded():
    dec = rs.RaceSignalDecoder()
    dec.feed(b'', 0.0)
    assert not dec.seen and dec.take_records() == [('race_signal_unknown', 'empty_frame')]


def test_age():
    dec = rs.RaceSignalDecoder()
    assert dec.age_s(5.0) is None and not dec.seen
    dec.feed(f124(), 5.0)
    assert dec.age_s(5.3) == pytest.approx(0.3)


# --- ResMonitor -------------------------------------------------------------------------------------------------------

def test_res_start_needs_two_frames_a7():
    m = rs.ResMonitor()
    m.feed(res_frame(1, 0), 0.0)
    assert not m.active
    m.feed(res_frame(0, 1), 0.02)
    m.feed(res_frame(1, 2), 0.04)
    assert not m.active
    m.feed(res_frame(1, 3), 0.06)
    assert m.active and m.asserted and m.trigger_t == pytest.approx(0.04)
    assert ('res_start', 'alive=3') in m.take_records()


def test_res_on_from_first_frame_is_active():
    m = rs.ResMonitor()
    m.feed(res_frame(1, 0), 0.0)
    m.feed(res_frame(1, 1), 0.02)
    assert m.active


def test_res_release_needs_two_frames():
    m = rs.ResMonitor()
    m.feed(res_frame(1, 0), 0.0)
    m.feed(res_frame(1, 1), 0.02)
    m.feed(res_frame(0, 2), 0.04)
    assert m.active
    m.feed(res_frame(0, 3), 0.06)
    assert not m.active
    assert ('res_end', 'alive=3') in m.take_records()


def test_res_not_seen_is_not_lost():
    m = rs.ResMonitor()
    m.update(1000.0)
    assert not m.seen and not m.alive_lost and not m.asserted


def test_res_lost_after_timeout_only_past_0_5s():
    m = rs.ResMonitor()
    m.feed(res_frame(0, 0), 1.0)
    m.update(1.5)
    assert not m.alive_lost
    m.update(1.51)
    assert m.alive_lost and m.asserted
    assert ('res_alive_lost', 'timeout') in m.take_records()


def test_res_lost_restored_when_frames_resume():
    m = rs.ResMonitor()
    m.feed(res_frame(0, 0), 1.0)
    m.update(2.0)
    assert m.alive_lost
    m.feed(res_frame(0, 1), 2.1)
    m.update(2.1)
    assert not m.alive_lost
    assert ('res_alive_restored', 'alive=1') in m.take_records()


def test_alive_boundary_255_to_0_is_not_a_jump():
    m = rs.ResMonitor()
    t = 0.0
    for a in [253, 254, 255, 0, 1]:
        m.feed(res_frame(0, a), t)
        t += DT
    assert not [r for r in m.take_records() if r[0] == 'res_alive_jump']


@pytest.mark.parametrize('seq,detail', [([5, 7], '5->7'), ([255, 1], '255->1'), ([10, 9], '10->9')])
def test_alive_jump_recorded_but_not_lost(seq, detail):
    m = rs.ResMonitor()
    m.feed(res_frame(0, seq[0]), 0.0)
    m.feed(res_frame(0, seq[1]), DT)
    m.update(DT)
    assert ('res_alive_jump', detail) in m.take_records()
    assert not m.alive_lost


def test_alive_stall_after_counting_is_lost():
    m = rs.ResMonitor()
    m.feed(res_frame(0, 1), 0.0)
    m.feed(res_frame(0, 2), 0.02)
    t = 0.02
    while t < 0.5:
        t += DT
        m.feed(res_frame(0, 2), t)
        m.update(t)
    assert not m.alive_lost
    t = 0.53
    m.feed(res_frame(0, 2), t)
    m.update(t)
    assert m.alive_lost
    assert ('res_alive_lost', 'alive_stall') in m.take_records()
    m.feed(res_frame(0, 3), t + DT)
    m.update(t + DT)
    assert not m.alive_lost


def test_alive_never_changed_only_recorded():
    """서버가 카운터를 안 올려도 경기를 끝내지 않는다."""
    m = rs.ResMonitor()
    t = 0.0
    for _ in range(100):
        m.feed(res_frame(0, 0), t)
        m.update(t)
        t += DT
    assert not m.alive_lost
    assert m.take_records() == [('res_alive_never_changed', 'alive=0')]


def test_timeout_zero_disables_lost():
    m = rs.ResMonitor(alive_timeout_s=0)
    m.feed(res_frame(0, 0), 0.0)
    m.update(100.0)
    assert not m.alive_lost
