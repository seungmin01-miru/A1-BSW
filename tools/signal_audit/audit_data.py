"""세션 녹화 → 신호 시계열(numpy) + 한계 분석. judge.py(표)와 plot.py(그림)가 같은 숫자를 쓰도록 한곳에 둔다."""
import glob
import os
import re
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'tools', 'race_day'))
import dbc_lite  # noqa: E402
from summarize_session import LINE  # noqa: E402

DEFAULT_DBC = os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc')
HOLD_S = 0.75         # 명령이 이만큼 그대로였던 표본만 쓴다(정착 상태) — 바뀌는 순간·깜빡임 제외. 10-07 63 % 응답 약 0.2 s
CMD_SAMPLE_S = 0.1    # 명령-응답 쌍을 만들 때 명령을 이 간격으로 솎는다


def load_frames(raw):
    """raw/ 아래(하위 폴더 포함) CAN 원본 → (파일들, [(t, id, data)]).

    Kvaser 원본(kvaser_ch*.log — 미러 --log, bringup.sh 도 같은 이름)이 하나라도 있으면 그것만 쓴다. 2차 candump 복사본은
    같은 프레임인데 수신 시각만 달라 중복이 안 걸러지기 때문(summarize_session.py 와 같은 규칙). 없을 때만 candump*.log.
    단계마다 녹화를 따로 켜도(원격 단계는 listen-only, L4 는 bringup.sh --out raw/l4) 한 번에 합쳐 읽는다.
    """
    def find(pat):
        return sorted(glob.glob(os.path.join(raw, '**', pat), recursive=True))
    files = find('kvaser_ch*.log') or find('candump*.log')
    seen, frames = set(), []
    for p in files:
        with open(p, errors='replace') as f:
            for line in f:
                m = LINE.match(line)
                if not m:
                    continue
                key = (m.group(1), m.group(2).upper(), m.group(3).upper())
                if key in seen:
                    continue
                seen.add(key)
                frames.append((float(m.group(1)), int(m.group(2), 16), bytes.fromhex(m.group(3))))
    frames.sort(key=lambda x: x[0])
    return files, frames


def load_lift_cmd(raw):
    """raw/ 아래 lift_cmd 기록(lift_cmd_<시각>.log, bringup 의 lift_cmd.log) → [(t, 'lift_cmd', 내용)], 중복 제거."""
    out = set()
    for p in glob.glob(os.path.join(raw, '**', 'lift_cmd*.log'), recursive=True):
        for line in open(p, errors='replace'):
            m = re.match(r'(\d+\.\d+) \d\d:\d\d:\d\d (.*)', line.strip())
            if m:
                out.add((float(m.group(1)), 'lift_cmd', m.group(2)))
    return sorted(out)


class Session:
    def __init__(self, raw, dbc=DEFAULT_DBC):
        self.msgs = dbc_lite.load(dbc)
        self.msgs.update(dbc_lite.remote_messages())
        self.files, self.frames = load_frames(raw)
        self.by = defaultdict(list)
        for t, fid, d in self.frames:
            self.by[fid].append((t, d))
        self._cache = {}

    def sig(self, fid, name):
        """(t[], v[]) — 해당 ID 가 없으면 빈 배열."""
        key = (fid, name)
        if key not in self._cache:
            rows = self.by.get(fid, [])
            s = next((x for x in self.msgs[fid].signals if x.name == name), None) if fid in self.msgs else None
            if not rows or s is None:
                self._cache[key] = (np.array([]), np.array([]))
            else:
                self._cache[key] = (np.array([t for t, _ in rows]), np.array([s.phys(d) for _, d in rows]))
        return self._cache[key]

    def at(self, fid, name, t):
        """시각 배열 t 에서의 값(직전 프레임). 그 전에 프레임이 없으면 nan."""
        ts, vs = self.sig(fid, name)
        if not len(ts):
            return np.full(len(t), np.nan)
        i = np.searchsorted(ts, t, side='right') - 1
        out = vs[np.clip(i, 0, None)].astype(float)
        out[i < 0] = np.nan
        return out


def _held(s, fid, name, t, v):
    """t 시점 명령 v 가 HOLD_S 전부터 같았나(중간 두 점도 확인)."""
    ok = np.ones(len(t), dtype=bool)
    for f in (1.0, 0.5, 0.25):
        ok &= s.at(fid, name, t - HOLD_S * f) == v
    return ok


