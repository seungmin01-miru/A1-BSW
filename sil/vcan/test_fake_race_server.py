#!/usr/bin/env python3
"""fake_race_server.py 시험 — 인코딩(cantools + DBC 대조)·재생 방식·시나리오 파일. vcan 없이 돈다(가상 시계·virtual 버스)."""
import glob
import inspect
import os
import sys
import threading
import uuid

import can
import cantools
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import fake_race_server as frs  # noqa: E402
from fake_a1_vehicle import FakeVehicle  # noqa: E402
from fake_race_server import RaceServer, load_scenario  # noqa: E402

DBC = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc'))
SCEN_DIR = os.path.join(HERE, 'race_scenarios')
ALL_YAML = sorted(glob.glob(os.path.join(SCEN_DIR, '*.yaml')))
SIG_NAME = {'go': 'SIG_GO', 'stop': 'SIG_STOP', 'finish': 'SIG_FINISH', 'slow_on': 'SIG_SLOW_ON', 'slow_off': 'SIG_SLOW_OFF'}


class _Rec:
    def __init__(self):
        self.tx = []

    def send(self, m):
        self.tx.append(m)


def play(scenario, dt=0.02, end=None):
    """가상 시계로 끝까지 재생. 프레임 목록을 돌려준다."""
    rec = _Rec()
    srv = RaceServer(rec, scenario)
    srv.start(100.0)
    end = srv.duration_s if end is None else end
    n = int(round(end / dt)) + 1
    for i in range(n):
        srv.step(100.0 + i * dt)
    return rec.tx


def of(frames, arb):
    return [(round(m.timestamp - 100.0, 6), bytes(m.data)) for m in frames if m.arbitration_id == arb]


def go_frames(frames):
    return [t for t, d in of(frames, 0x124) if d[0] & 0x80]


def sc(events, duration=1.0, **kw):
    return dict(name='t', duration_s=duration, events=events, **kw)


# ---- 인코딩 ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('flag,byte0', [('go', 0x80), ('stop', 0x40), ('finish', 0x20), ('slow_on', 0x10), ('slow_off', 0x08)])
def test_race_signal_single_bit_matches_plan_and_dbc(flag, byte0):
    d = frs.encode_race_signal([flag])
    assert d == bytes([byte0]) + bytes(7)
    values = {v: 0 for v in SIG_NAME.values()}
    values[SIG_NAME[flag]] = 1
    assert d == DBC.encode_message('KIAPI_Race_Signal', values)
    assert DBC.decode_message(0x124, d, decode_choices=False) == values


def test_race_signal_every_combination_matches_dbc():
    names = list(SIG_NAME)
    for mask in range(32):
        on = [n for i, n in enumerate(names) if mask >> i & 1]
        values = {SIG_NAME[n]: int(n in on) for n in names}
        assert frs.encode_race_signal(on) == DBC.encode_message('KIAPI_Race_Signal', values)


@pytest.mark.parametrize('start,alive', [(0, 0), (1, 0), (0, 255), (1, 37)])
def test_res_matches_dbc(start, alive):
    assert frs.encode_res(start, alive) == DBC.encode_message('KIAPI_RES', {'RES_START': start, 'alive_count': alive})


def test_pit_zone_and_repair_match_dbc():
    d = frs.encode_pit_zone(37.1234567, 127.7654321)
    assert d == DBC.encode_message('KIAPI_PIT_ZONE', {'PITZONE_LAT': 37.1234567, 'PITZONE_LONG': 127.7654321})
    dec = DBC.decode_message(0x125, d)
    assert dec['PITZONE_LAT'] == pytest.approx(37.1234567, abs=1e-7)
    assert dec['PITZONE_LONG'] == pytest.approx(127.7654321, abs=1e-7)
    assert frs.encode_repair(200) == DBC.encode_message('KIAPI_REPAIR', {'SIG_REPAIR': 200})
    with pytest.raises(ValueError):
        frs.encode_repair(256)
    with pytest.raises(ValueError):
        frs.encode_pit_zone(-1.0, 127.0)


