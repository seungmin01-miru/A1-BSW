#!/usr/bin/env python3
"""경기 깃발 SIL 자동 판정(계획서 WP7, S1~S18) — 실제 can_guard 를 돌리고 **버스의 0x210·0x203 프레임 + can_guard 이벤트 로그**로 판정한다.

  python3 sil_tests/r_race.py                                   # virtual: 같은 프로세스, vcan 없는 PC(개발 PC 참고용)
  python3 sil_tests/r_race.py --with-ros                        # + 실제 cmd_adapter·race_state_publisher 경로(S6r, S14 어댑터, S15r, W5)
  python3 sil_tests/r_race.py --interface socketcan --channel vcan0   # 대회 PC: can_guard·서버·차량을 별도 프로세스로. vcan* 만 받는다
  python3 sil_tests/r_race.py --only S05,S09 --rounds 1 --out /tmp/r
  python3 sil_tests/r_race.py --print-commands S16              # 프로세스 모드 명령만 찍고 끝(아무것도 실행 안 함)

**차량·실제 CAN 에 닿지 않는다.** socketcan 은 `vcan*` 채널만 받고 그 밖은 종료 코드 2.
벽시계로 돈다. 개발 PC 결과는 로직 확인용이고 지연 판정은 대회 PC 로(CLAUDE.md). 합격 기준은 계획서 11절 WP7 표 그대로다.

시나리오: sil/vcan/race_scenarios/*.yaml. 모델은 어댑터 없이 CommandChannel 에 직접 쓴다(can_guard `--adapter-hold-after-s 0`, 끊긴 시간 = 명령 나이).
`--with-ros` 면 S6·S14·S15 와 "/race/state 가 실제 can_guard 를 따라감"(WP5 관문)을 실제 노드로 돌린다: 모델 → /control/target → cmd_adapter → shm → can_guard.
ROS 쪽 시나리오 시계는 어댑터가 자동 arm 한 뒤부터 잰다(DDS·노드 기동 시간을 뺌). 0x200 대역: 이 스크립트가 /control/status/control_info 를 낸다.

결과: 시나리오마다 자식 프로세스 하나(`--one`, can_guard 종료 플래그·신호가 전역이라 나눈다). `--out` 에 README.md·rounds.csv·csv/·figs/.
"""
import argparse
import concurrent.futures as cf
import csv
import json
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD_DIR = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(GUARD_DIR, '..', '..'))
SIL_DIR = os.path.join(REPO, 'sil', 'vcan')
SCEN_DIR = os.path.join(SIL_DIR, 'race_scenarios')
for _p in (GUARD_DIR, SIL_DIR, os.path.join(REPO, 'tools', 'race_day')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import can  # noqa: E402

import fake_a1_vehicle as fav  # noqa: E402
import fake_race_server as frs  # noqa: E402
from a1_proto import decode_0x210  # noqa: E402
from protocol import Command, CommandChannel, HeartbeatChannel, StatusChannel, TornReadError  # noqa: E402
from recovery import RecoveryParams, budget_gap_s  # noqa: E402
from rx_decode import decode_vehicle_speed_kph  # noqa: E402

GO, STOP, FINISH, SLOW_ON, SLOW_OFF = 0x80, 0x40, 0x20, 0x10, 0x08
TRUE_RATIO = 0.958          # ★ 11-04 실측: 0x201 ÷ GNSS (measurements/2026-11-04_drive_log/README.md 2.4절)
LIMIT_KPH = 10.0            # 규정 서행 상한(R-03·R-04)
ACC_CUT, BRAKE_AT, TARGET = 8.8, 9.3, 8.0
JITTER_S = 0.08             # 시각 비교 여유: 20 ms 주기 2번 + 스케줄링. 판정 숫자가 아니라 "그 주기"를 찾기 위한 창

# ★ 11-04 실측에서 낸 --physics 표(measurements/2026-11-04_drive_log/README.md 5.2·5.3, 0~10 km/h 순가속 + 타행분). 잠정
MEASURED = {'accel_map': ((0, 50, 72.5, 77.5, 82.5, 87.5, 92.5, 100), (0, 0.17, 0.41, 0.55, 0.58, 0.83, 0.90, 1.14)),
            'brake_map': ((0, 35, 45, 55, 82), (0, 0, 0.3, 1.35, 2.0)),
            'drag': (0.57, 0.0, 0.001)}     # long_coast.csv "fit all"
LAUNCH_DELAY_S = 0.7        # 11-04 출발 지연 실측 0.64~0.74 s (quick_lon.md)

MO = namedtuple('MO', 'acc brake steer accel')  # 가속 %, 브레이크 %, 핸들 °, ROS 경로용 m/s²


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def m_const(acc=100.0, steer=0.0, accel=40.0):
    return lambda t, v: MO(acc, 0.0, steer, accel)


def m_hold(target, ff, k=15.0):
    """속도 유지. accel 은 어댑터 경로용(track.yaml 표: 0.1 → 75 %, 0.57 → 100 %)."""
    def fn(t, v):
        err = target - v
        return MO(clamp(ff + k * err, 0.0, 100.0), 0.0, 0.0, clamp(0.2 + 0.8 * err / 3.6, -1.5, 0.8))
    return fn


def with_gap(fn, t0, t1):
    """[t0, t1) 동안 입력 없음(None)."""
    return lambda t, v: None if t0 <= t < t1 else fn(t, v)


def m_finish(t_stop, target=25.0):
    hold = m_hold(target, 10.0)

    def fn(t, v):
        return hold(t, v) if t < t_stop else MO(0.0, 70.0, 0.0, -2.0)
    return fn


class Case:
    """시나리오 하나 = 자식 프로세스 하나."""

    def __init__(self, cid, yaml_name, title, criterion, judge, model=None, guard=(), vehicle=None, events=(), ros=False,
                 hold_after=0.0, params=None, csv_keep=True, duration=None, extra_events=()):
        self.id, self.yaml, self.title, self.criterion, self.judge = cid, yaml_name, title, criterion, judge
        self.model = model if model is not None else m_const()
        self.guard, self.vehicle, self.events, self.ros = list(guard), dict(vehicle or {}), list(events), ros
        self.hold_after, self.params, self.csv_keep, self.duration = hold_after, params or {}, csv_keep, duration
        self.extra_events = list(extra_events)   # 시나리오 yaml 에 깃발 사건 더하기(W5 처럼 코드로 만드는 경우)


# ─── 프레임·이벤트 ─────────────────────────────────────────────────────────────────────────────

class Recorder(threading.Thread):
    """버스의 모든 프레임을 (벽시계, id, 8바이트) 로 받아 둔다. 시각은 프레임 타임스탬프(virtual·socketcan 모두 epoch)."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus = bus
        self.frames = []
        self.stop_evt = threading.Event()
        self.kph = 0.0
        self.steer = 0.0

    def run(self):
        while not self.stop_evt.is_set():
            m = self.bus.recv(timeout=0.05)
            if m is None:
                continue
            d = bytes(m.data)
            self.frames.append((m.timestamp, m.arbitration_id, d))
            if m.arbitration_id == 0x201 and len(d) == 8:
                self.kph = decode_vehicle_speed_kph(d)
            elif m.arbitration_id == 0x200 and len(d) == 8:
                self.steer = int.from_bytes(d[0:2], 'little', signed=True) * 0.1


class Run:
    """한 시나리오의 결과. 시각 t 는 시나리오 시작 기준 s."""

    def __init__(self, frames, t0_wall, event_path, rcs, status, ros=None, silent=None, error=None):
        self.rcs, self.status, self.ros, self.silent, self.error = rcs, status, ros or {}, silent or [], error
        self.tx, self.v, self.f202, self.f203, self.f124, self.f127, self.f128, self.steer200 = [], [], [], [], [], [], [], []
        for ts, aid, d in frames:
            t = ts - t0_wall
            if aid == 0x210:
                c = decode_0x210(d)
                auto = c['steer_auto'] | c['brake_auto'] << 1 | c['acc_auto'] << 2
                self.tx.append((t, c['steer_deg'], c['brake_pct'], c['acc_pct'], auto))
            elif aid == 0x201:
                self.v.append((t, decode_vehicle_speed_kph(d)))
            elif aid == 0x202:
                self.f202.append((t, int.from_bytes(d[:2], 'little') & 0x3FF))
            elif aid == 0x203:
                self.f203.append((t, int.from_bytes(d[:2], 'little') & 0x3FF))
            elif aid == 0x124:
                self.f124.append((t, d[0] if d else 0))
            elif aid == 0x127:
                self.f127.append((t, d[0] & 0x80, d[7]))
            elif aid == 0x128:
                self.f128.append((t, d[0]))
        self.events = []
        if os.path.exists(event_path):
            with open(event_path, newline='') as f:
                for r in csv.DictReader(f):
                    if r.get('event'):
                        self.events.append((float(r['wall_s']) - t0_wall, r['event'], r['detail'] or ''))

    def ev(self, name, text=''):
        return [e for e in self.events if e[1] == name and text in e[2]]

    def ev_t(self, name, text=''):
        e = self.ev(name, text)
        return e[0][0] if e else None

    def state_t(self, to):
        """안전 축이 to 로 처음 들어간 시각."""
        return self.ev_t('state', f'->{to} ')

    def tx_in(self, a=-1e9, b=1e9):
        return [f for f in self.tx if a <= f[0] < b]

    def v_in(self, a=-1e9, b=1e9):
        return [x for x in self.v if a <= x[0] < b]

    def v_at(self, t):
        out = None
        for tt, v in self.v:
            if tt > t:
                break
            out = v
        return out

    def flag_t(self, bit, n=1):
        """깃발 비트가 켜진 n 번째 프레임 시각."""
        seen = [t for t, b in self.f124 if b & bit]
        return seen[n - 1] if len(seen) >= n else None


class J:
    """판정 항목 모음."""

    def __init__(self):
        self.items = []

    def __call__(self, label, ok, value=''):
        self.items.append((label, bool(ok), str(value)))
        return bool(ok)

    @property
    def ok(self):
        return all(i[1] for i in self.items)

    def text(self):
        return ' · '.join(f'{"" if i[1] else "✗ "}{i[0]}{" " + i[2] if i[2] else ""}' for i in self.items)


def common(run, j, tx_needed=True):
    """모든 경기 시나리오 공통: 내부 예외 없음, auto 000 없음."""
    if run.error:
        j('실행 오류', False, run.error)
    bad = [f for f in run.tx if f[4] != 7]
    j('auto 111 외 프레임', not bad, f'{len(bad)}회')
    errs = run.ev('race_internal_error')
    if errs:
        j('race_internal_error', False, errs[0][2])
    if tx_needed:
        j('0x210 수', len(run.tx) > 0, f'{len(run.tx)}')


def no_latch(run, j):
    bad = [e[2] for e in run.ev('state') if '->STOPPED' in e[2] or '->ESTOP' in e[2]]
    j('STOPPED·ESTOP', not bad, f'{len(bad)}회')


def not_suspended(run, j):
    bad = run.ev('race_phase', '->SUSPENDED')
    j('SUSPENDED', not bad, f'{len(bad)}회')


def gap_max(run, a=-1e9, b=1e9):
    frames = run.tx_in(a, b)
    return max((y[0] - x[0] for x, y in zip(frames, frames[1:])), default=0.0)


# ─── 판정 ─────────────────────────────────────────────────────────────────────────────────────

def j_s01(run, j):
    common(run, j)
    j('0x210 가속 최대', max(f[3] for f in run.tx) == 0, f'{max(f[3] for f in run.tx)} %')
    j('브레이크(잠정 30 %)', all(f[2] == 30 for f in run.tx), f'{min(f[2] for f in run.tx)}~{max(f[2] for f in run.tx)} %')
    j('차속 최대', max(v for _, v in run.v) == 0.0, f'{max(v for _, v in run.v):.1f} km/h')
    no_latch(run, j)
    j('경기 상태 전이', not run.ev('race_phase'), f'{len(run.ev("race_phase"))}회')


def _pre_go_clean(run, j, until, label='Go 전'):
    frames = run.tx_in(b=until)
    j(f'{label} 가속 최대', all(f[3] == 0 for f in frames), f'{max((f[3] for f in frames), default=0)} %')


def j_s02(run, j):
    common(run, j)
    t1, t2 = run.flag_t(GO, 1), run.flag_t(GO, 2)
    if not j('Go 2프레임 버스 확인', t2 is not None, ''):
        return
    _pre_go_clean(run, j, t2 - 0.005)
    first = next((f for f in run.tx if f[3] == 100 and f[0] >= t2 - 0.005), None)
    if not j('모델 명령 전달', first is not None, ''):
        return
    passed = len(run.tx_in(t2, first[0]))
    j('Go 확인 뒤 지나간 0x210', passed <= 1, f'{passed}프레임')
    j('지연(개발 PC 참고)', True, f'{(first[0] - t2) * 1e3:.1f} ms')
    j('race_phase', bool(run.ev('race_phase', 'PRE_GO->RACING go')), '')
    j.extra = {'go_confirm_t': t2, 'first_cmd_t': first[0]}


def j_s02b(run, j):
    common(run, j)
    j('go_level_at_start 기록', bool(run.ev('race_signal', 'go_level_at_start')), '')
    t2 = next((t for t, b in run.f124 if b & GO and t >= 4.5), None)
    _pre_go_clean(run, j, 5.0, '5 s 전(옛 Go 중)')
    tr = run.ev_t('race_phase', 'PRE_GO->RACING')
    j('RACING 시각(0 뒤 상승 5 s)', tr is not None and t2 is not None and tr >= 5.0, f'{tr:.3f} s' if tr else '없음')
    j('Go 뒤 모델 전달', any(f[3] == 100 for f in run.tx_in(5.0)), '')


def j_s03(run, j):
    common(run, j)
    pulses = [t for t, b in run.f124 if b & GO]
    j('버스 Go 프레임', len(pulses) >= 1, f'{len(pulses)}개')
    j('RACING 전이', not run.ev('race_phase', '->RACING'), f'{len(run.ev("race_phase", "->RACING"))}회')
    j('가속 최대', all(f[3] == 0 for f in run.tx), f'{max(f[3] for f in run.tx)} %')
    e = run.ev('race_signal_ignored', 'go')
    j('이벤트 기록(race_signal_ignored)', bool(e), e[0][2] if e else '없음')
    no_latch(run, j)


def j_s04(run, j):
    common(run, j)
    tr = run.ev_t('race_phase', 'PRE_GO->RACING go')
    j('RACING 진입', tr is not None, f'{tr:.3f} s' if tr else '없음')
    last_go = [t for t, b in run.f124 if b & GO]
    j('Go 프레임', len(last_go) == 2, f'{len(last_go)}개')
    after = run.tx_in(last_go[-1] + 0.1) if last_go else []
    j('비트 내려간 뒤 모델 명령 유지', after and all(f[3] == 100 for f in after), f'{len(after)}프레임')
    not_suspended(run, j)


def s5_rows(run, j, res):
    """S5: 0x201 기준·실제 속도 추정 ★ 두 줄을 따로 낸다."""
    common(run, j)
    t0 = run.flag_t(SLOW_ON, 2)
    if not j('Slow On 확인', t0 is not None, ''):
        return
    v0 = run.v_at(t0)
    j('Slow On 때 30 km/h 근처', v0 is not None and abs(v0 - 30.0) <= 3.0, f'{v0:.1f} km/h')
    series = run.v_in(t0)
    reach = next((t for t, v in series if v <= BRAKE_AT), None)
    if not j('9.3 이하 도달', reach is not None, '없음' if reach is None else f'{reach - t0:.2f} s'):
        return
    after = [(t, v) for t, v in series if t >= reach]
    tail = [v for t, v in after if t >= reach + 3.0]
    peak = max(v for _, v in after)
    n10 = sum(1 for _, v in after if v > LIMIT_KPH)
    n10t = sum(1 for _, v in after if v / TRUE_RATIO > LIMIT_KPH)
    j('10 초과(0x201)', n10 == 0, f'{n10}회, 최고 {peak:.2f} km/h')
    j2 = J()
    j2('10 초과(실제 추정 ★ ÷0.958)', n10t == 0, f'{n10t}회, 최고 {peak / TRUE_RATIO:.2f} km/h')
    j2('9.3 이하 도달(실제 추정 ★ 기준 0x201 ≤ 8.91)', True,
       f'{next((t for t, v in series if v / TRUE_RATIO <= BRAKE_AT), t0) - t0:.2f} s')
    res['t_reach_s'] = reach - t0
    res['peak_0x201'] = peak
    res['peak_true'] = peak / TRUE_RATIO
    res['tail_mean'] = sum(tail) / len(tail) if tail else None
    res['tail_min'] = min(tail) if tail else None
    res['n_over'] = n10
    res['n_over_true'] = n10t
    j('정상 구간 평균·최저(개발 PC 참고)', bool(tail), f'{res["tail_mean"]:.2f} / {res["tail_min"]:.2f} km/h' if tail else '없음')
    no_latch(run, j)
    j.second = j2


def j_s05(run, j, res):
    s5_rows(run, j, res)


def j_s06(run, j, res=None):
    common(run, j)
    t0 = run.flag_t(SLOW_ON, 2)
    if not j('Slow On 확인', t0 is not None, ''):
        return
    v0 = run.v_at(t0)
    series = run.v_in(t0)
    reach = next((t for t, v in series if v <= BRAKE_AT), None)
    if not j('9.3 이하 도달', reach is not None, '없음' if reach is None else f'{reach - t0:.2f} s(진입 {v0:.1f} km/h)'):
        return
    after = [v for t, v in series if t >= reach]
    tail = [v for t, v in series if t >= reach + 3.0]
    j('10 초과', all(v <= LIMIT_KPH for v in after), f'{sum(v > LIMIT_KPH for v in after)}회, 최고 {max(after):.2f} km/h')
    j('정지 안 함(3 km/h 초과 유지)', min(tail) > 3.0, f'최저 {min(tail):.2f}, 평균 {sum(tail) / len(tail):.2f} km/h')
    no_latch(run, j)
    hold = run.ev('state', '->HOLDING')
    j('HOLDING', not hold, f'{len(hold)}회')
    if run.ros:
        _ros_latch_checks(run, j)


def _ros_latch_checks(run, j, a=0.0, b=1e9):
    log = [x for x in run.ros.get('adapter', []) if a <= x[0] < b]
    j('어댑터 상태 수신', bool(log), f'{len(log)}개')
    faults = {x[4] for x in log if x[4] and '끊김' not in x[4] and 'D7' not in x[4]}
    j('어댑터 고장 사유', not faults, '/'.join(sorted(faults)) or '없음')
    if log:
        j('어댑터 거부 입력 증가', log[-1][5] == log[0][5], f'{log[-1][5] - log[0][5]}개')


def j_s07(run, j):
    common(run, j)
    t0 = run.ev_t('slow_off')
    if not j('slow_off 이벤트', t0 is not None, ''):
        return
    start = (run.tx_in(b=t0) or [(0, 0, 0, 0, 0)])[-1][3]
    frames = run.tx_in(t0)
    over = max((f[3] - (start + 50.0 * (f[0] - t0)) for f in frames if f[3] < 100), default=0.0)
    # 1 주기 위상 + 정수 반올림 여유
    j('가속이 램프 위로 넘은 최대', over <= 50.0 * JITTER_S + 1.0, f'{over:.1f} %')
    first = next((f for f in frames if f[3] >= 100), None)
    expect = (100.0 - start) / 50.0
    j('100 % 도달(잠정 50 %/s)', first is not None and abs(first[0] - t0 - expect) <= JITTER_S + 0.02,
      f'{first[0] - t0:.2f} s(계산 {expect:.2f} s, 시작 {start} %)' if first else '없음')
    mono = all(y[3] >= x[3] for x, y in zip(frames, frames[1:]))
    j('가속 단조 증가', mono, '')
    grace = [t for t, s in ((x[0], x) for x in run.status) if t >= t0 and s[3]]
    t_end = max(grace) if grace else None
    j('입력 유예', t_end is not None and abs(t_end - t0 - 1.0) <= JITTER_S, f'{t_end - t0:.2f} s' if grace else '상태 없음')
    j.extra = {'ramp_t0': t0, 'ramp_start': start}


def j_s08(run, j):
    common(run, j)
    tf = run.ev_t('race_phase', 'RACING->FINISHED')
    tp = run.ev_t('race_phase', 'FINISHED->PARKED')
    j('FINISHED', tf is not None, f'{tf:.2f} s' if tf else '없음')
    if not j('PARKED', tp is not None, f'{tp:.2f} s' if tp else '없음'):
        return
    moving = [t for t, v in run.v_in(tf, tp) if v > 0.5]
    last = max(moving) if moving else tf
    j('정지 뒤 PARKED(잠정 5 s)', abs(tp - last - 5.0) <= JITTER_S + 0.05, f'{tp - last:.2f} s')
    after = run.tx_in(tp + JITTER_S)
    j('PARKED 뒤 가속 0·브레이크 60', after and all(f[3] == 0 and f[2] == 60 for f in after), f'{len(after)}프레임')
    j('0x210 끊김(침묵 없음) 최대', gap_max(run, tf) < 0.1, f'{gap_max(run, tf) * 1e3:.0f} ms')


def j_s09(run, j):
    common(run, j)
    res_on = [t for t, b, _ in run.f127 if b]
    if not j('버스 RES', len(res_on) > 2, f'{len(res_on)}프레임'):
        return
    t_on, t_off = res_on[0], res_on[-1]
    te = run.state_t('ESTOP')
    j('ESTOP 래치', te is not None, f'{te - t_on:.3f} s 뒤' if te else '없음')
    j('estop_request race_res', bool(run.ev('estop_request', 'race_res reason=res')), '')
    if te is None:
        return
    j('ESTOP 해제 없음', not run.ev('state', 'ESTOP->'), '')
    after = run.tx_in(t_off + 2 * JITTER_S)
    j('해제 후 가속 0', all(f[3] == 0 for f in after), f'{len(after)}프레임')
    j('해제 후 브레이크 ≥ 30·감소 없음', all(f[2] >= 30 for f in after) and all(y[2] >= x[2] for x, y in zip(after, after[1:])),
      f'{after[0][2]}→{after[-1][2]} %' if after else '')
    vs = [v for _, v in run.v_in(t_off)]
    rise = max((y - x for x, y in zip(vs, vs[1:])), default=0.0)
    j('해제 뒤 튀어 나감(차속 상승 최대)', rise <= 0.1 + 1e-6, f'{rise:.2f} km/h/프레임')
    j('reason', bool(run.ev('race_phase', '->SUSPENDED res')), '')
    j.extra = {'res_on': t_on, 'res_off': t_off, 'estop_t': te}


def j_s09b(run, j):
    common(run, j)
    n = sum(1 for _, b, _ in run.f127 if b)
    j('버스 RES 프레임', n == 1, f'{n}개')
    not_suspended(run, j)
    no_latch(run, j)
    j('끝까지 모델 명령', run.tx[-1][3] == 100, f'{run.tx[-1][3]} %')


def _alive_loss(run, j, t_loss):
    te = run.ev_t('race_phase', '->SUSPENDED res_alive_lost')
    if not j('SUSPENDED(res_alive_lost)', te is not None, ''):
        return
    j('마지막 alive 뒤 0.5 s', 0.5 - 0.02 <= te - t_loss <= 0.5 + JITTER_S, f'{te - t_loss:.3f} s')
    ts = run.state_t('ESTOP')
    j('ESTOP', ts is not None, '')
    after = run.tx_in(te + JITTER_S)
    j('가속 0', all(f[3] == 0 for f in after), f'{len(after)}프레임')


def j_s10(run, j):
    common(run, j)
    _alive_loss(run, j, run.f127[-1][0] if run.f127 else 0.0)


def j_s10b(run, j):
    common(run, j)
    frozen = next((t for (t, _, a), (_, _, b) in zip(run.f127[1:], run.f127) if a == b), None)
    j('프레임은 계속 옴', run.f127 and run.f127[-1][0] > 9.0, f'{run.f127[-1][0]:.1f} s')
    if j('alive 고정 시각', frozen is not None, f'{frozen:.3f} s' if frozen else ''):
        _alive_loss(run, j, frozen - 0.02)


def j_s11a(run, j):
    common(run, j)
    j('race_signal_lost 기록', bool(run.ev('race_signal_lost')), '')
    not_suspended(run, j)
    no_latch(run, j)
    t_last = run.f124[-1][0]
    after = run.tx_in(t_last + 1.0)
    j('끊긴 뒤 주행 유지', after and all(f[3] == 100 for f in after), f'{len(after)}프레임')


def j_s11b(run, j):
    common(run, j)
    t_last = run.f124[-1][0]
    te = run.ev_t('race_phase', '->SUSPENDED race_signal_timeout')
    if j('SUSPENDED(timeout)', te is not None, ''):
        j('끊김 뒤 1.0 s(시험용 값)', 1.0 - 0.02 <= te - t_last <= 1.0 + JITTER_S, f'{te - t_last:.3f} s')
        j('ESTOP', run.state_t('ESTOP') is not None, '')


def j_s12a(run, j):
    common(run, j)
    t0 = run.flag_t(SLOW_ON | SLOW_OFF, 2)
    j('0x18 프레임', any(b & 0x18 == 0x18 for _, b in run.f124), '')
    j('slow_on 이벤트', run.ev_t('slow_on') is not None, '')
    j('slow_off 이벤트 없음', not run.ev('slow_off'), '')
    j('서행 끝까지 유지', run.ev_t('override_on') is not None and not run.ev('override_off'), '')
    after = run.tx_in(t0 + JITTER_S) if t0 else []
    j('모델 100 % 차단', after and all(f[3] < 100 or f[2] == 0 and run.v_at(f[0]) < 8.0 for f in after), '')


def j_s12b(run, j):
    common(run, j)
    t0 = next((t for t, b in run.f124 if b == 0x90), None)
    j('0x90 프레임', t0 is not None, '')
    j('RACING', bool(run.ev('race_phase', 'PRE_GO->RACING')), '')
    j('서행 켜짐', run.ev_t('override_on') is not None, '')
    vmax = max(v for _, v in run.v)
    j('10 초과', vmax <= LIMIT_KPH, f'최고 {vmax:.2f} km/h')
    j('서행 PI 로 출발(9 초 안)', vmax > 3.0, f'최고 {vmax:.2f} km/h')


def j_s12c(run, j):
    common(run, j)
    j('0xE0 프레임', any(b == 0xE0 for _, b in run.f124), '')
    j('SUSPENDED stop', bool(run.ev('race_phase', 'PRE_GO->SUSPENDED stop')), '')
    j('RACING 전이', not run.ev('race_phase', '->RACING'), '')
    j('가속 전부 0', all(f[3] == 0 for f in run.tx), '')
    j('차속 0', max(v for _, v in run.v) == 0.0, '')
    j('ESTOP', run.state_t('ESTOP') is not None, '')


def j_s13(run, j):
    common(run, j)
    j('can_guard 두 번 실행·둘 다 rc 0', run.rcs == [0, 0], str(run.rcs))
    j('두 번째 start', len(run.ev('start')) == 2, '')
    ts = run.ev_t('race_phase', 'RACING->SUSPENDED stop')
    j('STOP 으로 중단', ts is not None and ts < 6.0, f'{ts:.2f} s' if ts else '없음')
    t_re = run.ev('start')[1][0] if len(run.ev('start')) == 2 else None
    if t_re is None:
        return
    second = [e for e in run.ev('race_phase') if e[0] >= t_re]
    j('재시작 뒤 전이', [e[2] for e in second] == ['PRE_GO->RACING go'], str([e[2] for e in second]))
    frames = run.tx_in(t_re, second[0][0] if second else 1e9)
    j('재시작 뒤 Go 전 가속 0·브레이크 30', frames and all(f[3] == 0 and f[2] == 30 for f in frames), f'{len(frames)}프레임')
    if second:
        j('Go 시각', second[0][0] >= 16.0, f'{second[0][0]:.2f} s')
        j('Go 뒤 모델 전달', any(f[3] == 100 for f in run.tx_in(second[0][0])), '')
    prev = run.tx_in(b=t_re)
    j('재시작 틈(0x210 없음, 개발 PC 참고)', True, f'{frames[0][0] - prev[-1][0]:.2f} s' if frames and prev else '')


def j_s14(run, j, res):
    """예산 판정은 recovery.py 그대로: 끊긴 시간 + 신선한 입력 3주기 확인까지 이탈이 0.30 m 를 넘으면 정지 확정."""
    common(run, j)
    p = run.params
    tg, gap, hold, rmin = p['t_gap'], p['gap'], p['hold'], p['rmin']
    written_gap = max(gap - hold, 0.0)
    peak = max((v for _, v in run.v_in(tg - 0.1, tg + gap + 0.1)), default=p['kph'])
    params = RecoveryParams(min_radius_m=rmin)
    budget = budget_gap_s(peak, params)
    g_eff = gap + params.fresh_frames * 0.02
    if written_gap <= 0.05:
        predicted = '무변화'
        boundary = written_gap > 0.05 - 0.03
    else:
        predicted = '정지 확정' if g_eff > budget else '복구'
        boundary = abs(g_eff - budget) <= 0.03 or abs(written_gap - 0.05) <= 0.03
    commit = run.ev('commit')
    recover = run.ev('recover')
    holds = [e for e in run.ev('state', '->HOLDING') if e[0] >= tg - 0.05]
    outcome = '정지 확정' if commit else '복구' if recover else '무변화' if not holds else '미완'
    res.update(cond=p['cond'], rmin=rmin, kph=p['kph'], gap=gap, peak=peak, budget=budget, predicted=predicted, boundary=boundary,
               outcome=outcome, commit=commit[0][2] if commit else '', recover=recover[0][2] if recover else '',
               stopped=run.state_t('STOPPED'))
    after = run.tx_in(tg + gap + 0.3)
    res['min_kph'] = min((v for _, v in run.v_in(tg)), default=None)
    res['end_kph'] = run.v[-1][1] if run.v else None
    res['max_brake'] = max((f[2] for f in run.tx_in(tg)), default=0)
    res['t_stop_after_gap'] = None if res['stopped'] is None else res['stopped'] - tg
    j('결과', outcome == predicted or boundary, f'{outcome}(예산 {budget:.2f} s, 끊김+확인 {g_eff:.2f} s, 예상 {predicted}{", 경계" if boundary else ""})')
    if outcome == '복구':
        coast = [f for f in run.tx_in(tg, tg + gap) if f[3] == 0]
        j('복구 구간 브레이크 0', all(f[2] == 0 for f in coast), f'{len(coast)}프레임')
        j('복구 뒤 ACTIVE', run.ev_t('state', 'HOLDING->ACTIVE') is not None, '')
        j('복구 뒤 STOPPED 없음', res['stopped'] is None, '')
    if outcome == '정지 확정':
        j('정지 확정 뒤 입력 재개돼도 가속 0', all(f[3] == 0 for f in after), f'{len(after)}프레임')
        j('브레이크 ≥ 30', after and after[-1][2] >= 30, f'{after[-1][2] if after else "-"} %')
        j('ACTIVE 로 안 감', not [e for e in run.ev('state', '->ACTIVE') if e[0] > commit[0][0]], '')
    if run.ros:
        _ros_latch_checks(run, j)


def j_s15(run, j):
    common(run, j)
    tgo = run.ev_t('race_phase', 'PRE_GO->RACING go')
    hold_t, active = run.state_t('HOLDING'), run.ev('state', 'HOLDING->ACTIVE')
    j('끊김 → HOLDING', hold_t is not None, f'{hold_t:.2f} s' if hold_t else '없음')
    j('복귀 → ACTIVE', bool(active), f'{active[0][0]:.2f} s' if active else '없음')
    no_latch(run, j)
    frames = run.tx_in(b=tgo if tgo else 1e9)
    j('Go 전 가속 0', all(f[3] == 0 for f in frames), f'{len(frames)}프레임')
    j('Go 전 브레이크 ≥ 30', all(f[2] >= 30 for f in frames), '')
    j('Go 전 0x210 끊김 최대', gap_max(run, 0.5, tgo or 1e9) < 0.1, f'{gap_max(run, 0.5, tgo or 1e9) * 1e3:.0f} ms')
    if j('Go', tgo is not None, f'{tgo:.2f} s' if tgo else '없음'):
        j('Go 뒤 모델 전달', any(f[3] > 0 and f[2] == 0 for f in run.tx_in(tgo)), '')
    if run.ros:
        log = run.ros.get('adapter', [])
        gap_w = [x for x in log if 1.7 <= x[0] < 2.4]
        j('끊김 중 어댑터 기록 멈춤·래치 없음', gap_w and all(not x[3] for x in gap_w) and not any('입력 거부' in x[4] for x in gap_w), f'{len(gap_w)}개')
        ok_w = [x for x in log if 2.7 <= x[0] < 3.4]
        j('복귀 뒤 기록 재개', ok_w and all(x[3] for x in ok_w), f'{len(ok_w)}개')
        latch = next((x[0] for x in log if 3.5 <= x[0] < 4.5 and not x[3] and '입력 거부' in x[4]), None)
        j('NaN → 어댑터 래치', latch is not None, f'{latch:.2f} s' if latch else '없음')
        back = next((x[0] for x in log if latch and x[0] > latch + 0.1 and x[3]), None)
        j('래치 자동 해제(잠정 1 s, NaN 끝 3.7 s 기준)', back is not None and 0.8 <= back - 3.7 <= 1.5, f'{back - 3.7:.2f} s' if back else '없음')
        armed = [x for x in log if back and back <= x[0] < 6.0]
        j('재 arm', armed and all(x[1] and x[2] for x in armed), f'{len(armed)}개')


def j_s16(run, j):
    common(run, j)
    ton = run.ev_t('slow_on')
    toff = run.ev_t('slow_off')
    if not (j('slow_on', ton is not None, '') and j('slow_off', toff is not None, '')):
        return
    tx93 = [x for x in run.f203 if x[1] == 93]
    tx1000 = [x for x in run.f203 if x[1] == 1000 and x[0] >= toff - 0.01]
    j('Slow On → 0x203 93', tx93 and tx93[0][0] - ton <= JITTER_S, f'{tx93[0][0] - ton:.3f} s' if tx93 else '없음')
    conf = run.ev('speed_limit_confirm', 'target=9.3')
    if j('3프레임 반영 확인(speed_limit_confirm)', bool(conf), conf[0][2] if conf else '없음') and tx93:
        n = sum(1 for t, r in run.f202 if tx93[0][0] <= t <= conf[0][0] and r == 93)
        j('확인까지 0x202 = 93 프레임', n >= 3, f'{n}개')
    j('Slow Off → 0x203 1000(해제, 시험용 값)', tx1000 and tx1000[0][0] - toff <= JITTER_S, f'{tx1000[0][0] - toff:.3f} s' if tx1000 else '없음')
    j('해제 확인', bool(run.ev('speed_limit_confirm', 'target=100')), '')
    last = [r for t, r in run.f202 if t >= toff + 0.5]
    j('해제 뒤 0x202', last and all(r == 1000 for r in last), '')
    # 시험 조작: 8.0 s 에 값이 저절로 100 으로 바뀌고 0.6 s 동안 0x203 을 안 받음(0.1 s 보험 주기가 바로 고쳐 어긋남 경로가 안 도는 것을 막음)
    forced = next((t for t, r in run.f202 if r == 1000 and ton + 1.0 < t < toff), None)
    mm = run.ev('speed_limit_mismatch')
    if j('어긋남 발생(8 s)', forced is not None, f'{forced:.2f} s' if forced else '없음'):
        j('어긋남 기록(잠정 0.3 s)', bool(mm) and 0.3 - 0.02 <= mm[0][0] - forced <= 0.3 + JITTER_S, f'{mm[0][0] - forced:.3f} s 뒤' if mm else '없음')
        tx_rate = [t for t, r in run.f203 if r == 93 and mm and mm[0][0] - 0.01 <= t < forced + 0.6]
        back = next((t for t, r in run.f202 if r == 93 and t > forced), None)
        j('어긋남 뒤 빠른 주기 재송신', len(tx_rate) >= 3, f'{len(tx_rate)}프레임')
        j('수락되자 복귀', back is not None and back >= forced + 0.6 - 0.05, f'{back - forced:.2f} s' if back else '없음')
        j('다시 3프레임 확인', len(run.ev('speed_limit_confirm', 'target=9.3')) >= 2, f'{len(run.ev("speed_limit_confirm", "target=9.3"))}회')
    j('외부 0x203', not run.ev('foreign_0x203'), '')


def j_s17a(run, j):
    j('종료 코드', run.rcs == [5], str(run.rcs))
    j('0x210', not run.tx, f'{len(run.tx)}개')
    j('0x203', not run.f203, f'{len(run.f203)}개')
    e = run.ev('speed_limit_startup', 'action=')
    j('시작 점검 기록', bool(e), e[0][2] if e else '')


def j_s17b(run, j):
    common(run, j)
    j('종료 코드', run.rcs == [0], str(run.rcs))
    j('0x203 해제값(시험용 100)', run.f203 and run.f203[0][1] == 1000, str(run.f203[0][1]) if run.f203 else '없음')
    t_conf = run.ev_t('speed_limit_confirm', 'target=100')
    j('반영 확인', t_conf is not None, '')
    j('0x202 → 100', run.f202 and run.f202[-1][1] == 1000, '')
    if run.tx and t_conf is not None:
        j('0x210 은 해제 확인 뒤', run.tx[0][0] >= t_conf - 0.02, f'첫 0x210 {run.tx[0][0]:.2f} s, 확인 {t_conf:.2f} s')


def j_x01(run, j):
    common(run, j)
    j('race_pit_zone 기록', bool(run.ev('race_pit_zone')), run.ev('race_pit_zone')[0][2] if run.ev('race_pit_zone') else '없음')
    j('race_repair 기록', bool(run.ev('race_repair')), run.ev('race_repair')[0][2] if run.ev('race_repair') else '없음')
    j('상태 변화(Go 말고)', [e[2] for e in run.ev('race_phase')] == ['PRE_GO->RACING go'], str(len(run.ev('race_phase'))))
    no_latch(run, j)
    j('모델 명령 유지', run.tx[-1][3] == 100, '')


def j_s18(run, j):
    if run.error:
        j('실행 오류', False, run.error)
    j('rc', run.rcs == [0], str(run.rcs))
    frames = run.tx_in(0.6)
    j('깃발·RES 와 무관하게 0x210 그대로(가속 40·브레이크 0·조향 10)',
      frames and all(f[3] == 40 and f[2] == 0 and abs(f[1] - 10.0) < 0.11 and f[4] == 7 for f in frames), f'{len(frames)}프레임')
    j('0x203', not run.f203, f'{len(run.f203)}개')
    extra = {e[1] for e in run.events} - {'start', 'state', 'stop', 'estop_request'}
    j('경기 이벤트', not extra, '/'.join(sorted(extra)) or '없음')
    j('StatusChannel', not run.status, f'{len(run.status)}개')


def j_w5(run, j):
    """WP5 관문: /race/state 가 실제 can_guard 를 따라감."""
    common(run, j)
    race = run.ros.get('race', [])
    if not j('/race/state 수신', bool(race), f'{len(race)}개'):
        return
    seq = []
    for t, code, name, *_ in race:
        if not seq or seq[-1][1] != name:
            seq.append((t, name))
    names = [n for _, n in seq]
    want = ['PRE_GO', 'RACING', 'SLOW', 'RACING', 'FINISHED', 'PARKED', 'SUSPENDED']
    j('상태 순서', names == want, '→'.join(names))
    # can_guard 이벤트 → /race/state 도착 지연(같은 PC 벽시계)
    ev_t = [run.ev_t('race_phase', 'PRE_GO->RACING'), run.ev_t('slow_on'), run.ev_t('slow_off'),
            run.ev_t('race_phase', 'RACING->FINISHED'), run.ev_t('race_phase', 'FINISHED->PARKED'), run.ev_t('race_phase', '->SUSPENDED')]
    if names == want and all(t is not None for t in ev_t):
        lags = [seq[i + 1][0] - ev_t[i] for i in range(6)]
        j('전이 지연 최대(개발 PC 참고, can_guard 이벤트→수신)', all(-0.02 <= x < 0.1 for x in lags), f'{max(lags) * 1e3:.0f} ms')
    # 값 대조: override·guard_state
    s_race = [x for x in race if x[2] == 'SLOW']
    j('SLOW 동안 long_override·상한 8.8', s_race and all(x[3] and abs(x[4] - ACC_CUT) < 1e-3 for x in s_race), f'{len(s_race)}개')
    last = race[-1]
    j('끝 guard_state', last[5] == 'ESTOP', last[5])
    j('0x202 값 전달', all(abs(x[6] - 30.0) < 1e-3 for x in race), '')


# ─── 실행 ─────────────────────────────────────────────────────────────────────────────────────

class ModelLink:
    """모델 → CommandChannel + 하트비트(어댑터가 없는 경로). can_guard 가 재시작하면 세그먼트를 다시 연다."""

    def __init__(self, cmd_name, hb_name):
        self.cmd_name, self.hb_name = cmd_name, hb_name
        self.cmd = self.hb = self.ino = None

    def _ensure(self):
        try:
            ino = os.stat(f'/dev/shm/{self.cmd_name}').st_ino
        except FileNotFoundError:
            self.close()
            return False
        if self.cmd is not None and ino == self.ino:
            return True
        self.close()
        try:
            self.cmd = CommandChannel.open(self.cmd_name)
            self.hb = HeartbeatChannel.open(self.hb_name)
        except (FileNotFoundError, ValueError):
            self.close()
            return False
        self.ino = ino
        return True

    def write(self, out, kph_steer):
        if out is None or not self._ensure():
            return
        try:
            self.cmd.write(Command(steer_auto=True, brake_auto=True, acc_auto=True, steer_cmd_deg=out.steer,
                                   acc_cmd_pct=out.acc, brake_cmd_pct=out.brake))
            self.hb.beat()
        except (ValueError, OSError):
            self.close()

    def ready(self):
        return True

    def close(self):
        for ch in (self.cmd, self.hb):
            if ch is not None:
                try:
                    ch.close()
                except Exception:   # noqa: BLE001 — 시험 정리
                    pass
        self.cmd = self.hb = None

    def logs(self):
        return {}


def ros_env(env=None):
    """`source` 없이 install 을 잡는다(ros2 run 과 rclpy 가 쓰는 변수만). 환경에 ROS 기본이 있어야 한다."""
    env = dict(os.environ if env is None else env)
    inst = os.path.join(REPO, 'ros2_ws', 'install')
    if not os.path.isdir(inst):
        raise SystemExit(f'{inst} 없음 — ros2_ws 에서 colcon build 먼저')
    pkgs = [os.path.join(inst, p) for p in ('a1_can_msgs', 'a1_can_bridge', 'a1_control_adapter')]
    def join(key, parts):
        env[key] = ':'.join([*parts, env.get(key, '')]).rstrip(':')
    join('AMENT_PREFIX_PATH', pkgs)
    join('COLCON_PREFIX_PATH', [inst])
    py = []
    for p in pkgs:
        py += [os.path.join(p, 'local', 'lib', 'python3.10', 'dist-packages'), os.path.join(p, 'lib', 'python3.10', 'site-packages')]
    join('PYTHONPATH', py)
    join('LD_LIBRARY_PATH', [os.path.join(p, 'lib') for p in pkgs])
    return env


class RosLink:
    """모델 → /control/target → 실제 cmd_adapter. race_state_publisher 도 같이 띄워 /race/state·어댑터 상태를 기록한다."""

    def __init__(self, names, work, tag):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
        from a1_can_msgs.msg import AdapterStatus, ControlInfo, ControllerOutput, RaceState
        self.msgs = (ControlInfo, ControllerOutput)
        self.rclpy = rclpy
        self.procs, self.files = [], []
        self.adapter, self.race = [], []
        self.t_wall0 = None
        self.last_adapter = None
        env = ros_env()
        common_p = [f'can_guard_dir:={GUARD_DIR}', f'status_shm:={names["status"]}']
        track = os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')

        def launch(pkg, exe, params, extra=()):
            cmd = ['ros2', 'run', pkg, exe, '--ros-args', *extra] + [x for p in params for x in ('-p', p)]
            f = open(os.path.join(work, f'{tag}_{exe}.log'), 'w')
            self.files.append(f)
            self.procs.append(subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=env))

        launch('a1_can_bridge', 'race_state_publisher', common_p)
        launch('a1_control_adapter', 'cmd_adapter', [*common_p, f'cmd_shm:={names["cmd"]}', f'hb_shm:={names["hb"]}', 'auto_arm:=true'],
               ('--params-file', track))
        rclpy.init()
        self.node = Node('r_race_probe')
        qb = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
        rs = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub_t = self.node.create_publisher(ControllerOutput, '/control/target', qb)
        self.pub_i = self.node.create_publisher(ControlInfo, '/control/status/control_info', qb)
        self.node.create_subscription(RaceState, '/race/state', self._on_race, rs)
        self.node.create_subscription(AdapterStatus, '/control/adapter/status', self._on_adapter, 10)
        self.ex = SingleThreadedExecutor()
        self.ex.add_node(self.node)
        threading.Thread(target=self.ex.spin, daemon=True).start()

    def _on_race(self, m):
        self.race.append((time.time(), m.phase, m.phase_name, m.long_override, m.speed_cap_kph, m.guard_state, m.vehicle_speed_limit_kph))

    def _on_adapter(self, m):
        self.last_adapter = m
        self.adapter.append((time.time(), m.steer_armed, m.long_armed, m.writing, m.note, m.rejected_count))

    def write(self, out, steer):
        ControlInfo, ControllerOutput = self.msgs
        info = ControlInfo()
        info.steer_position_deg = float(steer)
        self.pub_i.publish(info)
        if out is not None:
            m = ControllerOutput()
            m.accel_mps2, m.steer_tire_deg = float(out.accel), float(out.steer) / 7.5
            self.pub_t.publish(m)

    def ready(self):
        a = self.last_adapter
        return a is not None and a.steer_armed and a.long_armed and a.writing

    def close(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        for f in self.files:
            f.close()
        self.ex.shutdown()
        self.node.destroy_node()
        if self.rclpy.ok():
            self.rclpy.shutdown()

    def logs(self):
        return {'adapter': self.adapter, 'race': self.race}


def scenario_dict(case):
    """yaml + 코드의 덧붙임 → fake_race_server 가 받는 dict. harness.vehicle·vehicle_events·guard_args·restart 를 꺼내 쓸 수 있게 raw 를 돌려준다."""
    import yaml
    with open(os.path.join(SCEN_DIR, case.yaml + '.yaml'), encoding='utf-8') as f:
        raw = yaml.safe_load(f)
    if case.duration:
        raw['duration_s'] = case.duration
    raw['events'] = list(raw.get('events') or []) + case.extra_events
    return raw


def vehicle_cli(vehicle, channel, cmd_file):
    """프로세스 모드 fake_a1_vehicle 명령. 표는 "x,..:y,.." 로 넘긴다."""
    def tbl(t):
        return ','.join(str(v) for v in t[0]) + ':' + ','.join(str(v) for v in t[1])
    argv = [sys.executable, os.path.join(SIL_DIR, 'fake_a1_vehicle.py'), '--channel', channel, '--cmd-file', cmd_file]
    for key, flag, conv in (('speed_limit_kph', '--speed-limit-kph', str), ('speed_limit_latency_s', '--speed-limit-latency', str),
                            ('accel_map', '--accel-map', tbl), ('brake_map', '--brake-map', tbl),
                            ('drag', '--drag', lambda d: ','.join(str(v) for v in d)), ('drive_delay_s', '--drive-delay', str)):
        if key in vehicle:
            argv += [flag, conv(vehicle[key])]
    if vehicle.get('physics'):
        argv.append('--physics')
    return argv


class InProcRig:
    """같은 프로세스(virtual). can_guard 는 주 스레드(신호 등록), 서버·차량은 드라이버 스레드에서 step."""

    def __init__(self, case, channel, names, vehicle, argv, raw):
        import can_guard
        self.cg = can_guard
        self.channel, self.argv = channel, argv
        self.car = fav.FakeVehicle(can.Bus(channel=channel, interface='virtual'), period_s=0.021, **vehicle)
        self.server = frs.RaceServer(can.Bus(channel=channel, interface='virtual'), frs.load_scenario(raw))
        self.restart_pending = False
        self.server_on = False
        self.rcs = []
        self.done = False

    def start_server(self, now):
        self.server.start(now)
        self.server_on = True

    def tick(self, now):
        self.car.step(now)
        if self.server_on:
            self.server.step(now)

    def vehicle_event(self, kind, val):
        if kind == 'force_limit':
            self.car.force_speed_limit(val)
        elif kind == 'ignore_limit':
            self.car.set_ignore_speed_limit(bool(val))
        else:
            self.car.kph = val

    def restart_guard(self):
        self.restart_pending = True
        self.cg._stop_requested = True

    def stop(self):
        self.cg._stop_requested = True

    def main_thread(self, driver):
        while True:
            self.cg._stop_requested = False
            self.rcs.append(self.cg.run(self.cg.parse_args(self.argv)))
            if self.restart_pending:
                self.restart_pending = False
                continue
            break
        self.done = True
        driver.join(timeout=60)

    def close(self):
        self.car.bus.shutdown()
        self.server.bus.shutdown()


class ProcRig:
    """대회 PC(socketcan vcan*): can_guard·서버·차량을 별도 프로세스로. 드라이버 스레드는 모델·기록만."""

    def __init__(self, case, channel, names, vehicle, argv, raw, work):
        import yaml
        self.channel, self.argv, self.work = channel, argv, work
        self.tag = names['tag']
        self.scen_path = os.path.join(work, f'{self.tag}_scenario.yaml')
        with open(self.scen_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(raw, f, allow_unicode=True)
        self.cmd_file = os.path.join(work, f'{self.tag}_vehicle.cmd')
        self.log = open(os.path.join(work, f'{self.tag}_proc.log'), 'w')
        self.car = subprocess.Popen(vehicle_cli(vehicle, channel, self.cmd_file), stdout=self.log, stderr=subprocess.STDOUT)
        self.guard = self._guard()
        self.server = None
        self.restart_pending = False
        self.rcs = []
        self.done = False

    def _guard(self):
        return subprocess.Popen([sys.executable, os.path.join(GUARD_DIR, 'can_guard.py'), *self.argv], stdout=self.log, stderr=subprocess.STDOUT)

    def start_server(self, now):
        self.server = subprocess.Popen([sys.executable, os.path.join(SIL_DIR, 'fake_race_server.py'), self.scen_path, '--channel', self.channel,
                                        '--quiet'], stdout=self.log, stderr=subprocess.STDOUT)

    def tick(self, now):
        if not self.restart_pending and self.guard is not None and self.guard.poll() is not None:
            self.rcs.append(self.guard.returncode)
            self.guard = None
            self.done = True

    def vehicle_event(self, kind, val):
        tmp = self.cmd_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(f'{kind} {val}\n')
        os.replace(tmp, self.cmd_file)

    def restart_guard(self):
        self.restart_pending = True
        self.guard.send_signal(signal.SIGTERM)
        self.rcs.append(self.guard.wait(5))
        self.guard = self._guard()
        self.restart_pending = False

    def stop(self):
        if self.guard is not None:
            self.guard.send_signal(signal.SIGTERM)
            self.rcs.append(self.guard.wait(5))
            self.guard = None

    def main_thread(self, driver):
        driver.join()

    def close(self):
        for p in (self.server, self.car):
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.log.close()


def guard_argv(case, interface, channel, names, event_path, race=True):
    argv = ['--interface', interface, '--channel', channel, '--precheck-s', '0.3', '--quiet',
            '--cmd-shm', names['cmd'], '--hb-shm', names['hb'], '--estop-shm', names['estop'], '--event-log', event_path]
    if race:
        argv += ['--race', '--status-shm', names['status'], '--adapter-hold-after-s', str(case.hold_after)]
    return argv + case.guard


def run_case(case, interface, channel, work):
    """시나리오 하나를 돌린다. 주 스레드에서 불러야 한다(can_guard 신호 등록)."""
    tag = uuid.uuid4().hex[:8]
    names = {'tag': tag, 'cmd': 'rc' + tag, 'hb': 'rh' + tag, 'estop': 're' + tag, 'status': 'rs' + tag}
    event_path = os.path.join(work, f'{case.id}_{tag}_events.csv')
    raw = scenario_dict(case)
    h = raw.get('harness') or {}
    race = h.get('race', True)
    vehicle = {**(h.get('vehicle') or {}), **case.vehicle}
    guard = guard_argv(case, interface, channel if interface != 'virtual' else 'r' + tag, names, event_path, race)
    guard += [str(x) for x in h.get('guard_args') or []]
    bus_channel = channel if interface != 'virtual' else 'r' + tag
    rec = Recorder(can.Bus(channel=bus_channel, interface=interface))
    if interface == 'virtual':
        rig = InProcRig(case, bus_channel, names, vehicle, guard, raw)
    else:
        rig = ProcRig(case, bus_channel, names, vehicle, guard, raw, work)
    link = RosLink(names, work, f'{case.id}_{tag}') if case.ros else ModelLink(names['cmd'], names['hb'])
    duration = float(raw['duration_s'])
    timeline = sorted(case.events + [(float(e['t']), 'force_limit', float(e['force_speed_limit_kph']))
                                     for e in h.get('vehicle_events') or []], key=lambda x: x[0])
    restart_at = h.get('restart_guard_at_s')
    state = {'t0_wall': time.time(), 't0': None, 'error': None}
    status = []
    st_ch = {'ch': None}

    def sample_status(t):
        if not race:
            if StatusChannel.open_or_none(names['status']) is not None:
                status.append((t, 'exists', '', False, False))
            return
        if st_ch['ch'] is None:
            st_ch['ch'] = StatusChannel.open_or_none(names['status'])
        if st_ch['ch'] is None:
            return
        try:
            s = st_ch['ch'].read()
        except (TornReadError, ValueError):
            return
        if s is not None:
            status.append((t, s.race_phase_name, s.guard_state_name, bool(s.input_grace), bool(s.long_override)))

    def driver():
        next_model = time.monotonic()
        started = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                rig.tick(now)
                if state['t0'] is None and (not case.ros or link.ready()):
                    state['t0_wall'] = time.time()
                    state['t0'] = now
                    rig.start_server(now)
                if state['t0'] is None and now - started > 40.0:
                    state['error'] = '어댑터 자동 arm 못 함(40 s)'
                    break
                t = -1.0 if state['t0'] is None else now - state['t0']
                if now >= next_model:
                    next_model = max(next_model + 0.02, now - 0.02)
                    link.write(case.model(t, rec.kph), rec.steer)
                    if state['t0'] is not None:
                        sample_status(t)
                while timeline and state['t0'] is not None and t >= timeline[0][0]:
                    _, kind, val = timeline.pop(0)
                    rig.vehicle_event(kind, val)
                if restart_at is not None and t >= float(restart_at) and not state.get('restarted'):
                    state['restarted'] = True
                    rig.restart_guard()
                if t >= duration or rig.done:
                    break
                time.sleep(0.001)
        except Exception as e:   # noqa: BLE001 — 주 스레드에서 판정에 넣는다
            state['error'] = f'{type(e).__name__}: {e}'
        finally:
            rig.stop()

    rec.start()
    th = threading.Thread(target=driver, daemon=True)
    th.start()
    try:
        rig.main_thread(th)
    finally:
        time.sleep(0.1)
        rec.stop_evt.set()
        rec.join(timeout=1)
        ros_logs = link.logs()
        link.close()
        rig.close()
        rec.bus.shutdown()
        if st_ch['ch'] is not None:
            try:
                st_ch['ch'].close()
            except Exception:   # noqa: BLE001
                pass
    t0w = state['t0_wall']
    ros = {k: [(x[0] - t0w, *x[1:]) for x in v] for k, v in ros_logs.items()}
    run = Run(rec.frames, t0w, event_path, rig.rcs, status, ros, error=state['error'])
    run.params = case.params
    return run


def compress_rows(run):
    """0x210 프레임 + 그 시각 차속. 값이 바뀌었거나 0.1 s 지났을 때만(파일을 작게)."""
    rows, last, last_t = [], None, -1e9
    for f in run.tx:
        cur = (f[1], f[2], f[3], f[4])
        if cur != last or f[0] - last_t >= 0.1:
            v = run.v_at(f[0])
            rows.append([f'{f[0]:.3f}', f[1], f[2], f[3], f[4], '' if v is None else f'{v:.2f}'])
            last, last_t = cur, f[0]
    return rows


def run_one(case, interface, channel, work, out_json, csv_dir):
    run = run_case(case, interface, channel, work)
    j = J()
    j.extra = {}
    j.second = None
    res = {}
    try:
        if case.judge in (j_s05, j_s14):
            case.judge(run, j, res)
        else:
            case.judge(run, j)
    except Exception as e:   # noqa: BLE001 — 판정기 오류도 FAIL 로 드러낸다
        j('판정 오류', False, f'{type(e).__name__}: {e}')
    rows = [{'id': case.id, 'title': case.title, 'criterion': case.criterion, 'value': j.text(), 'ok': j.ok}]
    if j.second is not None:
        rows[0]['line'] = '0x201'
        rows.append({'id': case.id + '★', 'title': case.title + ' — 실제 속도 추정 ★', 'criterion': case.criterion,
                     'value': j.second.text(), 'ok': j.second.ok, 'line': 'true'})
    extra = dict(j.extra)
    extra.update(res)
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump({'id': case.id, 'rows': rows, 'res': extra, 'n_tx': len(run.tx), 'ros': case.ros}, f, ensure_ascii=False, default=str)
    if csv_dir and case.csv_keep:
        os.makedirs(csv_dir, exist_ok=True)
        with open(os.path.join(csv_dir, f'{case.id}.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['t_s', 'steer_deg', 'brake_pct', 'acc_pct', 'auto', 'kph_0x201'])
            w.writerows(compress_rows(run))
        with open(os.path.join(csv_dir, f'{case.id}_events.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['t_s', 'event', 'detail'])
            w.writerows((f'{e[0]:.3f}', e[1], e[2]) for e in run.events)
    return 0


# ─── 시나리오 표 ──────────────────────────────────────────────────────────────────────────────

def s14_cases(cond):
    import yaml
    with open(os.path.join(SCEN_DIR, 'S14_model_gap.yaml'), encoding='utf-8') as f:
        h = yaml.safe_load(f)['harness']
    m, t_after = h['matrix'], float(h['t_gap_after_go_s'])
    out = []
    for rmin in m['r_min_m']:
        if cond == 'emu' and rmin is not None:
            continue
        for kph in m['speeds_kph']:
            for gap in m['gaps_s']:
                tg = 1.0 + t_after
                hold = 0.0 if cond == 'direct' else 0.10
                ff = 10.0
                if cond == 'ros':
                    model = with_gap(m_hold(kph, ff), tg, tg + gap)
                    vehicle = dict(MEASURED)
                else:
                    written = max(gap - hold, 0.0)
                    model = with_gap(m_hold(kph, ff), tg, tg + written)
                    vehicle = {}
                extra = [] if rmin is None else ['--track-min-radius-m', str(rmin)]
                label = {'direct': '어댑터 없음(hold_after 0)', 'emu': '어댑터 흉내(hold_after 0.10)', 'ros': '실제 어댑터(hold_after 0.10)'}[cond]
                cid = f'S14-{cond}-{"R" + str(int(rmin)) if rmin else "Rx"}-{int(kph)}-{gap:g}'
                out.append(Case(cid, 'S14_model_gap', f'모델 {gap:g} s 끊김, {kph:g} km/h, R_min {rmin or "모름"}, {label}',
                                '예산 안 = 복구, 밖 = 정지 확정', j_s14, model, guard=extra, vehicle=vehicle,
                                events=[(1.1, 'set_kph', kph)], ros=cond == 'ros', hold_after=hold,
                                params={'cond': cond, 'rmin': rmin, 'kph': kph, 'gap': gap, 'hold': hold, 't_gap': tg},
                                csv_keep=gap == 0.2, duration=tg + gap + 2.5))
    return out


def m_s06r(t, v):
    """Slow On(3.0 s) 이 확인된 뒤에만 한계 밖 40 m/s². 그 전 RACING(유예 밖)에서 한계 밖이면 래치가 맞는 동작이다(D7)."""
    return MO(100.0, 0.0, 0.0, 40.0 if t >= 3.1 else 0.8)


def m_s15r(t, v):
    if 1.5 <= t < 2.5:
        return None
    if 3.5 <= t < 3.7:
        return MO(100.0, 0.0, 0.0, float('nan'))
    return MO(100.0, 0.0, 0.0, 1.0)


def all_cases(with_ros):
    meas = dict(MEASURED)
    meas_d = {**MEASURED, 'drive_delay_s': LAUNCH_DELAY_S}
    s5 = '9.3 이하 도달 시간 기록, 이후 10 초과 0회'
    cases = [
        Case('S01', 'S01_pre_go_model_full_accel', 'Go 전 모델이 최대 가속을 냄', '0x210 가속 0, 차속 0, 래치 없음', j_s01),
        Case('S02', 'S02_go', 'Go', 'Go 확인 후 다음 주기에 모델 명령 전달', j_s02),
        Case('S02b', 'S02b_go_on_at_start', '시작부터 Go 켜짐(A1), 0 뒤 상승만', '0 을 본 뒤의 상승에서만 RACING(브리프 A1)', j_s02b),
        Case('S03', 'S03_go_pulse_1frame', 'Go 가 1프레임만(pulse)', '무시(2프레임 확인), 이벤트 기록', j_s03),
        Case('S03b', 'S03b_go_flicker', 'Go 1프레임 깜빡임 10회', '무시(2프레임 확인), 이벤트 기록', j_s03),
        Case('S04', 'S04_go_pulse_2frames', 'Go 가 2프레임만', 'RACING 진입, 비트가 내려가도 유지', j_s04),
        Case('S05', 'S05_slow_on_from_30kph', '30 km/h 에서 Slow On, 가짜 차량 기본 표(75 % = 2.4 m/s², 실측보다 강함)', s5, j_s05, m_hold(30.0, 9.0),
             vehicle={}),
        Case('S05m', 'S05_slow_on_from_30kph', '30 km/h 에서 Slow On, 실측 표 ★(75 % ≈ 0 m/s²)', s5, j_s05, m_hold(30.0, 70.0), vehicle=meas,
             events=[(11.5, 'set_kph', 30.0)]),
        Case('S05d', 'S05_slow_on_from_30kph', '30 km/h 에서 Slow On, 실측 표 ★ + 구동 지연 0.7 s', s5, j_s05, m_hold(30.0, 70.0), vehicle=meas_d,
             events=[(11.5, 'set_kph', 30.0)]),
        Case('S06', 'S06_slow_model_aggressive', '서행 중 모델 큰 가속(모델 직결)', '속도 유지, 래치 없음(D7)', j_s06),
        Case('S07', 'S07_slow_off_ramp', 'Slow Off', '램프대로 가속 상한 증가, 입력 유예 1 s', j_s07),
        Case('S08', 'S08_finish_model_stop', 'Finish → 모델 정지', '5 s 뒤 PARKED, auto 000 없음', j_s08, m_finish(8.0)),
        Case('S09', 'S09_res_then_release', '주행 중 RES → 해제', 'ESTOP 래치, 해제 후에도 브레이크 유지', j_s09),
        Case('S09b', 'S09b_res_pulse_1frame', 'RES 1프레임(A7)', 'ESTOP 래치 없음(브리프 A7)', j_s09b),
        Case('S10', 'S10_res_alive_stop', '0x127 끊김', '0.5 s 뒤 SUSPENDED', j_s10),
        Case('S10b', 'S10b_res_alive_freeze', '0x127 alive 정지', '0.5 s 뒤 SUSPENDED', j_s10b),
        Case('S11a', 'S11a_race_signal_lost_default', '0x124 끊김, 기본', '기본: 기록만', j_s11a),
        Case('S11b', 'S11b_race_signal_lost_timeout', '0x124 끊김, timeout 1.0 s(시험용)', 'timeout 설정 시 SUSPENDED', j_s11b),
        Case('S12a', 'S12a_slow_on_and_off', '비트 동시 slow_on + slow_off', '4.2절 우선순위 — slow_on', j_s12a),
        Case('S12b', 'S12b_go_and_slow_on', '비트 동시 go + slow_on', '4.2절 우선순위 — RACING + 서행', j_s12b),
        Case('S12c', 'S12c_stop_finish_go', '비트 동시 stop + finish + go', '4.2절 우선순위 — stop 먼저', j_s12c),
        Case('S13', 'S13_restart_green_go', '중단 → 재시작 → 녹기 + Go', 'PRE_GO → RACING', j_s13),
        Case('S15', 'S15_pre_go_gap', 'PRE_GO 중 모델 끊김 → 복귀(모델 직결)', '래치 자동 해제, 재 arm', j_s15, with_gap(m_const(), 1.5, 2.5)),
        Case('S16', 'S16_speed_limit_tx', '0x203 tx on: Slow On/Off', '3프레임 반영, 해제, 어긋남 재송신', j_s16,
             events=[(8.0, 'ignore_limit', 1), (8.6, 'ignore_limit', 0)]),
        Case('S17a', 'S17a_residual_limit_tx_off', '시작 시 0x202 = 9.3, tx off', 'tx off: 시작 거부(종료 코드 5)', j_s17a, m_const(0.0)),
        Case('S17b', 'S17b_residual_limit_tx_on', '시작 시 0x202 = 9.3, tx on', 'tx on: 해제 후 통과', j_s17b, m_const(0.0)),
        Case('S18', 'S18_race_off', '--race off, 깃발·RES 를 흘림', '기존 E1~E7·P1·P2 결과와 같음(대체 시험)', j_s18, m_const(40.0, 10.0)),
        Case('X01', 'X01_pit_repair_record_only', '0x125·0x126 기록 전용', '기록만, 상태 변화 없음', j_x01),
    ]
    cases += s14_cases('direct') + s14_cases('emu')
    if with_ros:
        cases += [
            Case('S06r', 'S06_slow_model_aggressive', '서행 중 모델 큰 가속(40 m/s², 실제 어댑터)', '속도 유지, 래치 없음(D7)', j_s06, m_s06r, ros=True, hold_after=0.10),
            Case('S15r', 'S15_pre_go_gap', 'PRE_GO 중 끊김 복귀 + NaN 래치(실제 어댑터)', '래치 자동 해제, 재 arm', j_s15, m_s15r, ros=True, hold_after=0.10),
            Case('W5r', 'S02_go', '/race/state 가 실제 can_guard 를 따라감(WP5 관문)', 'PRE_GO→RACING→SLOW→RACING→FINISHED→PARKED→SUSPENDED 순서·값 일치', j_w5, m_finish(7.0, 15.0),
                 ros=True, hold_after=0.10, duration=19.0, extra_events=[{'t': 3.0, 'flag': 'slow_on', 'mode': 'level', 'until': 5.0},
                                                                         {'t': 5.0, 'flag': 'slow_off', 'mode': 'level', 'until': 6.0},
                                                                         {'t': 7.0, 'flag': 'finish', 'mode': 'level'},
                                                                         {'t': 17.0, 'flag': 'res', 'mode': 'level'}]),
        ]
        cases += s14_cases('ros')
    return {c.id: c for c in cases}


# ─── S18 값 비교 ──────────────────────────────────────────────────────────────────────────────

def rle(seq):
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def load_scenarios_csv(path):
    data = {}
    with open(path, newline='') as f:
        rd = csv.reader(row for row in f if not row.startswith('#'))
        for r in rd:
            if r[0] == 'scenario':
                continue
            data.setdefault(r[0], []).append((float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[5])))
    return data


def compare_scenarios(base, new):
    """--race off 값 열 비교. 벽시계 프레임 수는 다르므로 열별 값이 바뀌는 순서를 비교한다.
    브레이크 램프는 시나리오가 끝나는 시각에 따라 끝이 잘릴 수 있어 짧은 쪽 길이까지(앞부분 일치), 나머지는 전체 일치."""
    rows = []
    for name in sorted(base):
        if name not in new:
            rows.append((name, False, '실행 안 됨'))
            continue
        bad = []
        for col, label in ((1, '조향'), (3, '가속'), (4, 'auto')):
            if rle(f[col] for f in base[name]) != rle(f[col] for f in new[name]):
                bad.append(label)
        b, n = rle(f[2] for f in base[name]), rle(f[2] for f in new[name])
        m = min(len(b), len(n))
        if b[:m] != n[:m] or abs(len(b) - len(n)) > 3:
            bad.append('브레이크')
        rows.append((name, not bad, f'{len(new[name])}프레임' + (f', 다름: {"/".join(bad)}' if bad else '')))
    return rows


def run_s18b(work):
    base = os.path.join(REPO, 'reports', '2026-11-05_bsw_update_report', 'data', 'scenarios.csv')
    out = os.path.join(work, 's18b')
    os.makedirs(os.path.join(out, 'data'), exist_ok=True)   # scenarios.py 는 run 전에 이 폴더에 임시 파일을 연다
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    r = subprocess.run([sys.executable, os.path.join(REPO, 'reports', '2026-11-05_bsw_update_report', 'scenarios.py'), REPO, out],
                       env=env, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return [{'id': 'S18b', 'title': '11-05 보고서 8개 시나리오 값 열', 'criterion': '기존 결과와 같음', 'value': f'실행 실패 {r.stderr[-200:]}', 'ok': False}]
    rows = compare_scenarios(load_scenarios_csv(base), load_scenarios_csv(os.path.join(out, 'data', 'scenarios.csv')))
    return [{'id': 'S18b', 'title': '--race off, 11-05 보고서 8개 시나리오 값 열 비교(E1~E7·P1·P2 대체, 개발 PC)', 'criterion': '기존 결과와 같음',
             'value': ' · '.join(f'{"" if ok else "✗ "}{n} {d}' for n, ok, d in rows), 'ok': all(ok for _, ok, _ in rows)}]


def run_s18c():
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    env['PYTHONNOUSERSITE'] = '1'
    r = subprocess.run([sys.executable, '-m', 'pytest', '-q', os.path.join(GUARD_DIR, 'test'), os.path.join(REPO, 'tools', 'race_day', 'test_lift_tx.py')],
                       env=env, capture_output=True, text=True, timeout=600)
    tail = (r.stdout.strip().splitlines() or ['(출력 없음)'])[-1]
    return [{'id': 'S18c', 'title': 'can_guard·lift_tx 단위·통합 시험 전체(--race off 시험 포함)', 'criterion': '기존 결과와 같음', 'value': tail, 'ok': r.returncode == 0}]


def run_s18_process(channel):
    """socketcan 모드에서만: 진짜 E1~E7·P1·P2. 개발 PC 에서는 돌지 않는다."""
    rows = []
    for name, script in (('E1~E7', 'e_estop.py'), ('P1', 'p1_kill_control_node.py'), ('P2', 'p2_kill_perception.py')):
        r = subprocess.run([sys.executable, os.path.join(HERE, script)], capture_output=True, text=True, timeout=900)
        tail = (r.stdout.strip().splitlines() or ['(출력 없음)'])[-1]
        rows.append({'id': f'S18-{name}', 'title': f'--race off 프로세스 시험 {name}', 'criterion': '기존 결과와 같음', 'value': tail, 'ok': r.returncode == 0})
    return rows


# ─── 오케스트레이션 ───────────────────────────────────────────────────────────────────────────

def child_cmd(cid, a, work, out_json, csv_dir):
    return [sys.executable, os.path.abspath(__file__), '--one', cid, '--interface', a.interface, '--channel', a.channel,
            '--work', work, '--json', out_json] + (['--csv-dir', csv_dir] if csv_dir else []) + (['--with-ros'] if a.with_ros else [])


def run_round(round_no, cases, a, work):
    rdir = os.path.join(work, f'round{round_no}')
    os.makedirs(rdir, exist_ok=True)
    csv_dir = os.path.join(work, 'csv') if round_no == 1 else ''

    def one(case):
        out = os.path.join(rdir, f'{case.id}.json')
        env = dict(os.environ)
        if case.ros:
            env = ros_env(env)
            env['ROS_DOMAIN_ID'] = str(random.randint(100, 200))
        t0 = time.monotonic()
        r = subprocess.run(child_cmd(case.id, a, rdir, out, csv_dir), env=env, capture_output=True, text=True, timeout=240)
        if r.returncode != 0 or not os.path.exists(out):
            with open(out, 'w', encoding='utf-8') as f:
                json.dump({'id': case.id, 'ros': case.ros, 'res': {}, 'n_tx': 0,
                           'rows': [{'id': case.id, 'title': case.title, 'criterion': case.criterion, 'ok': False,
                                     'value': f'자식 프로세스 실패 rc={r.returncode}: {r.stderr[-300:]}'}]}, f, ensure_ascii=False)
        with open(out, encoding='utf-8') as f:
            d = json.load(f)
        ok = all(x['ok'] for x in d['rows'])
        print(f'[{round_no}] {"PASS" if ok else "FAIL"} {case.id} ({time.monotonic() - t0:.1f} s)'
              + ('' if ok else '  ' + ' | '.join(x['value'] for x in d['rows'] if not x['ok'])), flush=True)
        return d

    results = []
    plain = [c for c in cases if not c.ros]
    ros_ones = [c for c in cases if c.ros]
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        results += list(ex.map(one, plain))
    with cf.ThreadPoolExecutor(max(1, a.ros_jobs)) as ex:
        results += list(ex.map(one, ros_ones))
    return results


def extra_rows(a, work, only):
    rows = []
    if only and not any(x in only for x in ('S18b', 'S18c', 'S18')):
        return rows
    if not only or 'S18b' in only:
        rows += run_s18b(work)
    if not only or 'S18c' in only:
        rows += run_s18c()
    if a.interface == 'socketcan' and not only:
        rows += run_s18_process(a.channel)
    return rows


def git_rev():
    try:
        rev = subprocess.run(['git', '-C', REPO, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(['git', '-C', REPO, 'status', '--porcelain', '--', 'safety', 'ros2_ws/src', 'sil'], capture_output=True, text=True).stdout.strip()
        return rev + ('+수정중' if dirty else '')
    except OSError:
        return '?'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--interface', default='virtual', choices=('virtual', 'socketcan'))
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--with-ros', action='store_true', help='실제 cmd_adapter·race_state_publisher 경로도(ros2_ws colcon build 필요)')
    ap.add_argument('--rounds', type=int, default=3, help='전체 반복 횟수(플레이키 확인)')
    ap.add_argument('--only', default='', help='쉼표로 ID. S14 는 앞부분 일치(예: S14-direct)')
    ap.add_argument('--out', default=os.path.join(REPO, 'reports', 'race_flag', '2026-11-06_sil'))
    ap.add_argument('--work', default='', help='작업 폴더(기본 임시)')
    ap.add_argument('--jobs', type=int, default=4, help='동시 시나리오 수(모델 직결). 지연이 커지면 줄인다')
    ap.add_argument('--ros-jobs', type=int, default=1)
    ap.add_argument('--report-only', action='store_true', help='--work 결과로 보고서만 다시 만듦')
    ap.add_argument('--no-report', action='store_true')
    ap.add_argument('--print-commands', default='', metavar='ID', help='프로세스 모드 명령을 찍고 끝(실행 안 함)')
    ap.add_argument('--one', default='', help=argparse.SUPPRESS)
    ap.add_argument('--json', default='', help=argparse.SUPPRESS)
    ap.add_argument('--csv-dir', default='', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.interface == 'socketcan' and not a.channel.startswith('vcan'):
        print(f'[r_race] 거부: {a.channel} — 가상 버스(vcan*)에서만 돈다', file=sys.stderr)
        return 2

    cases = all_cases(a.with_ros)
    if a.print_commands:
        return print_commands(cases[a.print_commands], a)
    if a.one:
        return run_one(cases[a.one], a.interface, a.channel, a.work, a.json, a.csv_dir)

    only = [x for x in a.only.split(',') if x]
    selected = [c for c in cases.values() if not only or any(c.id == x or c.id.startswith(x + '-') or c.id.startswith(x) and x.startswith('S14') for x in only)]
    work = a.work or tempfile.mkdtemp(prefix='r_race_')
    os.makedirs(work, exist_ok=True)
    rounds_path = os.path.join(work, 'rounds.json')
    if not a.report_only:
        rounds, t_start = [], time.monotonic()
        for r in range(1, a.rounds + 1):
            t0 = time.monotonic()
            res = run_round(r, selected, a, work)
            flat = [row | {'res': d['res'], 'ros': d['ros']} for d in res for row in d['rows']]
            flat += [row | {'res': {}, 'ros': False} for row in extra_rows(a, work, only)]
            rounds.append({'round': r, 'rows': flat, 'elapsed_s': time.monotonic() - t0})
            print(f'[{r}] 끝: {sum(x["ok"] for x in flat)}/{len(flat)} 통과, {time.monotonic() - t0:.0f} s', flush=True)
        meta = {'rev': git_rev(), 'interface': a.interface, 'with_ros': a.with_ros, 'total_s': time.monotonic() - t_start}
        with open(rounds_path, 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'rounds': rounds}, f, ensure_ascii=False)
    if a.no_report:
        print(f'작업 폴더: {work}')
        return 0
    from r_race_report import build_report
    with open(rounds_path, encoding='utf-8') as f:
        data = json.load(f)
    if os.path.isdir(os.path.join(work, 'csv')):
        shutil.copytree(os.path.join(work, 'csv'), os.path.join(a.out, 'csv'), dirs_exist_ok=True)
    build_report(data, a.out)
    print(f'작업 폴더: {work}\n보고서: {a.out}/README.md')
    bad = [x['id'] for x in data['rounds'][0]['rows'] if not x['ok']]
    return 1 if bad else 0


def print_commands(case, a):
    """프로세스 모드 인자가 실제 파서를 통과하는지 확인하고 명령을 찍는다. 아무것도 실행하지 않는다."""
    import can_guard
    names = {k: f'r{k[0]}TAG' for k in ('cmd', 'hb', 'estop', 'status')}
    raw = scenario_dict(case)
    h = raw.get('harness') or {}
    argv = guard_argv(case, 'socketcan', a.channel, names, '/tmp/ev.csv', h.get('race', True)) + [str(x) for x in h.get('guard_args') or []]
    can_guard.parse_args(argv)
    veh = vehicle_cli({**(h.get('vehicle') or {}), **case.vehicle}, a.channel, '/tmp/vehicle.cmd')
    fav.build_parser().parse_args(veh[2:])
    print('can_guard.py', ' '.join(argv))
    print('fake_a1_vehicle.py', ' '.join(veh[2:]))
    print('fake_race_server.py <시나리오 yaml> --channel', a.channel, '--quiet')
    return 0


if __name__ == '__main__':
    sys.exit(main())
