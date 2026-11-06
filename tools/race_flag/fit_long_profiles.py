#!/usr/bin/env python3
"""가감속 프로파일 추천 도구 — 주행 로그에서 `race_flag.yaml` 값을 추천한다(계획서 7절, D9).

    # 정식 실행: CAN + GNSS bag. 8종 검사를 전부 통과해야 추천을 낸다
    python3 tools/race_flag/fit_long_profiles.py --can <로그 또는 세션 폴더>... --bag <bag> --out reports/race_flag/<날짜>_profile_fit
    # bag 을 못 읽는 PC: GNSS CSV(extract.py 출력 또는 t,speed_mps,fix,pitch_deg)
    python3 tools/race_flag/fit_long_profiles.py --can <로그> --gnss-csv <csv> --out <폴더>
    # GNSS 없이 CAN 통계만 보기: 갱신 판정을 하지 않는다. 종료 코드 0, 모든 값은 '참고'
    python3 tools/race_flag/fit_long_profiles.py --can <로그> --can-only-preview --out <폴더>

종료 코드: 0 = 전 검사 통과(또는 --can-only-preview), 2 = 입력·사용법 오류, 3 = ⛔ 갱신 중단(UPDATE_BLOCKED.md).

**이 도구는 `race_flag.yaml` 과 이력 문서를 절대 고치지 않는다.** 읽기만 하고, 추천값과 근거를 --out 에 쓴다.
사람이 승인해서 yaml 을 고친 뒤 `docs/race_flag_profile_history.md` 에 직접 적는다(7.3절).

확인할 수 없는 검사는 통과가 아니다. 계획서 7.2 의 "확인할 수 없거나" 를 시간 어긋남뿐 아니라 8종 전부에 적용한다.
"""
import argparse
import csv
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

import numpy as np
import yaml

REPO = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(REPO, 'DBC')):   # 저장소 루트(DBC/ 가 있는 곳)까지 올라간다
    REPO = os.path.dirname(REPO)
sys.path.insert(0, os.path.join(REPO, 'tools', 'signal_audit'))
import dbc_lite  # noqa: E402

DBC_PATH = os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc')
DEFAULT_CURRENT = os.path.join(REPO, 'safety', 'can_guard', 'race_flag.yaml')

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3

# ── 7.2 오염 검사 기준(계획서 값, 전부 잠정). 합격 기준이므로 여기서 임의로 바꾸지 않는다.
FIX_RATIO_MIN = 0.90
DECEL_MAX_MPS2 = 4.5
ACCEL_MAX_MPS2 = 1.6
WHEEL_GNSS_MAX = 0.05
LAG_MAX_S = 0.05
MIN_EVENTS = 3
PITCH_MAX_DEG = 2.0
ABRUPT_MAX = 0.20
# ── 7.1 추천 규칙(계획서 값)
SLIP_MAX = 0.10
A_SLOW_MARGIN = 0.8
# ── 6.3 서행 단계(계획서 값)
STAGE2 = dict(runs=3, wobble=0.5, diff=0.02)
STAGE3 = dict(runs=3, wobble=0.4, diff=0.01)

# ── 계획서에 숫자가 없어 이 도구에서 정한 값(잠정, README 에 근거)
LOCK_SLIP = 0.20          # 바퀴 잠김으로 보는 슬립(브레이크 중 후륜이 GNSS 보다 20 % 느림)
FOREIGN_HZ = 70.0         # 0x210 프레임이 초당 이보다 많으면 송신자가 둘(11-04: 혼자 47~51, bsw5 겹침 97)
APP_PAD_S = 2.0           # 앱 켜짐 앞뒤로 버리는 시간(tools/drive_log/cannpz.py app_mask 와 같음)
DEFAULT_SCALE = 0.958     # GNSS 없을 때만 쓰는 0x201 ÷ GNSS(11-04, measurements/2026-11-04_drive_log/README.md)
DEFAULT_FIX_VALUES = (4,)  # INS status.gnss: 4 = RTK Fixed(11-04 전 구간). RTX 코드는 TODO-5 확정 때 --fix-values 로
GRID_S = 0.02

BRAKE_BINS = [(1, 20), (20, 40), (40, 60), (60, 82), (82, 101)]   # 82 % 이상은 위치 포화(CLAUDE.md 2절)
ACC_BINS = [(75, 85), (85, 95), (95, 101)]                        # 75 % 아래는 구동 없음(11-04)
RATE_BINS = [(0, 25), (25, 50), (50, 100), (100, 200), (200, math.inf)]   # %/s, 맨 끝 = 계단(램프 없음)

BRAKE_EVENT = dict(min_hold_s=1.0, skip_s=0.4, min_fit_s=0.5, min_speed=0.8, max_fit_s=4.0)
ACC_EVENT = dict(min_hold_s=1.5, skip_s=0.5, min_fit_s=0.8, min_speed=0.3, max_fit_s=4.0)
HOLD_TOL_PCT = 3.0

LINE = re.compile(r'^\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)')


class InputError(Exception):
    """입력 파일이 없거나 형식이 틀림 → 종료 코드 2."""


# ───────────────────────── 입력 ─────────────────────────

@dataclass
class CanLog:
    name: str
    path: str
    frames: int = 0
    t210: np.ndarray = None
    acc: np.ndarray = None
    brk: np.ndarray = None
    t200: np.ndarray = None
    brkpos: np.ndarray = None
    t201: np.ndarray = None
    vl: np.ndarray = None   # km/h
    vr: np.ndarray = None   # km/h
    t156: np.ndarray = None
    app_en: np.ndarray = None
    t127: np.ndarray = None
    res: np.ndarray = None
    t124: np.ndarray = None
    slow_on: np.ndarray = None


@dataclass
class Gnss:
    t: np.ndarray
    v: np.ndarray                  # m/s
    fix: np.ndarray = None         # bool, None = 품질 정보 없음
    pitch: np.ndarray = None       # deg, None = 피치 정보 없음
    source: str = ''