def test_alive_count_wraps_through_real_decoder():
    frames = play(sc([], duration=5.4))
    res = of(frames, 0x127)
    assert [d[7] for _, d in res[:3]] == [0, 1, 2]
    assert res[255][1][7] == 255 and res[256][1][7] == 0
    assert DBC.decode_message(0x127, res[256 + 7][1])['alive_count'] == 7


# ---- 재생 방식 --------------------------------------------------------------------------------------------------------
def test_default_period_and_streams():
    frames = play(sc([]))
    t124, t127 = [t for t, _ in of(frames, 0x124)], [t for t, _ in of(frames, 0x127)]
    assert len(t124) == len(t127) == 50
    assert t124[:3] == pytest.approx([0.0, 0.02, 0.04])
    assert not of(frames, 0x125) and not of(frames, 0x126)      # 선택 프레임은 기본 없음
    assert all(d == bytes(8) for _, d in of(frames, 0x124))


def test_period_ms_respected():
    assert len(of(play(sc([], period_ms=10)), 0x124)) == 100


def test_level_with_until():
    frames = play(sc([dict(t=0.2, flag='go', mode='level', until=0.4)]))
    assert go_frames(frames) == pytest.approx([0.2 + 0.02 * i for i in range(10)])


def test_level_defaults_to_end():
    frames = play(sc([dict(t=0.9, flag='go')]))
    assert len(go_frames(frames)) == 5


@pytest.mark.parametrize('n', [1, 2, 3])
def test_pulse_exact_frame_count(n):
    assert len(go_frames(play(sc([dict(t=0.2, flag='go', mode='pulse', frames=n)])))) == n


def test_flicker_never_two_consecutive():
    frames = play(sc([dict(t=0.1, flag='go', mode='flicker', on_frames=1, off_frames=1, repeat=5)]))
    bits = [bool(d[0] & 0x80) for _, d in of(frames, 0x124)]
    assert sum(bits) == 5
    assert not any(a and b for a, b in zip(bits, bits[1:]))


def test_flicker_pattern_and_until():
    frames = play(sc([dict(t=0.0, flag='slow_on', mode='flicker', on_frames=2, off_frames=3, until=0.2)]))
    bits = [int(bool(d[0] & 0x10)) for _, d in of(frames, 0x124)]
    assert bits[:10] == [1, 1, 0, 0, 0, 1, 1, 0, 0, 0]
    assert sum(bits[10:]) == 0


def test_simultaneous_bits_in_one_frame():
    frames = play(sc([dict(t=0.1, flag=['slow_on', 'slow_off'], until=0.2),
                      dict(t=0.3, flag='go', until=0.4), dict(t=0.3, flag='slow_on', until=0.4)]))
    bytes0 = {d[0] for _, d in of(frames, 0x124)}
    assert bytes0 == {0x00, 0x18, 0x90}


def test_res_level_pulse_and_alive_keeps_counting():
    frames = play(sc([dict(t=0.2, flag='res', until=0.4), dict(t=0.6, flag='res', mode='pulse', frames=1)]))
    res = of(frames, 0x127)
    on = [t for t, d in res if d[0] & 0x80]
    assert on == pytest.approx([0.2 + 0.02 * i for i in range(10)] + [0.6])
    assert [d[7] for _, d in res] == list(range(len(res)))


def test_flag_list_with_race_and_res_counts_each_stream():
    frames = play(sc([dict(t=0.1, flag=['go', 'res'], mode='pulse', frames=2)]))
    assert len(go_frames(frames)) == 2
    assert sum(1 for _, d in of(frames, 0x127) if d[0] & 0x80) == 2


def test_res_alive_stop_and_resume():
    frames = play(sc([dict(t=0.2, stream='res', action='stop'), dict(t=0.6, stream='res', action='run')]))
    res = of(frames, 0x127)
    ts = [t for t, _ in res]
    assert not [t for t in ts if 0.2 - 1e-6 <= t < 0.6 - 1e-6]
    assert [t for t in ts if t >= 0.6 - 1e-6][0] == pytest.approx(0.6)
    assert [d[7] for _, d in res] == list(range(len(res)))      # 끊긴 구간은 세지 않는다
    assert len(of(frames, 0x124)) == 50                         # 0x124 는 그대로