def _sample(t, dt=CMD_SAMPLE_S):
    if not len(t):
        return np.array([], dtype=int)
    keep, nxt = [], t[0]
    for i, x in enumerate(t):
        if x >= nxt:
            keep.append(i)
            nxt = x + dt
    return np.array(keep, dtype=int)


def wheel_speed(s):
    """좌우 평균 바퀴 속도 (t, km/h) — 0x201."""
    t, l = s.sig(0x201, 'left_speed')
    _, r = s.sig(0x201, 'right_speed')
    return t, (l + r) / 2 if len(t) else l


def wheel_accel(s, half_window_s=0.25):
    """바퀴 속도에서 구한 가감속도 (t, m/s²) — 중심 차분. 0x201 이 살아 있을 때만 의미 있음."""
    t, v = wheel_speed(s)
    if len(t) < 3:
        return np.array([]), np.array([])
    v = v / 3.6
    k = max(1, int(round(half_window_s / max(np.median(np.diff(t)), 1e-3))))
    a = (v[2 * k:] - v[:-2 * k]) / (t[2 * k:] - t[:-2 * k])
    return t[k:-k], a


def steer_rate(s, window_s=0.1):
    """0x200 조향 위치 변화율 (t, °/s)."""
    t, p = s.sig(0x200, 'steer_postion')
    if len(t) < 3:
        return np.array([]), np.array([])
    k = max(1, int(round(window_s / max(np.median(np.diff(t)), 1e-3))))
    return t[k:], (p[k:] - p[:-k]) / (t[k:] - t[:-k])


def pairs(s):
    """축별 (명령, 응답) 쌍 — 한계표·산점도 공용. 각 항목: dict(label, cmd, resp, cmd_unit, resp_unit, dbc)."""
    out = []
    # 우리 0x210 (축 auto 켜진 동안, 명령이 HOLD_S 동안 그대로였던 표본만)
    t, steer = s.sig(0x210, 'steer_command')
    if len(t):
        i = _sample(t)
        t, steer = t[i], steer[i]
        pos, brk = s.at(0x200, 'steer_postion', t), s.at(0x200, 'break_postion', t)
        brake = s.at(0x210, 'break_command', t)
        acc = s.at(0x210, 'acc_command', t)
        sa = (s.at(0x210, 'steer_is_auto_command', t) == 1) & _held(s, 0x210, 'steer_command', t, steer)
        ba = (s.at(0x210, 'break_is_auto_command', t) == 1) & _held(s, 0x210, 'break_command', t, brake)
        aa = (s.at(0x210, 'acc_is_auto_command', t) == 1) & _held(s, 0x210, 'acc_command', t, acc)
        out.append(dict(axis='steer', label='0x210 조향 → 0x200 위치', cmd=steer[sa], resp=pos[sa],
                        cmd_unit='°', resp_unit='°', dbc=(-150, 150), ident=True))
        out.append(dict(axis='brake', label='0x210 브레이크 % → 0x200 위치', cmd=brake[ba], resp=brk[ba],
                        cmd_unit='%', resp_unit='위치', dbc=(0, 100), ident=False))
        wt, wa = wheel_accel(s)
        if len(wt):
            ws = np.interp(t, wt, wa)
            out.append(dict(axis='acc', label='0x210 가속 % → 바퀴 선가속도 (공회전: 차량 가속도 아님)', cmd=acc[aa], resp=ws[aa],
                            cmd_unit='%', resp_unit='m/s²', dbc=(0, 100), ident=False))
    # 원격 0x157 (En=1 이고 명령이 HOLD_S 동안 그대로였던 표본만 — En=0 이면 차량이 무시)
    t, eps = s.sig(0x157, 'EPS_Cmd')
    if len(t):
        i = _sample(t)
        t, eps = t[i], eps[i]
        accc = s.sig(0x157, 'ACC_Cmd')[1][i]
        ee = (s.at(0x156, 'EPS_En', t) == 1) & _held(s, 0x157, 'EPS_Cmd', t, eps)
        ae = (s.at(0x156, 'ACC_En', t) == 1) & _held(s, 0x157, 'ACC_Cmd', t, accc)
        pos, brk = s.at(0x200, 'steer_postion', t), s.at(0x200, 'break_postion', t)
        out.append(dict(axis='steer', label='원격 EPS_Cmd → 0x200 위치', cmd=eps[ee], resp=pos[ee],
                        cmd_unit='°', resp_unit='°', dbc=(-500, 500), ident=True))
        dec = ae & (accc < 0)
        out.append(dict(axis='brake', label='원격 ACC_Cmd(감속) → 0x200 브레이크 위치', cmd=accc[dec], resp=brk[dec],
                        cmd_unit='m/s²', resp_unit='위치', dbc=(-3, 0), ident=False))
        wt, wa = wheel_accel(s)
        if len(wt):
            ws = np.interp(t, wt, wa)
            out.append(dict(axis='accel', label='원격 ACC_Cmd → 바퀴 선가속도 (공회전: 차량 가속도 아님)', cmd=accc[ae], resp=ws[ae],
                            cmd_unit='m/s²', resp_unit='m/s²', dbc=(-3, 1), ident=True))
    return [p for p in out if len(p['cmd'])]