def read_lines(path):
    """평문 또는 .zst 로그의 줄을 돌려준다. 원본은 읽기만 한다."""
    if path.endswith('.zst'):
        try:
            out = subprocess.run(['zstd', '-dc', path], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            raise InputError(f'{path}: zstd 로 풀 수 없음 ({e})')
        return out.splitlines()
    try:
        with open(path, errors='replace') as f:
            return f.read().splitlines()
    except OSError as e:
        raise InputError(f'{path}: 읽을 수 없음 ({e})')


def resolve_can_paths(items):
    """파일은 그대로, 폴더는 세션 폴더로 보고 로그 하나를 고른다. candump 와 미러 기록은 같은 프레임이라 합치면 안 된다
    → tools/drive_log/cannpz.py 와 같이 더 긴 미러 기록(kvaser*)을 우선한다."""
    out = []
    for it in items:
        if os.path.isdir(it):
            fs = sorted((os.path.join(it, f) for f in os.listdir(it) if re.search(r'\.log(\.zst)?$', f)),
                        key=lambda p: ('kvaser' not in os.path.basename(p), p))
            if not fs:
                raise InputError(f'{it}: 로그 파일(*.log, *.log.zst)이 없음')
            out.append(fs[0])
        elif os.path.isfile(it):
            out.append(it)
        else:
            raise InputError(f'{it}: 없는 경로')
    return out


def log_name(path):
    base = os.path.basename(path)
    return f'{os.path.basename(os.path.dirname(os.path.abspath(path)))}/{base}'


def parse_can(lines, name='', path=''):
    db = dbc_lite.load(DBC_PATH)
    db.update(dbc_lite.remote_messages())   # 0x156/0x157 은 원격 정의 우선(dbc_lite 문서)
    m210, m200, m201 = db[0x210], db[0x200], db[0x201]
    rows = {k: [] for k in (0x210, 0x200, 0x201, 0x156, 0x127, 0x124)}
    n = 0
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            continue
        n += 1
        cid = int(m.group(2), 16)
        if cid not in rows:
            continue
        t, d = float(m.group(1)), bytes.fromhex(m.group(3))
        if cid == 0x210:
            s = m210.decode(d)
            rows[cid].append((t, s['acc_command'], s['break_command']))
        elif cid == 0x200:
            rows[cid].append((t, m200.decode(d)['break_postion']))
        elif cid == 0x201:
            s = m201.decode(d)
            rows[cid].append((t, s['left_speed'], s['right_speed']))
        elif cid == 0x156:
            s = db[0x156].decode(d)
            rows[cid].append((t, max(s['EPS_En'], s['ACC_En'], s['AEB_En'])))
        elif cid == 0x127:
            rows[cid].append((t, db[0x127].decode(d)['RES_START']))
        else:
            rows[cid].append((t, db[0x124].decode(d)['SIG_SLOW_ON']))

    def col(cid, width):
        return np.array(rows[cid], float).reshape(-1, width).T

    lg = CanLog(name=name, path=path, frames=n)
    lg.t210, lg.acc, lg.brk = col(0x210, 3)
    lg.t200, lg.brkpos = col(0x200, 2)
    lg.t201, lg.vl, lg.vr = col(0x201, 3)
    lg.t156, lg.app_en = col(0x156, 2)
    lg.t127, lg.res = col(0x127, 2)
    lg.t124, lg.slow_on = col(0x124, 2)
    return lg


def load_can(path):
    lg = parse_can(read_lines(path), log_name(path), path)
    if lg.frames == 0:
        raise InputError(f'{path}: CAN 프레임을 하나도 해석하지 못함(형식: "(ts) ch ID#DATA")')
    return lg


def _first(row, names):
    for n in names:
        if n in row:
            return n
    return None


def load_gnss_csv(path, fix_values=DEFAULT_FIX_VALUES):
    """GNSS CSV 어댑터. 열 이름은 tools/drive_log/extract.py 의 INS 출력(t,gnss,spd)과 표준 이름(t,speed_mps,fix,pitch_deg)을 받는다.
    fix·pitch 열이 없으면 None → 해당 검사는 '확인 불가'."""
    try:
        with open(path, errors='replace') as f:
            rd = csv.DictReader(ln for ln in f if not ln.startswith('#'))
            rows = list(rd)
            hdr = rd.fieldnames or []
    except OSError as e:
        raise InputError(f'{path}: 읽을 수 없음 ({e})')
    ct = _first(hdr, ('t', 'time', 't_s'))
    cv = _first(hdr, ('speed_mps', 'spd', 'v_mps'))
    cf = _first(hdr, ('fix', 'gnss'))
    cp = _first(hdr, ('pitch_deg', 'pitch'))
    if ct is None or cv is None or not rows:
        raise InputError(f'{path}: GNSS CSV 에 t 와 speed_mps(또는 spd) 열이 필요함, 받은 열 {hdr}')
    t = np.array([float(r[ct]) for r in rows])
    v = np.array([float(r[cv]) for r in rows])
    fix = np.isin(np.array([float(r[cf]) for r in rows]), fix_values) if cf else None
    pitch = np.array([float(r[cp]) for r in rows]) if cp else None
    order = np.argsort(t)
    return Gnss(t[order], v[order], None if fix is None else fix[order], None if pitch is None else pitch[order],
                f'csv:{os.path.basename(path)}')


def _pitch_from_quat(q):
    if math.sqrt(q.x ** 2 + q.y ** 2 + q.z ** 2 + q.w ** 2) < 0.5:
        return math.nan
    return math.degrees(math.asin(max(-1.0, min(1.0, 2.0 * (q.w * q.y - q.z * q.x)))))


def load_gnss_bag(uri, odom_topic, ins_topic, fix_values=DEFAULT_FIX_VALUES):
    """bag 어댑터. tools/track_day/fit_maps.py 의 read_bag(헤더 stamp 기준, 11-04 bag 밀림 대응)을 그대로 쓴다.
    INS 해(품질·피치 포함)를 먼저 찾고, 없으면 odom(품질 없음 → GNSS 품질 '확인 불가').
    돌려주는 값: (Gnss 또는 None, 이유 문자열). 메시지 정의가 없는 PC 에서는 None + 이유."""
    sys.path.insert(0, os.path.join(REPO, 'tools', 'track_day'))
    try:
        import fit_maps
        ins = fit_maps.read_bag(uri, [ins_topic])[ins_topic]
    except Exception as e:   # noqa: BLE001 — ROS 없음·메시지 정의 없음·손상 bag 전부 '확인 불가'
        ins, why = [], f'{type(e).__name__}: {e}'
    else:
        why = f'{ins_topic} 메시지 없음'
    if ins:
        t = np.array([x[0] for x in ins])
        v = np.array([float(m.total_speed) for _, m in ins])
        fix = np.isin(np.array([int(m.status.gnss) for _, m in ins]), fix_values)
        pitch = np.array([float(getattr(m, 'pitch', math.nan)) for _, m in ins])
        return Gnss(t, v, fix, None if not np.isfinite(pitch).any() else pitch, f'bag:{ins_topic}'), ''
    try:
        import fit_maps
        odom = fit_maps.read_bag(uri, [odom_topic])[odom_topic]
    except Exception as e:   # noqa: BLE001
        return None, f'bag 을 읽을 수 없음 ({why}; {type(e).__name__}: {e})'
    if not odom:
        return None, f'bag 에 GNSS 토픽이 없음 ({why}; {odom_topic} 메시지 없음)'
    t = np.array([x[0] for x in odom])
    v = np.array([math.hypot(m.twist.twist.linear.x, m.twist.twist.linear.y) for _, m in odom])
    pitch = np.array([_pitch_from_quat(m.pose.pose.orientation) for _, m in odom])
    # 평면 자세만 채우는 odom 은 피치가 전부 0 → 측정이 아니므로 '없음'으로 본다(가짜 통과 방지)
    usable = np.isfinite(pitch).any() and np.nanmax(np.abs(pitch)) > 0.0
    return Gnss(t, v, None, pitch if usable else None, f'bag:{odom_topic}'), ''


def load_events(paths, can_paths):
    """can_guard --event-log CSV(wall_s,event,detail). 지정이 없으면 CAN 로그 옆의 can_guard_events*.csv 를 찾는다."""
    if not paths:
        paths = []
        for p in can_paths:
            d = os.path.dirname(os.path.abspath(p))
            paths += [os.path.join(d, f) for f in sorted(os.listdir(d)) if re.match(r'can_guard_events.*\.csv$', f)]
    out = []
    for p in paths:
        try:
            with open(p, errors='replace') as f:
                for r in csv.DictReader(ln for ln in f if not ln.startswith('#')):
                    try:
                        out.append((float(r['wall_s']), r['event'], r.get('detail') or ''))
                    except (KeyError, TypeError, ValueError):
                        continue
        except OSError as e:
            raise InputError(f'{p}: 읽을 수 없음 ({e})')
    return sorted(out, key=lambda x: x[0])


def load_current(path):
    """기존 값 읽기 전용. 이 도구에 yaml 쓰기 코드는 없다."""
    try:
        with open(path) as f:
            d = yaml.safe_load(f) or {}
    except OSError as e:
        raise InputError(f'{path}: 기존 값 파일을 읽을 수 없음 ({e})')
    for k in ('a_slow', 'slow_off_ramp_pct_s', 'brake_map', 'accel_map'):
        if k not in d:
            raise InputError(f'{path}: 필수 키 없음 {k}')
    return d


# ───────────────────────── 격자 ─────────────────────────

@dataclass
class Span:
    kind: str
    t0: float
    t1: float


@dataclass
class Trace:
    log: CanLog
    t: np.ndarray
    vw: np.ndarray            # 후륜 좌우 평균 m/s (0x201 그대로, 보정 없음)
    acc: np.ndarray           # 0x210 %, 끊긴 곳 NaN
    brk: np.ndarray
    brkpos: np.ndarray        # 0x200 %
    vg: np.ndarray = None     # GNSS m/s, 공백 NaN. GNSS 가 없으면 전부 NaN
    fix: np.ndarray = None
    pitch: np.ndarray = None
    vref: np.ndarray = None   # 가감속 계산에 쓰는 속도(GNSS 우선, 없으면 후륜 ÷ 배율)
    aref: np.ndarray = None   # vref 미분. 평활은 tools/drive_log/remote.py 와 같게(속도 0.25 s + 가속도 0.25 s)
    bad: np.ndarray = None    # 앱·RES·ESTOP·겹친 0x210 구간(통계에서 제외)
    spans: list = field(default_factory=list)


def _hold(t_src, x_src, t, max_age):
    """t 격자에 직전 값을 잡는다. 표본이 max_age 보다 오래됐으면 NaN(끊긴 값을 이어 붙이지 않는다)."""
    out = np.full(len(t), np.nan)
    if len(t_src) == 0:
        return out
    i = np.searchsorted(t_src, t, side='right') - 1
    ok = (i >= 0) & (t - t_src[np.clip(i, 0, None)] <= max_age)
    out[ok] = x_src[i[ok]]
    return out


def smooth(x, n):
    """NaN 을 빼고 평균하는 이동평균(폭 n 표본)."""
    if n <= 1:
        return x.copy()
    ok = np.isfinite(x)
    k = np.ones(n)
    num = np.convolve(np.where(ok, x, 0.0), k, 'same')
    den = np.convolve(ok.astype(float), k, 'same')
    with np.errstate(invalid='ignore', divide='ignore'):
        out = num / den
    out[den < n / 2] = np.nan
    return out


def mask_runs(m):
    """bool 배열의 True 구간 [(시작, 끝 포함)]."""
    d = np.diff(np.r_[False, m, False].astype(int))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0] - 1))


def _group_times(t, gap):
    """시각 배열을 gap 보다 크게 벌어진 곳에서 나눈다."""
    if len(t) == 0:
        return []
    br = np.where(np.diff(t) > gap)[0]
    return list(zip(np.r_[t[0], t[br + 1]], np.r_[t[br], t[-1]]))


