#!/usr/bin/env python3
"""가짜 대회 서버 — yaml 시나리오대로 0x124(깃발)·0x127(RES)·0x125(피트존)·0x126(수리)을 재생한다(SIL 전용).

신호 방식(계속 켜짐/한 번만)은 대회 측 답변 전이라 미확정이다 → level·pulse·flicker 를 모두 재생할 수 있게 했다.
프레임 정의는 `DBC/A1_dbc_2_fixed.dbc`. 런타임에 DBC 를 읽지 않고 비트로 직접 만든다(can_guard 와 같은 원칙) —
정확성은 test_fake_race_server.py 가 cantools + DBC 로 대조한다.

  python3 sil/vcan/fake_race_server.py sil/vcan/race_scenarios/S02_go.yaml                # vcan0 에 재생
  python3 sil/vcan/fake_race_server.py <yaml> --interface virtual --channel r1             # python-can 가상 버스

같은 프로세스에서 쓸 때(vcan 없는 PC, sil_tests/r_race.py):

  server = RaceServer(can.Bus(channel='r1', interface='virtual'), 'S02_go.yaml')
  server.step(now)   # now 까지 밀린 주기를 모두 송신. 첫 호출이 시나리오 0 초(start 로 미리 정해도 됨)

실차 금지: socketcan 은 이름이 vcan 으로 시작하는 채널만 받는다. 실제 CAN 인터페이스 선택지는 일부러 두지 않았다.

시나리오 yaml 형식
------------------
  name: S03_go_pulse_1frame   # 필수
  desc: ...                   # 설명
  period_ms: 20               # 모든 프레임 주기(기본 20, 대회 엑셀 기준값)
  duration_s: 6.0             # 필수. 이 시각부터 아무것도 보내지 않는다
  initial:                    # 시작 때 프레임을 낼지(기본 둘 다 run)
    race_signal: run          #   0x124, run | stop
    res: run                  #   0x127, run | stop | freeze
  events:                     # 시각 t(시나리오 시작 기준 s) 순서와 무관하게 적어도 된다
    # (1) 신호 비트. flag = go | stop | finish | slow_on | slow_off (0x124) | res (0x127 RES_START), 또는 그 목록
    - {t: 2.0, flag: go, mode: level}                 # 끝까지 켜짐. until: 3.0 이면 그 시각에 꺼짐
    - {t: 2.0, flag: go, mode: pulse, frames: 1}      # 프레임 N 개만 켜짐(시각이 아니라 실제 보낸 프레임 수)
    - {t: 2.0, flag: go, mode: flicker, on_frames: 1, off_frames: 1, repeat: 5}   # 깜빡임. repeat 또는 until 로 끝냄
    - {t: 2.0, flag: [slow_on, slow_off], mode: level, until: 3.0}                # 같은 프레임에 비트 동시
    # (2) 프레임 흐름. stream = race_signal | res
    - {t: 5.0, stream: res, action: stop}             # 0x127 끊김(alive 정지)
    - {t: 5.0, stream: res, action: freeze}           # 0x127 은 계속 오는데 alive_count 만 고정(res 전용)
    - {t: 5.0, stream: race_signal, action: stop}     # 0x124 끊김
    - {t: 7.0, stream: race_signal, action: run}      # 다시 시작
    # (3) 선택 프레임(기본은 버스에 없음). 값 대신 off 면 그 시각부터 끊김
    - {t: 1.0, pit_zone: {lat_deg: 37.0, long_deg: 127.0}}
    - {t: 1.0, repair: 1}
  harness: {expect: ...}      # 재생기는 읽지 않는다 — r_race.py 가 판정 설명으로 쓴다

t 는 프레임 경계로 내림 없이 비교한다: t 이상인 첫 프레임부터 적용. 같은 t 의 stream 사건은 파일 순서대로 적용.
"""
import argparse
import os
import signal
import sys
import time

import can
import yaml

FRAME_RACE_SIGNAL = 0x124   # KIAPI_Race_Signal
FRAME_PIT_ZONE = 0x125      # KIAPI_PIT_ZONE
FRAME_REPAIR = 0x126        # KIAPI_REPAIR
FRAME_RES = 0x127           # KIAPI_RES

RACE_FLAG_BITS = {'go': 0x80, 'stop': 0x40, 'finish': 0x20, 'slow_on': 0x10, 'slow_off': 0x08}  # 0x124 byte0 (DBC 7~3)
RES_BIT = 0x80              # 0x127 byte0, RES_START 7/1
ALL_FLAGS = (*RACE_FLAG_BITS, 'res')
LATLONG_SCALE = 1e-7        # 0x125 °/raw
EPS_S = 1e-9                # 부동소수 누적 오차로 프레임이 한 칸 밀리지 않게


