#!/usr/bin/env python3
"""fake_a1_vehicle.py 시험 — 기존 동작 불변(스냅샷) + 경기 깃발 확장(0x202/0x203·RES·0x128). vcan 없이 돈다.

스냅샷: testdata/fake_vehicle_golden.json 은 확장 전 코드(커밋 d702f63)의 main() 을 가짜 버스·가짜 시계로 돌린 결과다.
같은 입력을 지금의 main() 에 넣어 0x200/0x201 프레임 열·버스 인자·인자 파싱·자세 CSV 가 같은지 본다.
다시 만들기(확장 전 파일을 주어야 뜻이 있다):
  python3 sil/vcan/test_fake_vehicle_race.py --make-golden <확장 전 fake_a1_vehicle.py>
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import uuid

import can
import cantools
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'race_day'))
sys.path.insert(0, HERE)
from a1_proto import decode_0x200, decode_0x201, encode_0x210  # noqa: E402
import fake_a1_vehicle as fv  # noqa: E402
from fake_a1_vehicle import FakeVehicle  # noqa: E402

GOLDEN = os.path.join(HERE, 'testdata', 'fake_vehicle_golden.json')
DBC = cantools.database.load_file(os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc'))


# ---- 스냅샷: 기존 rehearse·sil_session 이 쓰는 플래그 ----------------------------------------------------------------
def _cmd(steer, brake, acc, auto=7):
    return encode_0x210(steer, brake, acc, auto & 1, auto & 2, auto & 4)


CASES = {
    # rehearse_lift_guard.sh · rehearse_track.sh · bringup.sh 의 기본 사용
    'default': dict(argv=['--channel', 'vcan0'], ticks=200,
                    stim={5: _cmd(30.0, 0, 40), 60: _cmd(-49.8, 50, 0), 100: _cmd(0, 0, 0, auto=0), 150: _cmd(10, 20, 20, auto=5)}),
    # sil_session.py: 30 km/h 를 넘겨 가속 → 확장 전처럼 제한이 걸리면 안 된다
    'physics_pose': dict(argv=['--channel', 'vcan0', '--physics', '--pose-log', '{pose}'], ticks=500,
                         stim={5: _cmd(150.0, 0, 100), 300: _cmd(-30.0, 90, 0), 400: _cmd(0, 0, 3)}),
    'timeout_period': dict(argv=['--cmd-timeout-s', '0.5', '--period-ms', '10'], ticks=200,
                           stim={5: _cmd(20.0, 10, 30)}, jump={50: 0.2}),
    'physics_ratio_timeout': dict(argv=['--physics', '--steer-ratio', '9.0', '--cmd-timeout-s', '0.3'], ticks=200,
                                  stim={5: _cmd(100.0, 0, 50)}),
    # 리허설의 가상 채널 사용 — 버스·호환 모듈은 가짜로 바꿔 인자만 본다
    'virtual_channel_args': dict(argv=['--interface', 'kvaser', '--channel', '1', '--bitrate', '250000'], ticks=20,
                                 stim={}),
}


class _SnapBus:
    def __init__(self, world, **kw):
        self.world = world
        world['bus_kw'] = kw

    def recv(self, timeout=None):
        w = self.world
        data = w['stim'].pop(w['tick'], None)
        if data is None:
            return None
        return can.Message(arbitration_id=0x210, is_extended_id=False, data=data)

    def send(self, m):
        w = self.world
        w['frames'].append(f'{m.arbitration_id:03X} {bytes(m.data).hex()}')
        if m.arbitration_id == 0x201:
            w['tick'] += 1
            w['now'] += w['jump'].get(w['tick'], 0.0)
            if w['tick'] >= w['ticks']:
                w['mod']._stop = True

    def shutdown(self):
        pass


def _run_main(path, case):
    """main() 을 가짜 버스·가짜 시계로 ticks 주기 돌려 결과를 돌려준다. path = 시험할 fake_a1_vehicle.py."""
    spec = importlib.util.spec_from_file_location(f'fv_snap_{uuid.uuid4().hex}', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    world = dict(frames=[], tick=0, now=1000.0, ticks=case['ticks'], stim=dict(case['stim']),
                 jump=dict(case.get('jump', {})), mod=mod, bus_kw=None)
    fake_time = types.SimpleNamespace(monotonic=lambda: world['now'], sleep=lambda d: world.__setitem__('now', world['now'] + d))
    fake_can = types.SimpleNamespace(Bus=lambda **kw: _SnapBus(world, **kw), Message=can.Message)
    fake_signal = types.SimpleNamespace(signal=lambda *a: None, SIGTERM=15, SIGINT=2)
    mod.time, mod.can, mod.signal = fake_time, fake_can, fake_signal
    compat = types.ModuleType('kvaser_compat')
    compat.apply = lambda: None
    args_seen = {}
    real_parse = argparse.ArgumentParser.parse_args

    def spy_parse(self, *a, **k):
        ns = real_parse(self, *a, **k)
        args_seen.update(vars(ns))
        return ns

    saved_argv, saved_mod = sys.argv, sys.modules.get('kvaser_compat')
    with tempfile.TemporaryDirectory() as td:
        pose = os.path.join(td, 'pose.csv')
        sys.argv = ['fake_a1_vehicle.py'] + [pose if x == '{pose}' else x for x in case['argv']]
        sys.modules['kvaser_compat'] = compat
        argparse.ArgumentParser.parse_args = spy_parse
        try:
            mod.main()
        finally:
            argparse.ArgumentParser.parse_args = real_parse
            sys.argv = saved_argv
            if saved_mod is None:
                sys.modules.pop('kvaser_compat', None)
            else:
                sys.modules['kvaser_compat'] = saved_mod
        pose_text = open(pose, encoding='utf-8').read() if os.path.exists(pose) else ''
    old = [f for f in world['frames'] if f[:3] in ('200', '201')]
    args_seen.pop('pose_log', None)     # 임시 경로
    return {'n_frames': len(old), 'frames_sha256': hashlib.sha256('\n'.join(old).encode()).hexdigest(),
            'last_frames': old[-2:], 'pose_sha256': hashlib.sha256(pose_text.encode()).hexdigest(),
            'pose_lines': pose_text.count('\n'), 'bus_kw': world['bus_kw'], 'args': args_seen}


@pytest.fixture(scope='module')
def golden():
    with open(GOLDEN, encoding='utf-8') as f:
        return json.load(f)


@pytest.mark.parametrize('name', list(CASES))
def test_main_matches_pre_extension_snapshot(golden, name):
    now = _run_main(os.path.join(HERE, 'fake_a1_vehicle.py'), CASES[name])
    g = golden['cases'][name]
    assert now['n_frames'] == g['n_frames']
    assert now['last_frames'] == g['last_frames']
    assert now['frames_sha256'] == g['frames_sha256']
    assert now['pose_lines'] == g['pose_lines']
    assert now['pose_sha256'] == g['pose_sha256']
    assert now['bus_kw'] == g['bus_kw']
    assert {k: now['args'][k] for k in g['args']} == g['args']     # 옛 인자는 이름·기본값·해석 그대로


def test_snapshot_physics_case_really_passes_30kph():
    """제한 없음을 스냅샷이 실제로 건드리는지 — 시험 입력이 30 km/h 를 넘겨야 뜻이 있다."""
    case = CASES['physics_pose']
    bus = _Recorder()
    v = FakeVehicle(bus, physics=True)
    peak = 0.0
    for i in range(300):
        if i == 5:
            bus.rx.append(_msg(0x210, case['stim'][5]))
        v.tick(i * 0.021)
        peak = max(peak, v.kph)
    assert peak > 40.0


# ---- 공용 ---------------------------------------------------------------------------------------------------------
def _msg(arb, data):
    return can.Message(arbitration_id=arb, is_extended_id=False, data=data)


class _Recorder:
    """버스 없이 tick 을 직접 돌리는 시험용."""

    def __init__(self):
        self.rx = []
        self.tx = []

    def recv(self, timeout=None):
        return self.rx.pop(0) if self.rx else None

    def send(self, m):
        self.tx.append(m)

    def last(self, arb):
        return next(bytes(m.data) for m in reversed(self.tx) if m.arbitration_id == arb)

    def count(self, arb):
        return sum(1 for m in self.tx if m.arbitration_id == arb)


def _run(v, bus, seconds, t0=0.0, every=None):
    """seconds 동안 주기 진행. every = 주기마다 넣을 수신 프레임."""
    n = int(round(seconds / v.dt))
    for i in range(n):
        if every is not None:
            bus.rx.append(every)
        v.tick(t0 + i * v.dt)
    return t0 + n * v.dt


def _limit_203(kph):
    return _msg(0x203, DBC.encode_message('USER_service_command', {'speed_limit': kph}))


def _res(on, alive=0):
    return _msg(0x127, DBC.encode_message('KIAPI_RES', {'RES_START': 1 if on else 0, 'alive_count': alive}))


FULL = _msg(0x210, encode_0x210(0, 0, 100, 1, 1, 1))


# ---- 0x202 / 0x203 ------------------------------------------------------------------------------------------------
def test_default_0x202_matches_measured_bytes():
    bus = _Recorder()
    v = FakeVehicle(bus)
    v.tick(0.0)
    assert bus.last(0x202) == bytes.fromhex('2C01000000000000')    # 11-04 실측 2C01...
    assert DBC.decode_message(0x202, bus.last(0x202))['speed_limit_info'] == pytest.approx(30.0)
    assert fv.build_parser().parse_args([]).speed_limit_kph is None and v.speed_limit_kph == pytest.approx(30.0)


def test_0x202_every_period_alongside_old_frames():
    bus = _Recorder()
    _run(FakeVehicle(bus), bus, 0.21)
    assert bus.count(0x200) == bus.count(0x201) == bus.count(0x202) == bus.count(0x128) == 10


def test_default_30_is_report_only_in_physics():
    """30.0 은 원격 조종 제한이지 자율 상한이 아니다 — 0x203 전에는 넘을 수 있어야 한다."""
    bus = _Recorder()
    v = FakeVehicle(bus, physics=True)
    _run(v, bus, 15.0, every=FULL)
    assert v.kph > 35.0
    assert v.speed_limit_kph == pytest.approx(30.0)


def test_0x203_applies_after_latency_and_holds_without_more_frames():
    bus = _Recorder()
    v = FakeVehicle(bus, period_s=0.02, speed_limit_latency_s=0.1)
    v.tick(0.0)
    bus.rx.append(_limit_203(9.3))
    v.tick(0.02)     # 수신 시각 0.02 → 0.12 부터 반영
    assert v.speed_limit_kph == pytest.approx(30.0)
    v.tick(0.10)
    assert DBC.decode_message(0x202, bus.last(0x202))['speed_limit_info'] == pytest.approx(30.0)
    v.tick(0.12)
    assert DBC.decode_message(0x202, bus.last(0x202))['speed_limit_info'] == pytest.approx(9.3)
    _run(v, bus, 2.0, t0=0.14)      # 끊겨도 유지(업체 답변)
    assert DBC.decode_message(0x202, bus.last(0x202))['speed_limit_info'] == pytest.approx(9.3)


def test_0x203_out_of_range_ignored():
    bus = _Recorder()
    v = FakeVehicle(bus)
    bus.rx.append(_msg(0x203, (1001).to_bytes(2, 'little') + bytes(6)))
    v.tick(0.0)
    assert v.speed_limit_kph == pytest.approx(30.0) and v.limit_rejected == 1
    assert not v.limit_enforced


def test_limit_caps_acceleration_in_physics():
    bus = _Recorder()
    v = FakeVehicle(bus, physics=True)
    bus.rx.append(_limit_203(9.3))
    peak = 0.0
    for i in range(500):
        bus.rx.append(FULL)
        v.tick(i * v.dt)
        peak = max(peak, v.kph)
    assert peak <= 9.3 + 1e-6
    assert v.kph == pytest.approx(9.3, abs=0.05)
    frames = [decode_0x201(m.data)['right_kph'] for m in bus.tx if m.arbitration_id == 0x201]
    assert max(frames) <= 9.3 + 1e-6


def test_limit_lowered_while_faster_cuts_drive_without_braking():
    bus = _Recorder()
    v = FakeVehicle(bus, physics=True, initial_kph=30.0)
    bus.rx.append(_limit_203(9.3))
    prev = 30.0
    for i in range(100):
        bus.rx.append(FULL)
        v.tick(i * v.dt)
        assert v.kph <= prev + 1e-9     # 가속 명령에도 올라가지 않는다
        prev = v.kph
    drop = 30.0 - v.kph
    assert 0.0 < drop < 3.0             # 2.1 s 관성(저항)만 — 순간 이동·강제 브레이크 없음


def test_raising_limit_releases_cap():
    bus = _Recorder()
    v = FakeVehicle(bus, physics=True, speed_limit_kph=9.3)
    _run(v, bus, 6.0, every=FULL)
    assert v.kph <= 9.3 + 1e-6
    bus.rx.append(_limit_203(100.0))
    _run(v, bus, 3.0, t0=6.0, every=FULL)
    assert v.kph > 12.0


def test_limit_not_applied_without_physics():
    bus = _Recorder()
    v = FakeVehicle(bus, speed_limit_kph=9.3)
    _run(v, bus, 2.0, every=FULL)
    assert v.kph > 9.3


def test_force_limit_and_ignore_cmd_and_no_service_info():
    bus = _Recorder()
    v = FakeVehicle(bus, ignore_speed_limit_cmd=True, send_service_info=False)
    bus.rx.append(_limit_203(9.3))
    v.tick(0.0)
    assert v.speed_limit_kph == pytest.approx(30.0) and bus.count(0x202) == 0
    v.force_speed_limit(50.0)
    assert v.speed_limit_kph == pytest.approx(50.0) and v.limit_enforced
    with pytest.raises(ValueError):
        FakeVehicle(bus, speed_limit_kph=101.0)


# ---- RES ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('physics', [False, True])
def test_res_ignores_0x210_decelerates_reports_and_releases(physics):
    bus = _Recorder()
    v = FakeVehicle(bus, period_s=0.02, physics=physics, initial_kph=15.0, res_decel_mps2=2.0)
    steer_cmd = _msg(0x210, encode_0x210(40.0, 0, 100, 1, 1, 1))
    bus.rx.append(_res(True))
    t = 0.0
    prev = 15.0
    for _ in range(50):     # 1 s
        bus.rx.append(steer_cmd)
        v.tick(t)
        t += v.dt
        assert v.kph <= prev + 1e-9
        prev = v.kph
    assert v.res_active
    assert v.kph == pytest.approx(15.0 - 2.0 * 3.6 * 1.0, abs=0.2)     # 설정한 감속대로
    assert abs(v.steer) < 1e-9                                          # 조향 명령 무시
    assert decode_0x200(bus.last(0x200))['brake_pos_pct'] > 50          # 차량이 스스로 건 브레이크 보고
    assert DBC.decode_message(0x128, bus.last(0x128))['RES_END'] == 0   # 아직 안 멈춤
    for _ in range(150):    # 정지까지
        bus.rx.append(steer_cmd)
        v.tick(t)
        t += v.dt
    assert v.kph == 0.0
    fb = DBC.decode_message(0x128, bus.last(0x128))
    assert fb['RES_END'] == 1 and fb['RES_ERROR'] == 0

    bus.rx.append(_res(False))
    for _ in range(100):
        bus.rx.append(steer_cmd)
        v.tick(t)
        t += v.dt
    assert v.kph > 1.0                                                   # 해제 뒤 다시 따름
    assert v.steer == pytest.approx(40.0, abs=1.0)
    assert DBC.decode_message(0x128, bus.last(0x128))['RES_END'] == 0
    assert decode_0x200(bus.last(0x200))['brake_pos_pct'] == 0


def test_res_error_reported_and_no_self_brake():
    bus = _Recorder()
    v = FakeVehicle(bus, initial_kph=15.0, res_error=3)
    bus.rx.append(_res(True))
    _run(v, bus, 1.0)
    fb = DBC.decode_message(0x128, bus.last(0x128))
    assert fb['RES_ERROR'] == 3 and fb['RES_END'] == 0
    assert v.kph == pytest.approx(15.0)
    bus.rx.append(_res(False))
    v.tick(1.5)
    assert DBC.decode_message(0x128, bus.last(0x128))['RES_ERROR'] == 0


def test_res_uses_latest_command_on_release_not_pre_res():
    """RES 전 명령이 '가속'이고 RES 중 '정지'로 바뀌었으면 해제 뒤 정지를 따른다."""
    bus = _Recorder()
    v = FakeVehicle(bus, physics=True)
    _run(v, bus, 3.0, every=FULL)
    bus.rx.append(_res(True))
    bus.rx.append(_msg(0x210, encode_0x210(0, 80, 0, 1, 1, 1)))
    v.tick(3.0)
    speed = v.kph
    bus.rx.append(_res(False))
    for i in range(50):
        v.tick(3.02 + i * v.dt)
    assert v.kph < speed


def test_res_frame_loss_keeps_last_value():
    bus = _Recorder()
    v = FakeVehicle(bus, initial_kph=10.0)
    bus.rx.append(_res(True))
    _run(v, bus, 1.0)       # 이후 0x127 없음
    assert v.res_active


def test_res_steer_target_option():
    bus = _Recorder()
    v = FakeVehicle(bus, res_steer_deg=0.0)
    v.steer = 30.0
    bus.rx.append(_res(True))
    _run(v, bus, 2.0)
    assert abs(v.steer) < 1.0


def test_wrong_dlc_ignored():
    bus = _Recorder()
    v = FakeVehicle(bus)
    bus.rx.append(can.Message(arbitration_id=0x127, is_extended_id=False, data=b'\x80'))
    bus.rx.append(can.Message(arbitration_id=0x203, is_extended_id=False, data=b'\x5d\x00'))
    v.tick(0.0)
    assert not v.res_active and v.speed_limit_kph == pytest.approx(30.0)


# ---- 인코딩 대조·in-process API -----------------------------------------------------------------------------------
def test_encoders_match_dbc():
    for kph in (0.0, 9.3, 30.0, 100.0):
        raw = int(round(kph * 10))
        assert DBC.decode_message(0x202, fv.encode_0x202(raw))['speed_limit_info'] == pytest.approx(kph)
        assert fv.encode_0x202(raw) == DBC.encode_message('USER_service_info', {'speed_limit_info': kph})
        assert fv.decode_0x203_raw(DBC.encode_message('USER_service_command', {'speed_limit': kph})) == raw
    for end in (0, 1):
        for err in range(8):
            assert fv.encode_0x128(end, err) == DBC.encode_message('KIAPI_RES_FB', {'RES_END': end, 'RES_ERROR': err})


def test_brake_0x200_raw_is_percent():
    """브레이크 인코딩 불변 — raw 값 = %."""
    bus = _Recorder()
    v = FakeVehicle(bus)
    for i in range(100):
        bus.rx.append(_msg(0x210, encode_0x210(0, 60, 0, 1, 1, 1)))
        v.tick(i * v.dt)
    assert DBC.decode_message(0x200, bus.last(0x200))['break_postion'] == 60


def test_virtual_bus_closed_loop_with_step():
    ch = f'fv_{uuid.uuid4().hex[:8]}'
    tx, car_bus = can.Bus(channel=ch, interface='virtual'), can.Bus(channel=ch, interface='virtual')
    try:
        v = FakeVehicle(car_bus, period_s=0.02, physics=True)
        for i in range(200):        # 가상 시계 4 s
            tx.send(FULL)
            v.step(i * 0.02)
        assert v.kph > 5.0
        tx.send(_limit_203(9.3))
        tx.send(_res(True))
        v.step(4.0)
        assert v.res_active and v.speed_limit_kph == pytest.approx(9.3)
        seen = set()
        while True:
            m = tx.recv(timeout=0)
            if m is None:
                break
            seen.add(m.arbitration_id)
        assert seen == {0x200, 0x201, 0x202, 0x128}
    finally:
        tx.shutdown()
        car_bus.shutdown()


def test_step_catches_up_and_equals_ticks():
    a, b = _Recorder(), _Recorder()
    va, vb = FakeVehicle(a, period_s=0.02), FakeVehicle(b, period_s=0.02)
    assert va.step(0.0) == 1
    assert va.step(0.005) == 0      # 아직 주기 안 됨
    assert va.step(0.2) == 10       # 밀린 주기 모두
    for i in range(11):
        vb.tick(i * 0.02)
    assert [bytes(m.data) for m in a.tx] == [bytes(m.data) for m in b.tx]


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--make-golden':
        out = {'source': 'main() of fake_a1_vehicle.py before race-flag extension (commit d702f63)',
               'cases': {name: _run_main(os.path.abspath(sys.argv[2]), case) for name, case in CASES.items()}}
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=1, ensure_ascii=False, sort_keys=True)
            f.write('\n')
        print('wrote', GOLDEN)
    else:
        sys.exit(pytest.main([__file__, '-q']))


# === r_race.py 조건(WP7): 표·지연·값 바꾸기 창구 =====================================================================

def _cmd_210(acc=0, brake=0):
    d = bytearray(8)
    d[2:4] = int(brake).to_bytes(2, 'little')
    d[4] = int(acc)
    d[5] = 7
    return bytes(d)


def _drive(car, tx, acc, seconds, dt=0.021):
    now = getattr(car, '_test_now', 0.0)
    for _ in range(int(seconds / dt)):
        tx.send(can.Message(arbitration_id=0x210, data=_cmd_210(acc=acc), is_extended_id=False))
        now += dt
        car.tick(now)
    car._test_now = now


def _pair(**kw):
    ch = 'tv' + uuid.uuid4().hex[:8]
    return fv.FakeVehicle(can.Bus(channel=ch, interface='virtual'), physics=True, **kw), can.Bus(channel=ch, interface='virtual')


def test_measured_accel_map_is_weaker_than_default():
    meas = (((0, 75, 100), (0, 0.55, 1.14)))
    base, tx0 = _pair()
    weak, tx1 = _pair(accel_map=meas, drag=(0.57, 0.0, 0.001))
    _drive(base, tx0, 75, 2.0)
    _drive(weak, tx1, 75, 2.0)
    assert base.kph > 10.0      # 기본 표는 75 % 에서 2.4 m/s²
    assert weak.kph < 1.0       # 실측은 75 % 에서 거의 안 움직임


def test_drive_delay_holds_back_acceleration():
    car, tx = _pair(drive_delay_s=0.7)
    _drive(car, tx, 100, 0.6)
    assert car.kph == 0.0
    _drive(car, tx, 100, 0.6)
    assert car.kph > 0.0


def test_ignore_speed_limit_drops_pending_and_later_commands():
    car, tx = _pair(speed_limit_kph=100.0, speed_limit_latency_s=0.05)
    tx.send(can.Message(arbitration_id=0x203, data=(93).to_bytes(2, 'little') + bytes(6), is_extended_id=False))
    car.tick(0.0)
    car.set_ignore_speed_limit(True)
    car.tick(0.1)
    assert car.speed_limit_kph == pytest.approx(100.0)
    tx.send(can.Message(arbitration_id=0x203, data=(93).to_bytes(2, 'little') + bytes(6), is_extended_id=False))
    car.tick(0.2)
    car.tick(0.3)
    assert car.speed_limit_kph == pytest.approx(100.0)
    car.set_ignore_speed_limit(False)
    tx.send(can.Message(arbitration_id=0x203, data=(93).to_bytes(2, 'little') + bytes(6), is_extended_id=False))
    car.tick(0.4)
    car.tick(0.5)
    assert car.speed_limit_kph == pytest.approx(9.3)


def test_cmd_file_applies_once_and_is_removed(tmp_path):
    car, _ = _pair(speed_limit_kph=100.0)
    p = tmp_path / 'cmd'
    p.write_text('set_kph 30\nforce_limit 50\nignore_limit 1\n', encoding='utf-8')
    fv.apply_cmd_file(car, str(p))
    assert car.kph == 30.0 and car.speed_limit_kph == pytest.approx(50.0) and car.ignore_speed_limit_cmd
    assert not p.exists()
    fv.apply_cmd_file(car, str(p))      # 없으면 아무 일 없음
    assert car.kph == 30.0
    p.write_text('nope 1\n', encoding='utf-8')
    with pytest.raises(ValueError):
        fv.apply_cmd_file(car, str(p))