def binned(cmd, resp, width):
    """명령을 width 간격으로 묶어 [(중심, n, 응답 중앙값, 응답 최소, 최대)]."""
    if not len(cmd):
        return []
    k = np.round(cmd / width) * width
    out = []
    for c in np.unique(k):
        r = resp[(k == c) & ~np.isnan(resp)]
        if len(r):
            out.append((float(c), len(r), float(np.median(r)), float(r.min()), float(r.max())))
    return out


def saturation(rows, frac=0.95):
    """응답 중앙값이 최대(절댓값)의 frac 에 처음 닿는 명령 — 그 위로는 명령을 키워도 응답이 거의 안 는다."""
    if len(rows) < 3:
        return None
    for sign in (1, -1):
        side = [r for r in rows if r[0] * sign > 0]
        if len(side) < 2:
            continue
        top = max(r[2] * sign for r in side)
        if top <= 0:
            continue
        for r in sorted(side, key=lambda x: x[0] * sign):
            if r[2] * sign >= frac * top:
                yield sign, r[0], top * sign
                break


BIN_STEPS = {'°': (1, 2, 5, 10, 20), '%': (1, 2, 5, 10), 'm/s²': (0.1, 0.25, 0.5)}


def bin_width(cmd, unit, max_bins=15):
    """관측 범위가 max_bins 칸 안에 들어가는 가장 촘촘한 간격."""
    steps = BIN_STEPS.get(unit, (1,))
    span = float(cmd.max() - cmd.min()) if len(cmd) else 0
    return next((w for w in steps if span / w <= max_bins), steps[-1])


# ── 공회전(뒷바퀴 띄움) 분석 ─────────────────────────────────────────────
# 공회전 바퀴 가속도는 구동계 관성만 돌린 값이라 **차량 가감속도가 아니다**(질량·저항 없음). 여기서 얻는 것은
# 출력 상한(최고 속도·최대 각가속도)·불감대·지연·응답 모양(속도 제어인지 토크 제어인지)뿐 — m/s² 매칭은 주행(11-04)에서.
RPM_MOVE = 5          # 이 rpm 이상 오르면 '돌기 시작'
STEP_MIN_S = 0.5      # 이보다 짧게 유지된 명령은 계단으로 안 본다


def wheel_omega(s):
    """좌우 평균 바퀴 각속도 (t, rad/s) — 0x201 rpm."""
    t, l = s.sig(0x201, 'left_rpm')
    _, r = s.sig(0x201, 'right_rpm')
    return (t, (l + r) / 2 * 2 * np.pi / 60) if len(t) else (t, l)


def wheel_alpha(s, half_window_s=0.25):
    """바퀴 각가속도 (t, rad/s²) — rpm 중심 차분."""
    t, w = wheel_omega(s)
    if len(t) < 3:
        return np.array([]), np.array([])
    k = max(1, int(round(half_window_s / max(np.median(np.diff(t)), 1e-3))))
    return t[k:-k], (w[2 * k:] - w[:-2 * k]) / (t[2 * k:] - t[:-2 * k])