def test_res_alive_freeze_keeps_frames_but_stops_counter():
    frames = play(sc([dict(t=0.2, stream='res', action='freeze')]))
    res = of(frames, 0x127)
    assert len(res) == 50
    assert {d[7] for t, d in res if t >= 0.2 - 1e-6} == {10}


def test_race_signal_stop_and_pulse_counts_sent_frames_only():
    frames = play(sc([dict(t=0.2, stream='race_signal', action='stop'), dict(t=0.4, stream='race_signal', action='run'),
                      dict(t=0.3, flag='go', mode='pulse', frames=2)]))
    ts = [t for t, _ in of(frames, 0x124)]
    assert not [t for t in ts if 0.2 - 1e-6 <= t < 0.4 - 1e-6]
    assert go_frames(frames) == pytest.approx([0.4, 0.42])      # 끊겨 있는 동안 펄스가 사라지지 않는다


def test_initial_stop_for_each_stream():
    frames = play(sc([dict(t=0.5, stream='res', action='run')], initial=dict(race_signal='stop', res='stop')))
    assert not of(frames, 0x124)
    assert of(frames, 0x127)[0][0] == pytest.approx(0.5)


def test_optional_frames_on_and_off():
    frames = play(sc([dict(t=0.2, pit_zone=dict(lat_deg=37.0, long_deg=127.0)), dict(t=0.4, pit_zone='off'),
                      dict(t=0.6, repair=3)]))
    pit = of(frames, 0x125)
    assert [t for t, _ in pit] == pytest.approx([0.2 + 0.02 * i for i in range(10)])
    assert DBC.decode_message(0x125, pit[0][1])['PITZONE_LONG'] == pytest.approx(127.0, abs=1e-6)
    rep = of(frames, 0x126)
    assert rep[0][0] == pytest.approx(0.6) and DBC.decode_message(0x126, rep[0][1])['SIG_REPAIR'] == 3


@pytest.mark.parametrize('dt', [0.001, 0.02, 0.03, 0.25])
def test_call_rate_does_not_change_frames(dt):
    ref = [(m.arbitration_id, bytes(m.data), round(m.timestamp, 6))
           for m in play(sc([dict(t=0.1, flag='go', mode='pulse', frames=2), dict(t=0.5, flag='res', until=0.7)]))]
    got = [(m.arbitration_id, bytes(m.data), round(m.timestamp, 6))
           for m in play(sc([dict(t=0.1, flag='go', mode='pulse', frames=2), dict(t=0.5, flag='res', until=0.7)]), dt=dt,
                         end=1.0 + dt)]
    assert got == ref


def test_stops_at_duration_and_finished():
    rec = _Rec()
    srv = RaceServer(rec, sc([]))
    assert not srv.finished(0.0)
    srv.start(10.0)
    srv.step(11.5)
    assert len(of_any(rec.tx, 0x124)) == 50
    assert srv.finished(11.0) and not srv.finished(10.9)
    n = len(rec.tx)
    assert srv.step(12.0) == [] and len(rec.tx) == n


def of_any(frames, arb):
    return [m for m in frames if m.arbitration_id == arb]


def test_first_step_sets_zero_time():
    rec = _Rec()
    srv = RaceServer(rec, sc([]))
    assert len(srv.step(55.0)) == 2     # 0x124 + 0x127
    assert srv.t0 == 55.0