def encode_race_signal(flags=()):
    """0x124 8바이트. flags = 켜진 깃발 이름들."""
    b = 0
    for f in flags:
        b |= RACE_FLAG_BITS[f]
    return bytes([b]) + bytes(7)


def encode_res(res_start, alive_count):
    """0x127 8바이트. RES_START 7/1, alive_count 56/8."""
    d = bytearray(8)
    d[0] = RES_BIT if res_start else 0
    d[7] = alive_count & 0xFF
    return bytes(d)


def encode_pit_zone(lat_deg, long_deg):
    """0x125 8바이트. PITZONE_LAT 0/32, PITZONE_LONG 32/32 (1e-7 °/raw, unsigned, Intel)."""
    lat, lon = int(round(lat_deg / LATLONG_SCALE)), int(round(long_deg / LATLONG_SCALE))
    if not (0 <= lat <= 0xFFFFFFFF and 0 <= lon <= 0xFFFFFFFF):
        raise ValueError(f'피트존 좌표 범위 밖: {lat_deg}, {long_deg}')
    return lat.to_bytes(4, 'little') + lon.to_bytes(4, 'little')


def encode_repair(value):
    """0x126 8바이트. SIG_REPAIR 0/8 (뜻 미정의, 기록용)."""
    if not 0 <= int(value) <= 0xFF:
        raise ValueError(f'SIG_REPAIR 범위 밖: {value}')
    return bytes([int(value)]) + bytes(7)


def _fail(where, why):
    raise ValueError(f'시나리오 오류({where}): {why}')


def _check_keys(where, d, allowed):
    extra = set(d) - set(allowed)
    if extra:
        _fail(where, f'모르는 키 {sorted(extra)} (허용: {sorted(allowed)})')


def _flag_event(where, e):
    _check_keys(where, e, ('t', 'flag', 'mode', 'until', 'frames', 'on_frames', 'off_frames', 'repeat'))
    flags = e['flag'] if isinstance(e['flag'], (list, tuple)) else [e['flag']]
    for f in flags:
        if f not in ALL_FLAGS:
            _fail(where, f'모르는 flag {f!r} (허용: {ALL_FLAGS})')
    mode = e.get('mode', 'level')
    out = {'t': float(e['t']), 'flags': list(flags), 'mode': mode, 'until': e.get('until')}
    if out['until'] is not None:
        out['until'] = float(out['until'])
        if out['until'] <= out['t']:
            _fail(where, 'until 은 t 보다 커야 한다')
    if mode == 'level':
        for k in ('frames', 'on_frames', 'off_frames', 'repeat'):
            if k in e:
                _fail(where, f'level 에는 {k} 를 쓰지 않는다')
    elif mode == 'pulse':
        if int(e.get('frames', 0)) < 1:
            _fail(where, 'pulse 는 frames ≥ 1 필요')
        out['frames'] = int(e['frames'])
    elif mode == 'flicker':
        out['on'], out['off'] = int(e.get('on_frames', 0)), int(e.get('off_frames', 0))
        if out['on'] < 1 or out['off'] < 1:
            _fail(where, 'flicker 는 on_frames·off_frames ≥ 1 필요')
        out['repeat'] = int(e['repeat']) if 'repeat' in e else None
        if out['repeat'] is None and out['until'] is None:
            _fail(where, 'flicker 는 repeat 또는 until 필요')
    else:
        _fail(where, f'모르는 mode {mode!r} (level | pulse | flicker)')
    return out