def wheel_diameter(s, min_rpm=60):
    """0x201 speed ÷ rpm 로 역산한 바퀴 지름(cm) {'left': (중앙값, 표본), 'right': …}. 0x301 설정·실측과 대조용."""
    out = {}
    for side in ('left', 'right'):
        _, v = s.sig(0x201, f'{side}_speed')
        _, r = s.sig(0x201, f'{side}_rpm')
        ok = r >= min_rpm
        if ok.sum() >= 10:
            circ_m = (v[ok] / 3.6) / (r[ok] / 60)
            out[side] = (float(np.median(circ_m / np.pi * 100)), int(ok.sum()))
    return out


def _segments(t, v, gate):
    """gate 가 켜진 동안 같은 값이 이어진 구간 [(t0, t1, 값)] — STEP_MIN_S 이상만."""
    segs, cur = [], None
    for ti, vi, gi in zip(t, v, gate):
        key = vi if gi else None
        if cur and cur[2] == key:
            cur[1] = ti
        else:
            if cur and cur[2] is not None and cur[1] - cur[0] >= STEP_MIN_S:
                segs.append(tuple(cur))
            cur = [ti, ti, key]
    if cur and cur[2] is not None and cur[1] - cur[0] >= STEP_MIN_S:
        segs.append(tuple(cur))
    return segs


def freespin_steps(s):
    """명령 계단별 공회전 응답. 각 항목 dict(src, t0, t1, cmd, unit, w0, w_end, w_max, kmh_max, alpha_peak,
    alpha_end, latency_s, shape). shape: '정착'(끝 1 s 각가속도가 최대의 15 % 미만 — 속도 제어 의심) /
    '계속 증가'(토크 제어 의심) / '무반응'."""
    tw, w = wheel_omega(s)
    ta, al = wheel_alpha(s)
    tv, v = wheel_speed(s)
    if not len(tw):
        return []
    srcs = []
    t, acc = s.sig(0x210, 'acc_command')
    if len(t):
        g = s.at(0x210, 'acc_is_auto_command', t) == 1
        srcs += [('0x210 가속', t0, t1, c, '%') for t0, t1, c in _segments(t, acc, g & (acc > 0))]
        t2, brk = s.sig(0x210, 'break_command')
        g2 = s.at(0x210, 'break_is_auto_command', t2) == 1
        srcs += [('0x210 브레이크', t0, t1, c, '%') for t0, t1, c in _segments(t2, brk, g2 & (brk > 0))]
    t, ac = s.sig(0x157, 'ACC_Cmd')
    if len(t):
        g = s.at(0x156, 'ACC_En', t) == 1
        srcs += [('원격 ACC_Cmd', t0, t1, round(c, 2), 'm/s²') for t0, t1, c in _segments(t, np.round(ac, 2), g & (np.abs(ac) > 0.05))]
    out = []
    for src, t0, t1, c, unit in sorted(srcs, key=lambda x: x[1]):
        i0, i1 = np.searchsorted(tw, t0), np.searchsorted(tw, t1, side='right')
        if i1 - i0 < 3:
            continue
        ww = w[i0:i1]
        w0 = float(ww[0])
        rpm0 = w0 * 60 / (2 * np.pi)
        moved = np.nonzero(np.abs(ww * 60 / (2 * np.pi) - rpm0) >= RPM_MOVE)[0]
        lat = float(tw[i0 + moved[0]] - t0) if len(moved) else None
        j0, j1 = np.searchsorted(ta, t0), np.searchsorted(ta, t1, side='right')
        aa = al[j0:j1]
        k_end = np.searchsorted(ta, t1 - 1.0)
        a_end = al[max(k_end, j0):j1]
        decel = src.endswith('브레이크') or (unit == 'm/s²' and c < 0)
        peak = float(aa.min() if decel else aa.max()) if len(aa) else 0.0
        endv = float(np.median(a_end)) if len(a_end) else 0.0
        if lat is None:
            shape = '무반응'
        elif decel:
            shape = '정지' if ww[-1] < 0.5 else '감속 중'
        else:
            shape = '정착' if abs(endv) < 0.15 * abs(peak) else '계속 증가'
        vi0, vi1 = np.searchsorted(tv, t0), np.searchsorted(tv, t1, side='right')
        out.append(dict(src=src, t0=t0, t1=t1, cmd=float(c), unit=unit, w0=w0, w_end=float(ww[-1]),
                        w_max=float(ww.max()), kmh_max=float(v[vi0:vi1].max()) if vi1 > vi0 else 0.0,
                        alpha_peak=peak, alpha_end=endv, latency_s=lat, shape=shape))
    return out