# ---- 시나리오 검증 ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('bad', [
    dict(duration_s=1.0, events=[]),                                                   # name 없음
    dict(name='t', events=[]),                                                         # duration 없음
    dict(name='t', duration_s=1.0, typo=1),                                            # 최상위 오타
    sc([dict(flag='go')]),                                                             # t 없음
    sc([dict(t=2.0, flag='go')]),                                                      # 구간 밖
    sc([dict(t=0.1, flag='gogo')]),                                                    # 모르는 깃발
    sc([dict(t=0.1, flag='go', mode='wave')]),                                         # 모르는 방식
    sc([dict(t=0.1, flag='go', mode='pulse')]),                                        # frames 없음
    sc([dict(t=0.1, flag='go', mode='pulse', frames=0)]),
    sc([dict(t=0.1, flag='go', mode='flicker', on_frames=1, off_frames=1)]),           # 끝 없음
    sc([dict(t=0.1, flag='go', mode='flicker', on_frames=0, off_frames=1, repeat=1)]),
    sc([dict(t=0.1, flag='go', mode='level', frames=3)]),                              # level 에 frames
    sc([dict(t=0.5, flag='go', until=0.4)]),                                           # until ≤ t
    sc([dict(t=0.1, flag='go', extra=1)]),                                             # 사건 오타
    sc([dict(t=0.1, flag='go', stream='res', action='stop')]),                         # 종류 둘
    sc([dict(t=0.1)]),                                                                 # 종류 없음
    sc([dict(t=0.1, stream='radio', action='stop')]),
    sc([dict(t=0.1, stream='race_signal', action='freeze')]),                          # freeze 는 res 전용
    sc([dict(t=0.1, stream='res', action='pause')]),
    sc([dict(t=0.1, pit_zone=5)]),
    sc([dict(t=0.1, pit_zone=dict(lat=1, lon=2))]),
    sc([], initial=dict(race_signal='freeze')),
    sc([], initial=dict(other='run')),
    sc([], period_ms=0),
])
def test_bad_scenarios_rejected(bad):
    with pytest.raises((ValueError, KeyError)):
        load_scenario(bad)


# ---- 시나리오 파일 ----------------------------------------------------------------------------------------------------
def test_every_server_scenario_of_plan_exists():
    names = {os.path.basename(p).split('_')[0] for p in ALL_YAML}
    for sid in ('S01', 'S02', 'S03', 'S04', 'S05', 'S06', 'S07', 'S08', 'S09', 'S10', 'S11a', 'S11b',
                'S12a', 'S12b', 'S13', 'S16', 'S17a', 'S17b'):
        assert sid in names


@pytest.mark.parametrize('path', ALL_YAML, ids=[os.path.basename(p) for p in ALL_YAML])
def test_yaml_loads_name_matches_and_plays(path):
    s = load_scenario(path)
    assert s['name'] == os.path.splitext(os.path.basename(path))[0]
    assert s['harness'].get('expect'), '합격 기준 없음'
    assert of(play(path), 0x124), '0x124 프레임이 있어야 한다'


@pytest.mark.parametrize('path', ALL_YAML, ids=[os.path.basename(p) for p in ALL_YAML])
def test_yaml_vehicle_args_are_real_fake_vehicle_arguments(path):
    allowed = set(inspect.signature(FakeVehicle.__init__).parameters) - {'self', 'bus'}
    vehicle = load_scenario(path)['harness'].get('vehicle', {})
    assert set(vehicle) <= allowed
    FakeVehicle(_Rec(), **vehicle)      # 값까지 받아들여지는지


def _yaml(name):
    return os.path.join(SCEN_DIR, name + '.yaml')


def test_s03_is_one_frame_s04_is_two_frames():
    assert len(go_frames(play(_yaml('S03_go_pulse_1frame')))) == 1
    assert len(go_frames(play(_yaml('S04_go_pulse_2frames')))) == 2


def test_s02b_go_on_from_first_frame_then_falls_and_rises():
    bits = [bool(d[0] & 0x80) for _, d in of(play(_yaml('S02b_go_on_at_start')), 0x124)]
    assert bits[0] and not bits[225] and bits[260] and bits[-1]


def test_s09_res_window():
    on = [t for t, d in of(play(_yaml('S09_res_then_release')), 0x127) if d[0] & 0x80]
    assert on[0] == pytest.approx(7.0) and on[-1] == pytest.approx(9.98)


def test_s10_frames_end_and_s10b_counter_freezes():
    assert of(play(_yaml('S10_res_alive_stop')), 0x127)[-1][0] == pytest.approx(4.98)
    tail = of(play(_yaml('S10b_res_alive_freeze')), 0x127)[-100:]
    assert len({d[7] for _, d in tail}) == 1