def find_spans(lg, events):
    """통계에서 뺄 '다른 조작' 구간. 앱·RES·0x210 겹침은 CAN 에서, ESTOP 은 이벤트 기록에서."""
    spans = []
    on = lg.t156[lg.app_en > 0.5]
    for a, b in _group_times(on, 0.5):
        spans.append(Span('앱 0x156 En=1', a - APP_PAD_S, b + APP_PAD_S))
    on = lg.t127[lg.res > 0.5]
    for a, b in _group_times(on, 0.5):
        spans.append(Span('RES 0x127 RES_START=1', a - APP_PAD_S, b + APP_PAD_S))
    if len(lg.t210) > 1:
        t0 = lg.t210[0]
        cnt = np.bincount(np.floor(lg.t210 - t0).astype(int))
        for a, b in mask_runs(cnt > FOREIGN_HZ):
            spans.append(Span(f'0x210 송신자 둘(초당 최대 {cnt[a:b + 1].max()}프레임)', t0 + a, t0 + b + 1))
    if len(lg.t201):
        tlo, thi = lg.t201[0], lg.t201[-1]
        for t, ev, detail in events:
            if not tlo - 1 <= t <= thi + 1:
                continue
            if ev == 'estop_request' or '->ESTOP' in detail:
                spans.append(Span(f'ESTOP({ev})', t, thi))   # 래치라 로그 끝까지 정상 구간이 아니다
            elif ev == 'foreign_0x210':
                spans.append(Span('이벤트 foreign_0x210', t - 1.0, t + 1.0))
            elif ev.startswith('res') or '->SUSPENDED' in detail:
                spans.append(Span(f'RES({ev})', t, thi))
    return spans


def events_cover(lg, events):
    """이 로그 시간대의 can_guard 이벤트가 있는가. 없으면 ESTOP 은 확인 불가."""
    if not len(lg.t201):
        return False
    return any(lg.t201[0] - 60 <= t <= lg.t201[-1] + 60 for t, _, _ in events)


def make_trace(lg, gnss, events, scale=DEFAULT_SCALE):
    if len(lg.t201) < 10:
        return None
    t = np.arange(lg.t201[0], lg.t201[-1], GRID_S)
    vw = (_hold(lg.t201, lg.vl, t, 0.2) + _hold(lg.t201, lg.vr, t, 0.2)) / 2 / 3.6
    tr = Trace(lg, t, vw, _hold(lg.t210, lg.acc, t, 0.2), _hold(lg.t210, lg.brk, t, 0.2), _hold(lg.t200, lg.brkpos, t, 0.2))
    tr.vg = np.full(len(t), np.nan)
    if gnss is not None and len(gnss.t) >= 2:
        tr.vg = np.interp(t, gnss.t, gnss.v, left=np.nan, right=np.nan)
        i = np.clip(np.searchsorted(gnss.t, t), 1, len(gnss.t) - 1)
        tr.vg[gnss.t[i] - gnss.t[i - 1] > 0.5] = np.nan   # GNSS 공백을 직선으로 메우지 않는다
        if gnss.fix is not None:
            tr.fix = _hold(gnss.t, gnss.fix.astype(float), t, 0.5)
        if gnss.pitch is not None:
            tr.pitch = _hold(gnss.t, gnss.pitch, t, 0.5)
    tr.spans = find_spans(lg, events)
    tr.bad = np.zeros(len(t), bool)
    for s in tr.spans:
        tr.bad |= (t >= s.t0) & (t <= s.t1)
    set_vref(tr, scale)
    return tr


def set_vref(tr, scale):
    tr.vref = np.where(np.isfinite(tr.vg), tr.vg, np.nan) if np.isfinite(tr.vg).any() else tr.vw / scale
    n = int(0.25 / GRID_S)
    tr.aref = smooth(np.gradient(smooth(tr.vref, n), GRID_S), n)


# ───────────────────────── 이벤트 ─────────────────────────