def load_scenario(source):
    """yaml 경로 또는 dict → 검증·정규화한 dict. 오타를 조용히 넘기면 시험이 헛돈다 → 모르는 키는 ValueError."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, encoding='utf-8') as f:
            raw = yaml.safe_load(f)
    else:
        raw = source
    if not isinstance(raw, dict):
        _fail('최상위', 'dict 가 아니다')
    _check_keys('최상위', raw, ('name', 'desc', 'period_ms', 'duration_s', 'initial', 'events', 'harness'))
    for k in ('name', 'duration_s'):
        if k not in raw:
            _fail('최상위', f'{k} 필수')
    period_s = float(raw.get('period_ms', 20)) / 1000.0
    duration_s = float(raw['duration_s'])
    if period_s <= 0 or duration_s <= 0:
        _fail('최상위', 'period_ms·duration_s 는 양수')
    initial = dict(raw.get('initial') or {})
    _check_keys('initial', initial, ('race_signal', 'res'))
    if initial.get('race_signal', 'run') not in ('run', 'stop'):
        _fail('initial.race_signal', 'run | stop')
    if initial.get('res', 'run') not in ('run', 'stop', 'freeze'):
        _fail('initial.res', 'run | stop | freeze')

    flags, streams, optional = [], [], []
    for i, e in enumerate(raw.get('events') or []):
        where = f'events[{i}]'
        if not isinstance(e, dict) or 't' not in e:
            _fail(where, 't 필수')
        t = float(e['t'])
        if not 0 <= t < duration_s:
            _fail(where, f't={t} 가 재생 구간 [0, {duration_s}) 밖')
        kinds = [k for k in ('flag', 'stream', 'pit_zone', 'repair') if k in e]
        if len(kinds) != 1:
            _fail(where, 'flag | stream | pit_zone | repair 중 정확히 하나')
        if kinds[0] == 'flag':
            flags.append(_flag_event(where, e))
        elif kinds[0] == 'stream':
            _check_keys(where, e, ('t', 'stream', 'action'))
            if e['stream'] not in ('race_signal', 'res'):
                _fail(where, f"모르는 stream {e['stream']!r}")
            allowed = ('run', 'stop', 'freeze') if e['stream'] == 'res' else ('run', 'stop')
            if e.get('action') not in allowed:
                _fail(where, f"{e['stream']} 의 action 은 {allowed}")
            streams.append({'t': t, 'stream': e['stream'], 'action': e['action']})
        else:
            key = kinds[0]
            _check_keys(where, e, ('t', key))
            v = e[key]
            if v == 'off' or v is False:    # yaml 1.1 은 따옴표 없는 off 를 False 로 읽는다
                data = None
            elif key == 'pit_zone':
                if not isinstance(v, dict):
                    _fail(where, 'pit_zone 은 {lat_deg, long_deg} 또는 off')
                _check_keys(where, v, ('lat_deg', 'long_deg'))
                data = encode_pit_zone(float(v['lat_deg']), float(v['long_deg']))
            else:
                data = encode_repair(v)
            optional.append({'t': t, 'key': key, 'data': data})
    streams.sort(key=lambda s: s['t'])     # 안정 정렬: 같은 t 는 파일 순서
    optional.sort(key=lambda s: s['t'])
    return {'name': str(raw['name']), 'desc': raw.get('desc', ''), 'period_s': period_s, 'duration_s': duration_s,
            'initial': {'race_signal': initial.get('race_signal', 'run'), 'res': initial.get('res', 'run')},
            'flags': flags, 'streams': streams, 'optional': optional, 'harness': raw.get('harness') or {}}


class _FlagEvent:
    """깃발 사건 하나의 재생 상태. 프레임을 실제로 보낼 때만 부른다(pulse·flicker 가 프레임 수 기준이 되도록)."""

    def __init__(self, spec):
        self.spec = spec
        self.sent = 0

    def active(self, s):
        sp = self.spec
        if s < sp['t'] - EPS_S:
            return False
        if sp['until'] is not None and s >= sp['until'] - EPS_S:
            return False
        p, self.sent = self.sent, self.sent + 1
        if sp['mode'] == 'level':
            return True
        if sp['mode'] == 'pulse':
            return p < sp['frames']
        cycle = sp['on'] + sp['off']
        if sp['repeat'] is not None and p >= sp['repeat'] * cycle:
            return False
        return p % cycle < sp['on']


class RaceServer:
    """시나리오 재생기. 시각을 호출자가 주므로 가상 시계로도 돈다.

    step(now): now 까지 밀린 주기의 프레임을 모두 보내고 그 can.Message 목록을 돌려준다.
      호출 빈도와 상관없이 같은 프레임 열이 나온다(주기 k 의 시각 = 시작 + k × period).
    finished(now): 재생 구간이 끝났는지.
    """

    def __init__(self, bus, scenario, start=None):
        self.bus = bus
        self.scenario = load_scenario(scenario) if not isinstance(scenario, dict) or 'flags' not in scenario else scenario
        self.period_s = self.scenario['period_s']
        self.duration_s = self.scenario['duration_s']
        self.name = self.scenario['name']
        self.t0 = start
        self._k = 0
        self._alive = 0
        self._race_on = self.scenario['initial']['race_signal']
        self._res_on = self.scenario['initial']['res']
        self._opt_data = {'pit_zone': None, 'repair': None}
        self._stream_i = 0
        self._opt_i = 0
        self._race_ev = []
        self._res_ev = []
        for sp in self.scenario['flags']:     # 한 사건의 비트를 프레임별로 나눈다(res 는 0x127 프레임 수로 센다)
            race = [f for f in sp['flags'] if f != 'res']
            if race:
                self._race_ev.append((_FlagEvent(sp), race))
            if len(race) != len(sp['flags']):
                self._res_ev.append(_FlagEvent(sp))

    def start(self, now):
        self.t0 = now

    def finished(self, now):
        return self.t0 is not None and now - self.t0 >= self.duration_s - EPS_S

    def step(self, now):
        if self.t0 is None:
            self.t0 = now
        out = []
        while True:
            s = self._k * self.period_s
            if s >= self.duration_s - EPS_S or self.t0 + s > now + EPS_S:
                return out
            self._k += 1
            self._cycle(s, out)

    def _send(self, arb_id, data, s, out):
        m = can.Message(arbitration_id=arb_id, is_extended_id=False, data=data, timestamp=self.t0 + s)
        self.bus.send(m)
        out.append(m)

    def _cycle(self, s, out):
        sc = self.scenario
        while self._stream_i < len(sc['streams']) and sc['streams'][self._stream_i]['t'] <= s + EPS_S:
            ev = sc['streams'][self._stream_i]
            self._stream_i += 1
            if ev['stream'] == 'race_signal':
                self._race_on = ev['action']
            else:
                self._res_on = ev['action']
        while self._opt_i < len(sc['optional']) and sc['optional'][self._opt_i]['t'] <= s + EPS_S:
            ev = sc['optional'][self._opt_i]
            self._opt_i += 1
            self._opt_data[ev['key']] = ev['data']

        if self._race_on == 'run':
            on = set()
            for ev, flags in self._race_ev:
                if ev.active(s):
                    on.update(flags)
            self._send(FRAME_RACE_SIGNAL, encode_race_signal(on), s, out)
        if self._opt_data['pit_zone'] is not None:
            self._send(FRAME_PIT_ZONE, self._opt_data['pit_zone'], s, out)
        if self._opt_data['repair'] is not None:
            self._send(FRAME_REPAIR, self._opt_data['repair'], s, out)
        if self._res_on != 'stop':
            res = False
            for ev in self._res_ev:     # any() 금지: 모든 사건의 프레임 수를 세야 한다
                res = ev.active(s) or res
            self._send(FRAME_RES, encode_res(res, self._alive), s, out)
            if self._res_on == 'run':
                self._alive = (self._alive + 1) & 0xFF


_stop = False


def _on_signal(signum, frame):
    global _stop
    _stop = True


def main(argv=None):
    global _stop
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scenario', help='시나리오 yaml')
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--interface', default='socketcan', choices=('socketcan', 'virtual'))
    ap.add_argument('--quiet', action='store_true', help='값이 바뀐 프레임 출력 끄기')
    a = ap.parse_args(argv)
    if a.interface == 'socketcan' and not a.channel.startswith('vcan'):
        print(f'[fake_race_server] 거부: {a.channel} — 가상 버스(vcan*)에서만 돈다', file=sys.stderr)
        return 2
    _stop = False
    try:
        signal.signal(signal.SIGTERM, _on_signal)
        signal.signal(signal.SIGINT, _on_signal)
    except ValueError:      # 주 스레드가 아님(시험)
        pass
    scenario = load_scenario(a.scenario)
    bus = can.Bus(channel=a.channel, interface=a.interface)
    server = RaceServer(bus, scenario)
    print(f"[fake_race_server] {a.channel} 재생 시작: {server.name} ({server.duration_s:g} s)", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    server.start(t0)
    last = {}
    try:
        while not _stop and not server.finished(time.monotonic()):
            now = time.monotonic()
            for m in server.step(now):
                if not a.quiet and last.get(m.arbitration_id, (None,))[0] != bytes(m.data)[:7]:
                    print(f'  t={m.timestamp - t0:7.3f} 0x{m.arbitration_id:03X} {bytes(m.data).hex()}',
                          file=sys.stderr, flush=True)
                last[m.arbitration_id] = (bytes(m.data)[:7],)    # 0x127 의 alive(마지막 바이트)는 변화로 치지 않는다
            time.sleep(max(0.0, min(server.period_s, server.t0 + server._k * server.period_s - time.monotonic())))
    finally:
        bus.shutdown()
    print('[fake_race_server] 종료', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