def test_s11_gap_and_s12_bytes():
    assert of(play(_yaml('S11a_race_signal_lost_default')), 0x124)[-1][0] == pytest.approx(4.98)
    assert 0x18 in {d[0] & 0x18 for _, d in of(play(_yaml('S12a_slow_on_and_off')), 0x124)}
    assert 0x90 in {d[0] for _, d in of(play(_yaml('S12b_go_and_slow_on')), 0x124)}
    assert 0xE0 in {d[0] for _, d in of(play(_yaml('S12c_stop_finish_go')), 0x124)}


def test_x01_optional_frames_appear_and_vanish():
    frames = play(_yaml('X01_pit_repair_record_only'))
    assert of(frames, 0x125)[0][0] == pytest.approx(2.0) and of(frames, 0x125)[-1][0] == pytest.approx(4.98)
    assert of(frames, 0x126)[0][0] == pytest.approx(3.0) and of(frames, 0x126)[-1][0] == pytest.approx(4.98)


# ---- 진짜 virtual 버스 ------------------------------------------------------------------------------------------------
def test_virtual_bus_server_drives_fake_vehicle_res():
    """서버 → 가상 버스 → 차량. RES 중 감속, 0x128 이 버스로 나온다."""
    ch = f'srv_{uuid.uuid4().hex[:8]}'
    b_srv, b_car, b_mon = [can.Bus(channel=ch, interface='virtual') for _ in range(3)]
    try:
        srv = RaceServer(b_srv, sc([dict(t=0.1, flag='res', until=1.5)], duration=2.0))
        car = FakeVehicle(b_car, period_s=0.02, initial_kph=10.0, res_decel_mps2=5.0)
        srv.start(0.0)
        res_seen = False
        for i in range(0, 100):
            now = i * 0.02
            srv.step(now)
            car.step(now)
            res_seen = res_seen or car.res_active
            if i == 74:
                assert car.res_active and car.kph == 0.0 and car.res_end
        assert res_seen and not car.res_active and not car.res_end
        ids, fb = set(), []
        while True:
            m = b_mon.recv(timeout=0)
            if m is None:
                break
            ids.add(m.arbitration_id)
            if m.arbitration_id == 0x128:
                fb.append(DBC.decode_message(0x128, bytes(m.data))['RES_END'])
        assert {0x124, 0x127, 0x128, 0x200, 0x201, 0x202} <= ids
        assert fb[0] == 0 and 1 in fb and fb[-1] == 0
    finally:
        for b in (b_srv, b_car, b_mon):
            b.shutdown()


def test_cli_plays_on_virtual_bus(tmp_path):
    ch = f'cli_{uuid.uuid4().hex[:8]}'
    p = tmp_path / 's.yaml'
    p.write_text('name: s\nduration_s: 0.2\nevents:\n  - {t: 0.05, flag: go}\n', encoding='utf-8')
    mon = can.Bus(channel=ch, interface='virtual')
    out = {}
    th = threading.Thread(target=lambda: out.update(rc=frs.main([str(p), '--interface', 'virtual', '--channel', ch, '--quiet'])))
    try:
        th.start()
        th.join(timeout=5)
        assert not th.is_alive() and out['rc'] == 0
        seen = []
        while True:
            m = mon.recv(timeout=0)
            if m is None:
                break
            seen.append(m)
        assert 8 <= sum(1 for m in seen if m.arbitration_id == 0x124) <= 11
        assert any(m.arbitration_id == 0x124 and m.data[0] == 0x80 for m in seen)
    finally:
        mon.shutdown()


@pytest.mark.parametrize('channel', ['can0', 'can1', 'slcan0'])
def test_cli_refuses_non_virtual_socketcan(channel, capsys):
    assert frs.main([_yaml('S02_go'), '--channel', channel]) == 2
    assert '거부' in capsys.readouterr().err


def test_cli_has_no_real_hardware_interface():
    with pytest.raises(SystemExit):
        frs.main([_yaml('S02_go'), '--interface', 'pcan', '--channel', '0'])


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q']))