def hold_segments(cmd, other, bad, dt, min_s, tol=HOLD_TOL_PCT, lo=1.0):
    """cmd 가 ±tol 안에서 min_s 이상 유지되고 other = 0 이고 제외 구간이 아닌 [(i0, i1)]. 첫 값 기준(fit_maps.segments 와 같은 뜻)."""
    valid = np.isfinite(cmd) & (cmd >= lo) & (other == 0) & ~bad
    out, i, n = [], 0, len(cmd)
    while i < n:
        if not valid[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and valid[j + 1] and abs(cmd[j + 1] - cmd[i]) <= tol:
            j += 1
        if (j - i) * dt >= min_s:
            out.append((i, j))
        i = j + 1
    return out


def slip_series(tr, scale):
    """슬립률 = (후륜 − GNSS) ÷ GNSS. 후륜을 정속 비율(scale)로 먼저 맞춘다 — 0x201 이 4.2 % 낮게 읽어 그대로 쓰면 휠스핀이 작아 보인다.
    GNSS 2 km/h 미만은 분해능 잡음이라 버린다."""
    n = int(round(0.2 / GRID_S))
    vg, vw = smooth(tr.vg, n), smooth(tr.vw, n) / scale
    with np.errstate(invalid='ignore', divide='ignore'):
        s = (vw - vg) / vg
    s[~(vg > 2.0 / 3.6)] = np.nan
    return s


def axis_events(tr, kind, scale, have_gnss):
    """유지 구간 하나 = 이벤트 하나. kind: brake | acc."""
    cfg, cmd, other = (BRAKE_EVENT, tr.brk, tr.acc) if kind == 'brake' else (ACC_EVENT, tr.acc, tr.brk)
    slip = slip_series(tr, scale) if have_gnss else None
    out = []
    for i0, i1 in hold_segments(cmd, other, tr.bad, GRID_S, cfg['min_hold_s']):
        a = i0 + int(round(cfg['skip_s'] / GRID_S))
        b = min(i1, a + int(round(cfg['max_fit_s'] / GRID_S)))
        idx = np.arange(a, b + 1)
        idx = idx[np.isfinite(tr.vref[idx]) & (tr.vref[idx] >= cfg['min_speed'])]
        if len(idx) * GRID_S < cfg['min_fit_s']:
            continue
        slope = float(np.polyfit(tr.t[idx], tr.vref[idx], 1)[0])
        ev = dict(log=tr.log.name, kind=kind, t0=float(tr.t[i0]), t1=float(tr.t[i1]), pct=float(np.median(cmd[i0:i1 + 1])),
                  value=-slope if kind == 'brake' else slope, v_kph=float(np.mean(tr.vref[idx]) * 3.6), slip=math.nan)
        if slip is not None:
            s = slip[i0:i1 + 1]
            if np.isfinite(s).any():
                ev['slip'] = float(np.nanmin(s) if kind == 'brake' else np.nanmax(s))
        out.append(ev)
    return out


def acc_rises(tr, scale, have_gnss, min_rise=20.0, end_min=75.0, window_s=3.0):
    """가속 % 가 오르는 구간(Slow Off 복귀와 같은 모양): 상승률과 그 뒤 최대 슬립."""
    acc = tr.acc
    slip = slip_series(tr, scale) if have_gnss else None
    out, i, n = [], 0, len(acc)
    gap = int(round(0.1 / GRID_S))
    while i < n - 1:
        if not (np.isfinite(acc[i]) and np.isfinite(acc[i + 1]) and acc[i + 1] > acc[i]):
            i += 1
            continue
        j, last = i + 1, i + 1
        while j + 1 < n and j + 1 - last <= gap and np.isfinite(acc[j + 1]) and acc[j + 1] >= acc[last]:
            j += 1
            if acc[j] > acc[last]:
                last = j
        rise, dur = acc[last] - acc[i], (last - i) * GRID_S
        end = min(n, last + int(round(window_s / GRID_S)))
        clean = not tr.bad[i:end].any() and np.nanmax(np.nan_to_num(tr.brk[i:end])) == 0
        if rise >= min_rise and acc[last] >= end_min and clean:
            # 한두 프레임 만에 오르면 계단: 격자 분해능(20 ms)으로 잰 상승률은 뜻이 없다
            rate = math.inf if dur <= 2 * GRID_S else rise / dur
            s = math.nan
            if slip is not None and np.isfinite(slip[i:end]).any():
                s = float(np.nanmax(slip[i:end]))
            out.append(dict(log=tr.log.name, kind='rise', t0=float(tr.t[i]), t1=float(tr.t[last]), rate=rate,
                            pct=float(acc[last]), slip=s))
        i = last + 1
    return out


def brake_delays(tr):
    """브레이크 계단(한 프레임에 20 %p 이상, 5 % 미만에서 시작, drive_log brake_steps 와 같은 기준) → 0x200 위치가 2 %(옛 단위 0.2) 오를 때까지. 격자 20 ms 분해능."""
    out = []
    b, p = tr.brk, tr.brkpos
    for i in np.where((b[1:] - b[:-1] >= 20) & (b[:-1] < 5) & ~tr.bad[:-1])[0] + 1:
        if not np.isfinite(p[i]):
            continue
        after = np.where(p[i:i + 50] > p[i] + 2.0)[0]
        if len(after):
            out.append(float(after[0] * GRID_S))
    return out


def bin_events(events, bins):
    """% 구간별 표본·중앙값. 하한 ≤ pct < 상한."""
    out = []
    for lo, hi in bins:
        ev = [e for e in events if lo <= e['pct'] < hi]
        vals = [e['value'] for e in ev]
        out.append(dict(lo=lo, hi=hi, n=len(ev), pct=float(np.median([e['pct'] for e in ev])) if ev else math.nan,
                        value=float(np.median(vals)) if ev else math.nan,
                        p25=float(np.percentile(vals, 25)) if ev else math.nan,
                        p75=float(np.percentile(vals, 75)) if ev else math.nan))
    return out


def monotone_table(bins, x_key, y_key, start=None):
    """x(가감속)·y(%) 모두 오름차순인 표. 뒤집힌 점(잡음)은 뺀다(fit_maps.cmd_long 과 같은 규칙). 표본 부족 구간은 쓰지 않는다."""
    xs, ys = ([start[0]], [start[1]]) if start else ([], [])
    dropped = []
    for b in bins:
        if b['n'] < MIN_EVENTS:
            continue
        x, y = round(b['value'], 2), round(b['pct'], 1)
        if x > 0 and (not xs or (x > xs[-1] and y > ys[-1])):
            xs.append(x)
            ys.append(y)
        else:
            dropped.append((y, x))
    return {x_key: xs, y_key: ys}, dropped


def recommend_a_slow(brake_events, brake_bins, have_gnss):
    """a_slow = (잠김 직전 구간의 감속도) × 0.8. 표본이 충분한 구간만 쓴다."""
    locked = [b for b in brake_bins if have_gnss and
              any(b['lo'] <= e['pct'] < b['hi'] and e['slip'] <= -LOCK_SLIP for e in brake_events)]
    lock_lo = min((b['lo'] for b in locked), default=None)
    usable = [b for b in brake_bins if b['n'] >= MIN_EVENTS and b['value'] > 0 and (lock_lo is None or b['hi'] <= lock_lo)]
    if not usable:
        return dict(value=None, lock_start_pct=lock_lo, base=None, note='잠기지 않은 구간 중 표본이 충분한 곳이 없음')
    base = max(usable, key=lambda b: b['value'])
    if not have_gnss:
        note = '잠김 판정 불가(GNSS 없음): 표본이 충분한 구간의 최대 감속 기준'
    elif lock_lo is None:
        note = '잠김 미관측: 표본이 충분한 구간의 최대 감속 기준'
    else:
        note = f'잠김 시작 {lock_lo} % 구간 직전 기준'
    return dict(value=round(base['value'] * A_SLOW_MARGIN, 2), lock_start_pct=lock_lo,
                base=dict(lo=base['lo'], hi=base['hi'], decel=base['value']), note=note)


def recommend_ramp(rises, acc_events):
    """slow_off_ramp_pct_s = 슬립 ≤ 10 % 를 지키는 가장 빠른 값. 표본 3회 미만 구간은 근거로 쓰지 않고, 슬립 초과가 나온 구간부터 위는 믿지 않는다."""
    rows = []
    for lo, hi in RATE_BINS:
        ev = [r for r in rises if lo < r['rate'] <= hi and np.isfinite(r['slip'])]
        rows.append(dict(lo=lo, hi=hi, n=len(ev), n_all=sum(lo < r['rate'] <= hi for r in rises), max_slip=max((r['slip'] for r in ev), default=math.nan),
                         rate=float(np.median([r['rate'] for r in ev])) if ev else math.nan))
    best = None
    for row in rows:
        if row['n'] < MIN_EVENTS or row['max_slip'] > SLIP_MAX:
            if row['n'] and row['max_slip'] > SLIP_MAX:
                break
            continue
        best = row
    spin = [e['pct'] for e in acc_events if np.isfinite(e['slip']) and e['slip'] > SLIP_MAX]
    if best is None:
        value, note = None, '슬립 10 % 이하가 표본 3회 이상 확인된 상승률 구간이 없음'
    elif math.isinf(best['hi']):
        value, note = math.inf, '계단(램프 없음)도 슬립 10 % 이하'
    else:
        value, note = round(best['rate'], 1), f"{best['lo']}~{best['hi']} %/s 구간 중앙값, 최대 슬립 {best['max_slip'] * 100:.1f} %"
    return dict(value=value, rows=rows, spin_start_pct=min(spin) if spin else None, note=note)


# ───────────────────────── 정속·동기 ─────────────────────────

def constant_speed_segments(tr, min_s=3.0, min_kph=5.0, max_accel=0.1):
    """정속 구간: GNSS·후륜이 둘 다 있고, 가속도 |a| ≤ 0.1 m/s² 가 3 s 이상. 5 km/h 아래는 0x201 분해능(0.1 km/h)이 2 % 를 넘어 뺀다."""
    m = np.isfinite(tr.vg) & np.isfinite(tr.vw) & (tr.vg > min_kph / 3.6) & ~tr.bad
    with np.errstate(invalid='ignore'):
        m &= np.abs(np.gradient(smooth(tr.vg, 25), GRID_S)) <= max_accel
    out = []
    for a, b in mask_runs(m):
        if (b - a) * GRID_S >= min_s:
            vg, vw = float(np.mean(tr.vg[a:b + 1])), float(np.mean(tr.vw[a:b + 1]))
            out.append(dict(log=tr.log.name, t0=float(tr.t[a]), t1=float(tr.t[b]), gnss_kph=vg * 3.6, wheel_kph=vw * 3.6,
                            ratio=vw / vg))
    return out


def estimate_lag(tr, scale=1.0, max_lag_s=2.0, min_overlap_s=20.0, min_corr=0.7):
    """후륜과 GNSS 의 시간 차(가속도 상호상관). 양수 = GNSS 가 CAN 보다 늦게 찍힘.
    돌려주는 값: (lag_s 또는 None, 상관, 이유). 값에는 기록 시각 차이와 센서 필터 지연이 같이 들어 있다.
    잠김·휠스핀 구간은 후륜이 차를 따르지 않아 값을 끌어가므로 뺀다(±0.5 s).
    슬립을 뺀 뒤 겹침이 모자라면 '확인 불가'가 된다 — 큰 시간 차도 이렇게 드러난다."""
    if not np.isfinite(tr.vg).any():
        return None, math.nan, 'GNSS 와 CAN 시간대가 겹치지 않음'
    n = int(round(0.25 / GRID_S))
    aw = np.gradient(smooth(tr.vw, n), GRID_S)
    ag = np.gradient(smooth(tr.vg, n), GRID_S)
    with np.errstate(invalid='ignore', divide='ignore'):
        slipping = (tr.vg > 1.0) & (np.abs(tr.vw / scale - tr.vg) / tr.vg > SLIP_MAX)
    k = int(round(0.5 / GRID_S))
    slipping = np.convolve(slipping.astype(int), np.ones(2 * k + 1, int), 'same') > 0
    aw[slipping] = np.nan
    ok = np.isfinite(aw) & np.isfinite(ag)
    if ok.sum() * GRID_S < min_overlap_s:
        return None, math.nan, f'겹치는 시간 {ok.sum() * GRID_S:.1f} s < {min_overlap_s:.0f} s'
    if np.std(ag[ok]) < 0.05:
        return None, math.nan, '속도 변화가 거의 없어 시간 차를 잴 수 없음'
    m = int(round(max_lag_s / GRID_S))
    lags = np.arange(-m, m + 1)
    corr = np.full(len(lags), np.nan)
    for k, lag in enumerate(lags):
        if lag >= 0:
            a, b = aw[:len(aw) - lag], ag[lag:]
        else:
            a, b = aw[-lag:], ag[:lag]
        s = np.isfinite(a) & np.isfinite(b)
        if s.sum() * GRID_S >= min_overlap_s / 2:
            corr[k] = np.corrcoef(a[s], b[s])[0, 1]
    if not np.isfinite(corr).any():
        return None, math.nan, '상관을 계산할 수 없음'
    k = int(np.nanargmax(corr))
    if corr[k] < min_corr:
        return None, float(corr[k]), f'상관 {corr[k]:.2f} < {min_corr} — 같은 움직임으로 보기 어려움'
    if k in (0, len(lags) - 1):
        return None, float(corr[k]), f'최대 상관이 탐색 범위 끝(±{max_lag_s:.0f} s) — 실제 차이는 더 클 수 있음'
    frac = 0.0
    if np.isfinite(corr[k - 1]) and np.isfinite(corr[k + 1]):
        den = corr[k - 1] - 2 * corr[k] + corr[k + 1]
        if den < 0:
            frac = 0.5 * (corr[k - 1] - corr[k + 1]) / den
    return float((lags[k] + frac) * GRID_S), float(corr[k]), ''


def analysis_mask(tr, events):
    """분석 구간 = 쓰인 이벤트 시간. 이벤트가 없으면 움직인 시간 전체."""
    m = np.zeros(len(tr.t), bool)
    for e in events:
        if e['log'] == tr.log.name:
            m |= (tr.t >= e['t0']) & (tr.t <= e['t1'])
    if not m.any():
        m = np.nan_to_num(tr.vw) > 0.5 / 3.6
    return m & ~tr.bad


# ───────────────────────── 검사 8종 ─────────────────────────

PASS, FAIL, UNKNOWN = '통과', '걸림', '확인 불가'


@dataclass
class Check:
    key: str
    name: str
    status: str
    detail: str
    can_only: bool       # GNSS 없이 판정 가능한 검사
    spans: list = field(default_factory=list)


def fmt_span(tr, t0, t1):
    return f'{tr.log.name} {t0 - tr.t[0]:.1f}~{t1 - tr.t[0]:.1f} s (unix {t0:.1f}~{t1:.1f})'


def check_gnss_quality(traces, events, gnss, why):
    if gnss is None:
        return Check('gnss_quality', 'GNSS 품질', UNKNOWN, f'GNSS 입력 없음({why})', False)
    if gnss.fix is None:
        return Check('gnss_quality', 'GNSS 품질', UNKNOWN, f'{gnss.source} 에 fix 품질 정보 없음', False)
    n_ok, n_all, spans = 0, 0, []
    for tr in traces:
        m = analysis_mask(tr, events)
        if tr.fix is None:
            continue
        n_all += int(m.sum())
        n_ok += int((np.nan_to_num(tr.fix[m]) > 0.5).sum())
        for a, b in mask_runs(m & ~(np.nan_to_num(tr.fix) > 0.5)):
            if (b - a) * GRID_S >= 0.5:
                spans.append(fmt_span(tr, tr.t[a], tr.t[b]))
    if n_all == 0:
        return Check('gnss_quality', 'GNSS 품질', UNKNOWN, '분석 구간과 겹치는 GNSS 표본이 없음', False)
    ratio = n_ok / n_all
    status = PASS if ratio >= FIX_RATIO_MIN else FAIL
    return Check('gnss_quality', 'GNSS 품질', status, f'분석 구간 fix 비율 {ratio * 100:.1f} % (기준 ≥ {FIX_RATIO_MIN * 100:.0f} %)', False, spans[:20])


def check_mixed_can(traces):
    spans = [f'{s.kind}: {fmt_span(tr, s.t0, s.t1)}' for tr in traces for s in tr.spans if not s.kind.startswith('ESTOP')]
    if spans:
        return Check('mixed_can', '다른 조작 섞임(앱·RES·0x210 송신자)', FAIL, f'{len(spans)}개 구간', True, spans)
    return Check('mixed_can', '다른 조작 섞임(앱·RES·0x210 송신자)', PASS, '앱 En=0, RES 없음, 0x210 초당 프레임 정상', True)


def check_mixed_estop(traces, events):
    name = '다른 조작 섞임(ESTOP)'
    spans = [f'{s.kind}: {fmt_span(tr, s.t0, s.t1)}' for tr in traces for s in tr.spans if s.kind.startswith('ESTOP')]
    if spans:
        return Check('mixed_estop', name, FAIL, f'{len(spans)}개 구간', False, spans)
    miss = [tr.log.name for tr in traces if not events_cover(tr.log, events)]
    if miss:
        # estop.py·SIGUSR1 원천의 ESTOP 은 CAN 에서 구별되지 않는다 → can_guard 이벤트 기록이 있어야 확인된다
        return Check('mixed_estop', name, UNKNOWN, 'can_guard 이벤트 기록(--events)이 없는 로그: ' + ', '.join(miss), False)
    return Check('mixed_estop', name, PASS, '이벤트 기록에 ESTOP 없음', False)


def check_physical(traces, have_gnss):
    amax, dmax, where_a, where_d = 0.0, 0.0, '', ''
    for tr in traces:
        m = ~tr.bad & (np.nan_to_num(tr.vref) > 0.5 / 3.6) & np.isfinite(tr.aref)
        if not m.any():
            continue
        idx = np.where(m)[0]
        ia, id_ = idx[np.argmax(tr.aref[idx])], idx[np.argmin(tr.aref[idx])]
        if tr.aref[ia] > amax:
            amax, where_a = float(tr.aref[ia]), fmt_span(tr, tr.t[ia], tr.t[ia])
        if -tr.aref[id_] > dmax:
            dmax, where_d = float(-tr.aref[id_]), fmt_span(tr, tr.t[id_], tr.t[id_])
    src = 'GNSS' if have_gnss else '후륜 0x201(GNSS 없음)'
    detail = (f'{src} 최대 가속 {amax:.2f} m/s² (기준 ≤ {ACCEL_MAX_MPS2}), 최대 감속 {dmax:.2f} m/s² (기준 ≤ {DECEL_MAX_MPS2})')
    status = PASS if amax <= ACCEL_MAX_MPS2 and dmax <= DECEL_MAX_MPS2 else FAIL
    spans = []
    if amax > ACCEL_MAX_MPS2:
        spans.append(f'가속 {amax:.2f} m/s²: {where_a}')
    if dmax > DECEL_MAX_MPS2:
        spans.append(f'감속 {dmax:.2f} m/s²: {where_d}')
    return Check('physical', '물리 한계', status, detail, True, spans)


def check_sensor(segs, have_gnss):
    name = '센서 불일치'
    if not have_gnss:
        return Check('sensor', name, UNKNOWN, 'GNSS 없음 — 휠속과 비교할 수 없음', False)
    if not segs:
        return Check('sensor', name, UNKNOWN, '정속 구간(3 s 이상, 5 km/h 이상)이 없음', False)
    worst = max(segs, key=lambda s: abs(s['ratio'] - 1))
    over = [s for s in segs if abs(s['ratio'] - 1) > WHEEL_GNSS_MAX]
    detail = (f'정속 {len(segs)}구간, 휠÷GNSS 중앙값 {np.median([s["ratio"] for s in segs]):.3f}, '
              f'최대 차이 {abs(worst["ratio"] - 1) * 100:.1f} % (기준 ≤ {WHEEL_GNSS_MAX * 100:.0f} %)')
    spans = [f'{s["log"]} unix {s["t0"]:.1f}~{s["t1"]:.1f}: 비율 {s["ratio"]:.3f}' for s in over[:20]]
    return Check('sensor', name, FAIL if over else PASS, detail, False, spans)


def check_time(traces, have_gnss, lags):
    name = '시간 어긋남'
    if not have_gnss:
        return Check('time', name, UNKNOWN, 'GNSS 없음 — CAN 로그와 bag 의 시각 차이를 확인할 수 없음', False)
    spans, unknown, worst = [], [], 0.0
    for tr in traces:
        lag, corr, why = lags[tr.log.name]
        if lag is None:
            unknown.append(f'{tr.log.name}: {why}')
        else:
            worst = max(worst, abs(lag))
            if abs(lag) > LAG_MAX_S:
                spans.append(f'{tr.log.name}: {lag * 1000:+.0f} ms (상관 {corr:.2f})')
    if spans:
        return Check('time', name, FAIL, f'최대 {worst * 1000:.0f} ms (기준 ≤ {LAG_MAX_S * 1000:.0f} ms)', False, spans + unknown)
    if unknown:
        return Check('time', name, UNKNOWN, '확인할 수 없는 로그 있음', False, unknown)
    return Check('time', name, PASS, f'최대 {worst * 1000:.0f} ms (기준 ≤ {LAG_MAX_S * 1000:.0f} ms)', False)


def check_samples(brake_bins, acc_bins):
    short = [f'브레이크 {b["lo"]}~{b["hi"]} %: {b["n"]}회' for b in brake_bins if b['n'] < MIN_EVENTS]
    short += [f'가속 {b["lo"]}~{b["hi"]} %: {b["n"]}회' for b in acc_bins if b['n'] < MIN_EVENTS]
    if short:
        return Check('samples', '표본 부족', FAIL, f'{len(short)}개 구간이 {MIN_EVENTS} 이벤트 미만', True, short)
    return Check('samples', '표본 부족', PASS, f'모든 % 구간 ≥ {MIN_EVENTS} 이벤트', True)


def check_pitch(traces, events, gnss, why):
    name = '경사'
    if gnss is None:
        return Check('pitch', name, UNKNOWN, f'GNSS 입력 없음({why})', False)
    if gnss.pitch is None:
        return Check('pitch', name, UNKNOWN, f'{gnss.source} 에 INS 피치 없음', False)
    spans, worst, seen = [], 0.0, 0
    for tr in traces:
        if tr.pitch is None:
            continue
        m = analysis_mask(tr, events) & np.isfinite(tr.pitch)
        seen += int(m.sum())
        if m.any():
            worst = max(worst, float(np.max(np.abs(tr.pitch[m]))))
        for a, b in mask_runs(m & (np.abs(np.nan_to_num(tr.pitch)) > PITCH_MAX_DEG)):
            spans.append(fmt_span(tr, tr.t[a], tr.t[b]))
    if seen == 0:
        return Check('pitch', name, UNKNOWN, '분석 구간과 겹치는 피치 표본이 없음', False)
    return Check('pitch', name, FAIL if spans else PASS, f'분석 구간 최대 |피치| {worst:.2f}° (기준 ≤ {PITCH_MAX_DEG}°)', False, spans[:20])


def map_at(table, x_key, y_key, x):
    return float(np.interp(x, table[x_key], table[y_key]))


def changed(old, new):
    """변화율. 무한(램프 없음)으로 바뀌면 무한."""
    if new is None or old is None:
        return None
    if math.isinf(new) or math.isinf(old):
        return 0.0 if new == old else math.inf
    if old == 0:
        return 0.0 if new == 0 else math.inf
    return abs(new - old) / abs(old)


def abrupt_rows(current, rec):
    """기존 → 추천 비교 행. 표는 추천 표의 각 가감속 점에서 기존 표의 % 와 비교한다."""
    rows = [dict(name='a_slow (m/s²)', old=float(current['a_slow']), new=rec['a_slow']['value']),
            dict(name='slow_off_ramp_pct_s (%/s)', old=float(current['slow_off_ramp_pct_s']), new=rec['ramp']['value'])]
    pairs = (('brake_map', 'decel_mps2', 'brake_pct', '브레이크표'), ('accel_map', 'accel_mps2', 'acc_pct', '가속표'))
    for key, xk, yk, label in pairs:
        cur, new = current[key], rec[key]
        for x, y in zip(new[xk], new[yk]):
            if y > 0:
                rows.append(dict(name=f'{label} {x:.2f} m/s² 의 %', old=round(map_at(cur, xk, yk, x), 1), new=y))
    for r in rows:
        r['change'] = changed(r['old'], r['new'])
    return rows


def check_abrupt(rows):
    big = [r for r in rows if r['change'] is not None and r['change'] > ABRUPT_MAX]
    spans = [f"{r['name']}: {r['old']} → {r['new']} ({r['change'] * 100:.0f} %)" if not math.isinf(r['change'])
             else f"{r['name']}: {r['old']} → {r['new']}" for r in big]
    if big:
        return Check('abrupt', '급변', FAIL, f'{len(big)}개 값이 {ABRUPT_MAX * 100:.0f} % 넘게 바뀜 → 자동 판정 금지, 사람 확인 요청', True, spans)
    return Check('abrupt', '급변', PASS, f'모든 추천값 변화 ≤ {ABRUPT_MAX * 100:.0f} %', True)


def slow_runs(traces, band=(6.5, 9.8), min_s=5.0, drop_s=2.0):
    """서행 구간: 후륜 속도(can_guard 가 보는 값)가 band 안에 min_s 이상. 앞 drop_s 는 버린다. 0x124 SLOW_ON 이 있으면 그 안만."""
    out = []
    for tr in traces:
        kph = tr.vw * 3.6
        m = (kph >= band[0]) & (kph <= band[1]) & ~tr.bad
        with np.errstate(invalid='ignore'):
            m &= np.abs(np.gradient(smooth(tr.vw, 100), GRID_S)) <= 0.15
        if len(tr.log.t124) and tr.log.slow_on.max() > 0:
            m &= _hold(tr.log.t124, tr.log.slow_on, tr.t, 0.5) > 0.5
        for a, b in mask_runs(m):
            a2 = a + int(round(drop_s / GRID_S))
            if (b - a2) * GRID_S >= min_s:
                x = kph[a2:b + 1]
                dev = np.abs(x - np.nanmedian(x))
                out.append(dict(log=tr.log.name, t0=float(tr.t[a2]), t1=float(tr.t[b]), median_kph=float(np.nanmedian(x)),
                                wobble_p999=float(np.nanpercentile(dev, 99.9))))
    return out


def judge_stage(runs, wheel_gnss_diff):
    """6.3 절 단계. 3단계는 KIAPI 답변(질의서 7 판정 기준·허용 오차)이 있어야 하므로 도구가 3으로 올리지 않는다."""
    wobble = max((r['wobble_p999'] for r in runs), default=None)
    def ok(c):
        return len(runs) >= c['runs'] and wobble is not None and wobble <= c['wobble'] and \
            wheel_gnss_diff is not None and wheel_gnss_diff <= c['diff']
    stage = 2 if ok(STAGE2) else 1
    note = ''
    if ok(STAGE3):
        note = '3단계 수치 조건 충족 — KIAPI 질의서 7(판정 기준·허용 오차) 답변이 있어야 올릴 수 있음'
    return dict(stage=stage, runs=len(runs), wobble_p999=wobble, wheel_gnss_diff=wheel_gnss_diff, note=note)


# ───────────────────────── 출력 ─────────────────────────

def git_commit():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return '(알 수 없음)'


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def fmt(v, nd=2):
    if v is None:
        return '—'
    if isinstance(v, float) and math.isnan(v):
        return '—'
    if isinstance(v, float) and math.isinf(v):
        return '무한(램프 없음)'
    return f'{v:.{nd}f}' if isinstance(v, float) else str(v)


def md_table(header, rows):
    out = ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
    out += ['| ' + ' | '.join(str(c).replace('|', '/') for c in r) + ' |' for r in rows]
    return '\n'.join(out)


def checks_table(checks):
    return md_table(['검사', '결과', 'CAN 만으로 판정', '내용'],
                    [(c.name, c.status, '가능' if c.can_only else '불가', c.detail) for c in checks])


def write_blocked(out_dir, checks, inputs):
    bad = [c for c in checks if c.status != PASS]
    with open(os.path.join(out_dir, 'UPDATE_BLOCKED.md'), 'w') as f:
        f.write('# ⛔ 갱신 중단 — race_flag.yaml 값을 바꾸지 않는다\n\n')
        f.write(f'작성 {datetime.datetime.now():%Y-%m-%d %H:%M} · 커밋 {git_commit()} · `fit_long_profiles.py`, 계획서 7.2\n\n')
        f.write('입력: ' + ', '.join(f'`{i}`' for i in inputs) + '\n\n')
        f.write('## 걸린 검사\n\n')
        for c in bad:
            f.write(f'### {c.name} — {c.status}\n\n{c.detail}\n\n')
            for s in c.spans:
                f.write(f'- {s}\n')
            if c.spans:
                f.write('\n')
        if any(c.key == 'abrupt' for c in bad):
            f.write('## 사람 확인 요청\n\n급변 항목은 도구가 판정하지 않는다. 근거 그래프와 `report.md` 를 보고 사람이 결정한다.\n\n')
        f.write('## 전체 검사\n\n' + checks_table(checks) + '\n')


def report_lines(args, inputs, checks, verdict, rec, bins, stage, delays, lags, scale, rows, counts, rises):
    tag = {'pass': '전 검사 통과 — 추천값은 사람이 확인·승인한 뒤에만 반영',
           'blocked': '⛔ 갱신 중단 — 아래 값은 참고용, 반영 금지',
           'preview': '미리보기(--can-only-preview) — 갱신 판정 안 함, 아래 값은 참고용'}[verdict]
    now = datetime.datetime.now()
    L = [f'# 가감속 프로파일 추천 — {now:%Y-%m-%d}', '', f'**{tag}**', '',
         f'커밋 `{git_commit()}` · 도구 `tools/race_flag/fit_long_profiles.py` · 기존 값 `{os.path.relpath(args.current, REPO)}` (읽기 전용)', '',
         '## 입력', '', md_table(['로그', '프레임', '길이 s', '시작 unix'], inputs), '']
    L += ['## 검사(계획서 7.2)', '', checks_table(checks), '']
    label = '' if verdict == 'pass' else ' (참고)'
    L += [f'## 추천값{label}', '', md_table(
        ['항목', '기존', '추천', '변화', '근거'],
        [('a_slow (m/s²)', fmt(float(args.cur['a_slow'])), fmt(rec['a_slow']['value']), _chg(rows, 0), rec['a_slow']['note']),
         ('slow_off_ramp_pct_s (%/s)', fmt(float(args.cur['slow_off_ramp_pct_s']), 1), fmt(rec['ramp']['value'], 1), _chg(rows, 1), rec['ramp']['note'])]), '']
    L += [f'잠김 시작: {fmt(rec["a_slow"]["lock_start_pct"])} % · 휠스핀 시작: {fmt(rec["ramp"]["spin_start_pct"])} %', '',
          f'```yaml\n# 추천 표{label} — 사람이 승인한 뒤에만 race_flag.yaml 에 옮긴다',
          f'brake_map: {json.dumps(rec["brake_map"])}', f'accel_map: {json.dumps(rec["accel_map"])}', '```', '']
    L += ['## 브레이크 % → 감속도' + label, '', md_table(['구간 %', '이벤트', '중앙 %', '감속 m/s² p50', 'p25', 'p75'],
          [(f'{b["lo"]}~{b["hi"]}', b['n'], fmt(b['pct'], 0), fmt(b['value']), fmt(b['p25']), fmt(b['p75'])) for b in bins['brake']]), '']
    L += ['## 가속 % → 가속도' + label, '', md_table(['구간 %', '이벤트', '중앙 %', '가속 m/s² p50', 'p25', 'p75'],
          [(f'{b["lo"]}~{b["hi"]}', b['n'], fmt(b['pct'], 0), fmt(b['value']), fmt(b['p25']), fmt(b['p75'])) for b in bins['acc']]), '']
    L += ['## 가속 상승률 → 슬립', '', md_table(['상승률 %/s', '이벤트', '슬립을 잰 이벤트', '최대 슬립 %'],
          [(f'{r["lo"]}~{r["hi"]}'.replace('inf', '계단'), r['n_all'], r['n'], fmt(r['max_slip'] * 100, 1)) for r in rec['ramp']['rows']]), '']
    d = np.array(delays)
    L += ['## 그 밖의 값', '',
          f'- 이벤트 수: 브레이크 {counts["brake"]}, 가속 {counts["acc"]}, 가속 상승 {counts["rise"]} (`segments.csv`)',
          f'- 브레이크 지연(명령 계단 → 0x200 위치 +2 %): ' + (f'중앙값 {np.median(d) * 1000:.0f} ms, p90 {np.percentile(d, 90) * 1000:.0f} ms, {len(d)}회' if len(d) else '표본 없음'),
          f'- 0x201 ÷ GNSS 정속 비율: ' + (f'{scale:.3f}' if scale else f'측정 불가(GNSS 없음), 가감속 계산에 11-04 값 {DEFAULT_SCALE} 사용'),
          '- 시간 차(후륜 대 GNSS): ' + ('; '.join(f'{k}: ' + (f'{v[0] * 1000:+.0f} ms' if v[0] is not None else v[2]) for k, v in lags.items()) or '—'), '']
    L += ['## 서행 단계(계획서 6.3)', '',
          f'도구 판정 단계 **{stage["stage"]}** (승인 전까지 yaml·CLI 는 그대로) · 서행 구간 {stage["runs"]}회 · 흔들림 p99.9 최대 {fmt(stage["wobble_p999"])} km/h · 휠-GNSS 차이 '
          + (f'{stage["wheel_gnss_diff"] * 100:.1f} %' if stage['wheel_gnss_diff'] is not None else '측정 불가') + (f' · {stage["note"]}' if stage['note'] else ''), '']
    if not args.no_plots:
        L += ['## 근거 그래프', '', '![브레이크](brake_map.png)', '', '![가속](accel_map.png)', '', '![타임라인](timeline.png)', '']
        if any(np.isfinite(r['slip']) for r in rises):
            L += ['![슬립](slip.png)', '']
    if verdict == 'pass':
        names = ', '.join(r[0] for r in inputs)
        L += ['## 이력 줄 초안(승인 후 `docs/race_flag_profile_history.md` 에 직접 적는다)', '',
              f'`| {now:%Y-%m-%d} | {names} | a_slow {fmt(float(args.cur["a_slow"]))}→{fmt(rec["a_slow"]["value"])}, 램프 {fmt(float(args.cur["slow_off_ramp_pct_s"]), 1)}→{fmt(rec["ramp"]["value"], 1)} | (승인자) | report.md |`', '']
    return L


def _chg(rows, i):
    c = rows[i]['change']
    return '—' if c is None else ('무한' if math.isinf(c) else f'{c * 100:+.0f} %'.replace('+', ''))


def plot_all(out_dir, traces, ev, cur, have_gnss, label):
    """그래프: 축 하나·표식은 얇게·표는 텍스트 색(dataviz 기준). 색 슬롯 1~3 = 파랑·주황·청록(전체 쌍 검증 통과, README)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    for f in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',):
        if os.path.exists(f):
            fm.fontManager.addfont(f)
            plt.rcParams['font.family'] = 'Noto Sans CJK JP'
    INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'
    C1, C2, C3 = '#2a78d6', '#eb6834', '#1baf7a'
    plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 10, 'axes.edgecolor': GRID, 'axes.labelcolor': INK2,
                         'xtick.color': INK2, 'ytick.color': INK2, 'text.color': INK, 'axes.spines.top': False,
                         'axes.spines.right': False, 'figure.facecolor': SURF, 'axes.facecolor': SURF, 'savefig.facecolor': SURF})

    def style(ax, title):
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_title(title, loc='left', fontsize=11.5, fontweight='bold', color=INK)

    def map_plot(fname, title, xlabel, ylabel, events, bins, tab_x, tab_y, tab_label):
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        if events:
            ax.scatter([e['pct'] for e in events], [e['value'] for e in events], s=26, color=C1, alpha=0.55, linewidths=0,
                       label=f'이벤트별 실측 ({len(events)}회)')
        good = [b for b in bins if b['n'] > 0]
        if good:
            ax.plot([b['pct'] for b in good], [b['value'] for b in good], '-o', color=C3, linewidth=2, markersize=8,
                    markeredgecolor=SURF, markeredgewidth=2, label='% 구간 중앙값')
            for b in good:
                ax.annotate(f"n={b['n']}", (b['pct'], b['value']), xytext=(0, 9), textcoords='offset points', ha='center', fontsize=8.5, color=INK2)
        ax.plot(tab_y, tab_x, '--', color=C2, linewidth=2, label=tab_label)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.legend(frameon=False, fontsize=9, loc='upper left')
        style(ax, title)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, fname), dpi=130)
        plt.close(fig)

    src = 'GNSS' if have_gnss else '0x201 ÷ 0.958'
    map_plot('brake_map.png', f'브레이크 명령 % → 감속도 ({src}){label}', '브레이크 명령 (%)', '감속도 (m/s²)', ev['brake'], ev['brake_bins'],
             cur['brake_map']['decel_mps2'], cur['brake_map']['brake_pct'], '현재 race_flag.yaml 표')
    map_plot('accel_map.png', f'가속 명령 % → 가속도 ({src}){label}', '가속 명령 (%)', '가속도 (m/s²)', ev['acc'], ev['acc_bins'],
             cur['accel_map']['accel_mps2'], cur['accel_map']['acc_pct'], '현재 race_flag.yaml 표')

    shown = sorted(traces, key=lambda tr: -(tr.t[-1] - tr.t[0]))[:6]
    fig, axes = plt.subplots(len(shown), 1, figsize=(9, 1.9 * len(shown) + 0.6), squeeze=False)
    for ax, tr in zip(axes[:, 0], shown):
        rel = tr.t - tr.t[0]
        step = max(1, len(rel) // 3000)
        ax.plot(rel[::step], tr.vw[::step] * 3.6, color=C1, linewidth=1.4, label='후륜 0x201')
        if np.isfinite(tr.vg).any():
            ax.plot(rel[::step], tr.vg[::step] * 3.6, color=C2, linewidth=1.4, label='GNSS')
            ax.legend(frameon=False, fontsize=8.5, loc='upper right', ncol=2)
        for a, b in mask_runs(tr.bad):
            ax.axvspan(rel[a], rel[b], color='#b9b8b1', alpha=0.45, linewidth=0)
            ax.text((rel[a] + rel[b]) / 2, 0.92, '제외', transform=ax.get_xaxis_transform(), ha='center', va='top', fontsize=8.5, color=INK2)
        ax.set_ylabel('km/h')
        style(ax, tr.log.name)
        ax.title.set_fontsize(9.5)
        ax.margins(x=0)
    axes[-1, 0].set_xlabel('로그 시작부터 시간 (s) — 회색 = 앱·RES·ESTOP·겹친 0x210 구간(통계 제외)')
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, 'timeline.png'), dpi=120)
    plt.close(fig)

    rises = [r for r in ev['rise'] if np.isfinite(r['slip'])]
    if rises:
        fig, ax = plt.subplots(figsize=(7.2, 3.8))
        finite = [r['rate'] for r in rises if not math.isinf(r['rate'])]
        xmax = max(finite + [100.0]) * 1.4
        ax.scatter([xmax if math.isinf(r['rate']) else r['rate'] for r in rises], [r['slip'] * 100 for r in rises], s=30, color=C1, alpha=0.6, linewidths=0)
        ax.axhline(SLIP_MAX * 100, color=C2, linestyle='--', linewidth=2)
        ax.annotate(f'기준 {SLIP_MAX * 100:.0f} %', (0, SLIP_MAX * 100), xytext=(4, 4), textcoords='offset points', fontsize=9, color=INK2)
        ax.set_xlabel(f'가속 % 상승률 (%/s) — 맨 오른쪽 {xmax:.0f} = 계단')
        ax.set_ylabel('최대 슬립 (%)')
        style(ax, f'가속 상승률 → 최대 슬립{label}')
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, 'slip.png'), dpi=130)
        plt.close(fig)


def jsonable(x):
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.integer, np.bool_)):
        return x.item()
    if isinstance(x, (float, np.floating)):
        x = float(x)
        if math.isnan(x):
            return None
        if math.isinf(x):
            return 'inf'
    return x


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--can', nargs='+', required=True, help='CAN 로그(평문 또는 .zst) 또는 세션 폴더. 여러 개 가능')
    ap.add_argument('--bag', default=None, help='GNSS bag (mcap 또는 .db3). 메시지 정의가 있는 PC 에서')
    ap.add_argument('--gnss-csv', nargs='+', default=None, help='GNSS CSV(bag 대신, 열: t,speed_mps,fix,pitch_deg 또는 extract.py 출력)')
    ap.add_argument('--gnss-topic', default='/gsof_client/odom', help='odom 토픽(TODO-5 확정 시 변경)')
    ap.add_argument('--ins-topic', default='/gsof_client/gsof/ins_solution_49', help='INS 해 토픽(fix 품질·피치)')
    ap.add_argument('--fix-values', default=','.join(map(str, DEFAULT_FIX_VALUES)), help='fix 로 치는 GNSS 상태 값(쉼표). 4 = RTK Fixed')
    ap.add_argument('--events', nargs='+', default=None, help='can_guard --event-log CSV. 없으면 CAN 로그 옆 can_guard_events*.csv')
    ap.add_argument('--current', default=DEFAULT_CURRENT, help='기존 값(읽기 전용)')
    ap.add_argument('--out', required=True, help='출력 폴더(추천값·근거 그래프·구간 목록)')
    ap.add_argument('--slow-band-kph', nargs=2, type=float, default=(6.5, 9.8), metavar=('LO', 'HI'), help='서행 구간으로 볼 후륜 속도')
    ap.add_argument('--can-only-preview', action='store_true', help='갱신 판정 없이 통계만. 종료 코드 0, 추천값은 참고')
    ap.add_argument('--no-plots', action='store_true', help='그래프 생략')
    a = ap.parse_args(argv)
    if a.bag and a.gnss_csv:
        ap.error('--bag 과 --gnss-csv 는 함께 쓸 수 없음')
    try:
        a.fix_set = tuple(int(x) for x in a.fix_values.split(','))
    except ValueError:
        ap.error(f'--fix-values 형식 오류: {a.fix_values}')
    return a


def merge_gnss(items):
    """CSV 여러 개를 시간순으로 합친다. fix·피치는 모든 파일에 있을 때만 쓴다(일부만 있으면 확인 불가)."""
    if len(items) == 1:
        return items[0]
    t = np.concatenate([g.t for g in items])
    order = np.argsort(t)
    fix = np.concatenate([g.fix for g in items])[order] if all(g.fix is not None for g in items) else None
    pitch = np.concatenate([g.pitch for g in items])[order] if all(g.pitch is not None for g in items) else None
    return Gnss(t[order], np.concatenate([g.v for g in items])[order], fix, pitch, '+'.join(g.source for g in items))


def run(args):
    args.cur = load_current(args.current)
    paths = resolve_can_paths(args.can)
    logs = [load_can(p) for p in paths]
    events_log = load_events(args.events, paths)

    gnss, why = None, '--bag/--gnss-csv 미지정'
    if args.gnss_csv:
        gnss = merge_gnss([load_gnss_csv(p, args.fix_set) for p in args.gnss_csv])
    elif args.bag:
        gnss, why = load_gnss_bag(args.bag, args.gnss_topic, args.ins_topic, args.fix_set)
    have_gnss = gnss is not None

    traces = [tr for tr in (make_trace(lg, gnss, events_log) for lg in logs) if tr is not None]
    if not traces:
        raise InputError('0x201 바퀴 속도가 있는 로그가 없음')

    # 정속 비율: GNSS 가 있으면 측정값으로 슬립을 맞춘다
    const_segs = [s for tr in traces for s in constant_speed_segments(tr)] if have_gnss else []
    scale = float(np.median([s['ratio'] for s in const_segs])) if const_segs else None
    calib = scale or DEFAULT_SCALE
    for tr in traces:
        set_vref(tr, calib)

    ev = dict(brake=[], acc=[], rise=[])
    delays = []
    for tr in traces:
        ev['brake'] += axis_events(tr, 'brake', calib, have_gnss)
        ev['acc'] += axis_events(tr, 'acc', calib, have_gnss)
        ev['rise'] += acc_rises(tr, calib, have_gnss)
        delays += brake_delays(tr)
    ev['brake_bins'] = bin_events(ev['brake'], BRAKE_BINS)
    ev['acc_bins'] = bin_events(ev['acc'], ACC_BINS)

    rec = dict(a_slow=recommend_a_slow(ev['brake'], ev['brake_bins'], have_gnss),
               ramp=recommend_ramp(ev['rise'], ev['acc']))
    rec['brake_map'], _ = monotone_table(ev['brake_bins'], 'decel_mps2', 'brake_pct', start=(0.1, 0.0))
    rec['accel_map'], _ = monotone_table(ev['acc_bins'], 'accel_mps2', 'acc_pct')

    lags = {tr.log.name: estimate_lag(tr, calib) for tr in traces} if have_gnss else {}
    analysis_events = ev['brake'] + ev['acc']
    rows = abrupt_rows(args.cur, rec)
    checks = [check_gnss_quality(traces, analysis_events, gnss, why),
              check_mixed_can(traces),
              check_mixed_estop(traces, events_log),
              check_physical(traces, have_gnss),
              check_sensor(const_segs, have_gnss),
              check_time(traces, have_gnss, lags),
              check_samples(ev['brake_bins'], ev['acc_bins']),
              check_pitch(traces, analysis_events, gnss, why),
              check_abrupt(rows)]

    blocked = [c for c in checks if c.status != PASS]
    verdict = 'preview' if args.can_only_preview else ('blocked' if blocked else 'pass')

    os.makedirs(args.out, exist_ok=True)
    blocked_path = os.path.join(args.out, 'UPDATE_BLOCKED.md')
    inputs = [(tr.log.name, tr.log.frames, f'{tr.t[-1] - tr.t[0]:.0f}', f'{tr.t[0]:.1f}') for tr in traces]
    if verdict == 'blocked':
        write_blocked(args.out, checks, [i[0] for i in inputs])
    elif verdict == 'pass' and os.path.exists(blocked_path):
        os.remove(blocked_path)   # 같은 폴더의 지난 결과가 남아 판단을 흐리지 않게(도구 자신이 쓴 파일)

    diff = abs(scale - 1) if scale else None
    stage = judge_stage(slow_runs(traces, tuple(args.slow_band_kph)), diff)
    counts = {k: len(ev[k]) for k in ('brake', 'acc', 'rise')}
    bins = dict(brake=ev['brake_bins'], acc=ev['acc_bins'])
    label = '' if verdict == 'pass' else ' — 참고'
    if not args.no_plots:
        plot_all(args.out, traces, ev, args.cur, have_gnss, label)
    with open(os.path.join(args.out, 'report.md'), 'w') as f:
        f.write('\n'.join(report_lines(args, inputs, checks, verdict, rec, bins, stage, delays, lags, scale, rows, counts, ev['rise'])))
    with open(os.path.join(args.out, 'segments.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['log', 'kind', 't0_unix', 't1_unix', 'pct', 'value_mps2', 'speed_kph', 'peak_slip', 'rate_pct_s'])
        for e in ev['brake'] + ev['acc'] + ev['rise']:
            w.writerow([e['log'], e['kind'], f"{e['t0']:.3f}", f"{e['t1']:.3f}", f"{e['pct']:.1f}", _num(e.get('value')),
                        _num(e.get('v_kph')), _num(e.get('slip')), _num(e.get('rate'))])
    with open(os.path.join(args.out, 'summary.json'), 'w') as f:
        json.dump(jsonable(dict(verdict=verdict, commit=git_commit(),
                                inputs=[dict(path=tr.log.name, sha256=file_sha(tr.log.path)) for tr in traces],
                                checks=[dict(key=c.key, name=c.name, status=c.status, detail=c.detail, spans=c.spans) for c in checks],
                                recommendation=rec, bins=bins, stage=stage, scale=scale, abrupt=rows, counts=counts,
                                brake_delay_s=dict(n=len(delays), p50=float(np.median(delays)) if delays else None))),
                  f, ensure_ascii=False, indent=1)

    print(f'입력 {len(traces)}개 로그, 이벤트 브레이크 {counts["brake"]} · 가속 {counts["acc"]} · 상승 {counts["rise"]}')
    for c in checks:
        print(f'  [{c.status}] {c.name}: {c.detail}')
    print(f'a_slow: {fmt(float(args.cur["a_slow"]))} → {fmt(rec["a_slow"]["value"])} ({rec["a_slow"]["note"]})')
    print(f'slow_off_ramp_pct_s: {fmt(float(args.cur["slow_off_ramp_pct_s"]), 1)} → {fmt(rec["ramp"]["value"], 1)} ({rec["ramp"]["note"]})')
    print(f'결과: {args.out}')
    if verdict == 'preview':
        can = [c for c in checks if c.can_only]
        print(f"[참고] 미리보기: 갱신 판정 안 함. CAN 전용 검사 {sum(c.status == PASS for c in can)}/{len(can)} 통과, "
              f'GNSS·이벤트가 필요한 검사 {sum(not c.can_only for c in checks)}개는 정식 실행에서 판정')
        return EXIT_OK
    if verdict == 'blocked':
        print('⛔ 갱신 중단: ' + ', '.join(f'{c.name}({c.status})' for c in blocked))
        print(f'   기존 값을 그대로 둔다. 자세한 내용: {blocked_path}')
        return EXIT_BLOCKED
    print('전 검사 통과. race_flag.yaml 은 고치지 않았다 — 근거를 사람이 확인·승인한 뒤에 직접 고치고 이력 문서에 적는다.')
    return EXIT_OK


def _num(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ''
    if isinstance(v, float) and math.isinf(v):
        return 'inf'
    return f'{v:.3f}'


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args)
    except InputError as e:
        print(f'입력 오류: {e}', file=sys.stderr)
        return EXIT_USAGE


if __name__ == '__main__':
    sys.exit(main())
