#!/usr/bin/env python3
"""종방향 2차원 표 갱신 도구(지시서 2.3절) — 주행 로그에서 측정 표와 추천 long_map 을 만든다. **자동 반영 금지**: 파일만 쓴다.

  python3 tools/engine_room/fit_long_map.py --can <로그 또는 세션 폴더> --bag <bag> --out reports/engine_room/<날짜>_fit
  python3 tools/engine_room/fit_long_map.py --can <로그> --gnss-csv <csv> --out <폴더>      # t, speed_mps, fix, pitch_deg
  python3 tools/engine_room/fit_long_map.py --can <로그> --can-only-preview --out <폴더>    # GNSS 없이 값 보기(추천 없음)

종료 코드: 0 = 통과(또는 미리보기), 2 = 입력 오류, 3 = ⛔ 추천 차단(UPDATE_BLOCKED.md). 확인할 수 없는 검사는 통과가 아니다.
CAN·차량에 닿지 않는다(저장된 파일만 읽는다). 출력: report.md, cells.csv, segments.csv, long_map_recommended.yaml, long_map.png.
"""
import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

import numpy as np
import yaml

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'signal_audit'))
sys.path.insert(0, os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter'))
import dbc_lite  # noqa: E402
from a1_control_adapter.conversion import LongMap2D  # noqa: E402

DBC_PATH = os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc')
DEFAULT_MAP = os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'long_map.yaml')
EXIT_OK, EXIT_USAGE, EXIT_BLOCKED = 0, 2, 3

# 지시서 2.3절 표(전부 잠정). 합격 기준이라 여기서 임의로 바꾸지 않는다.
FIX_RATIO_MIN = 0.90
DECEL_MAX, ACCEL_MAX = 4.5, 1.6
LAG_MAX_S = 0.05
CELL_MIN_SPANS = 3
PITCH_MAX_DEG = 2.0
ABRUPT_MAX = 0.20
ACCURACY_MAX = 0.20      # 지시서 2.4절 정상 상태 ±20 %
# 지시서에 숫자가 없어 정한 값(잠정, README)
WHEEL_SCALE = 0.958      # GNSS 없을 때만: 0x201 ÷ GNSS (11-04)
FOREIGN_HZ = 70.0        # 0x210 초당 프레임이 이보다 많으면 송신자 둘
APP_PAD_S = 2.0
GRID_S = 0.02
HOLD_TOL_PCT = 3.0
SKIP_S = {'acc': 0.7, 'brake': 0.4}     # 지연(가속 출발 0.64 s, 브레이크 떼기 0.4 s)만큼 버림
MIN_PIECE_S = 0.5
MIN_SPEED_KPH = 1.0
MIN_PEDAL_PCT = 5.0       # 이보다 작은 명령은 페달 표에 쓰지 않는다(0 으로 내려가는 꼬리)
LINE = re.compile(r'^\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)')


class InputError(Exception):
    pass


@dataclass
class Check:
    name: str
    status: str      # 통과 / 걸림 / 확인 불가
    detail: str

    @property
    def ok(self):
        return self.status == '통과'


def read_lines(path):
    if path.endswith('.zst'):
        try:
            return subprocess.run(['zstd', '-dc', path], capture_output=True, text=True, check=True).stdout.splitlines()
        except (OSError, subprocess.CalledProcessError) as e:
            raise InputError(f'{path}: zstd 로 풀 수 없음 ({e})')
    try:
        with open(path, errors='replace') as f:
            return f.read().splitlines()
    except OSError as e:
        raise InputError(f'{path}: 읽을 수 없음 ({e})')


def resolve_can(item):
    """파일은 그대로, 폴더는 세션 폴더로 보고 로그 하나를 고른다(미러 기록 우선 — tools/drive_log/cannpz.py 와 같음)."""
    if os.path.isdir(item):
        fs = sorted((os.path.join(item, f) for f in os.listdir(item) if re.search(r'\.log(\.zst)?$', f)),
                    key=lambda p: ('kvaser' not in os.path.basename(p), p))
        if not fs:
            raise InputError(f'{item}: 로그 파일 없음')
        return fs[0]
    if os.path.isfile(item):
        return item
    raise InputError(f'{item}: 없는 경로')


def parse_can(lines):
    db = dbc_lite.load(DBC_PATH)
    db.update(dbc_lite.remote_messages())
    rows = {k: [] for k in (0x210, 0x201, 0x156, 0x127)}
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            continue
        cid = int(m.group(2), 16)
        if cid not in rows:
            continue
        t, d = float(m.group(1)), bytes.fromhex(m.group(3))
        if cid == 0x210:
            s = db[0x210].decode(d)
            rows[cid].append((t, s['acc_command'], s['break_command']))
        elif cid == 0x201:
            s = db[0x201].decode(d)
            rows[cid].append((t, (s['left_speed'] + s['right_speed']) / 2))
        elif cid == 0x156:
            s = db[0x156].decode(d)
            rows[cid].append((t, max(s['EPS_En'], s['ACC_En'], s['AEB_En'])))
        else:
            rows[cid].append((t, db[0x127].decode(d)['RES_START']))
    if not rows[0x210] or not rows[0x201]:
        raise InputError('0x210·0x201 프레임이 없다')
    return {k: np.array(v, float).reshape(-1, 3 if k == 0x210 else 2) for k, v in rows.items()}


def load_gnss_csv(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    if not rows or 't' not in rows[0] or 'speed_mps' not in rows[0]:
        raise InputError(f'{path}: t, speed_mps 열이 필요')
    g = {'t': np.array([float(r['t']) for r in rows]), 'v': np.array([float(r['speed_mps']) for r in rows]),
         'source': f'csv:{os.path.basename(path)}'}
    g['fix'] = np.array([int(float(r['fix'])) == 4 for r in rows]) if rows[0].get('fix') not in (None, '') else None
    g['pitch'] = np.array([float(r['pitch_deg']) for r in rows]) if rows[0].get('pitch_deg') not in (None, '') else None
    return g


def load_gnss_bag(uri, odom_topic, ins_topic):
    """INS 해(품질·피치)를 먼저, 없으면 odom(품질 없음 → GNSS 품질 '확인 불가'). tools/track_day/fit_maps.read_bag 재사용."""
    sys.path.insert(0, os.path.join(REPO, 'tools', 'track_day'))
    try:
        import fit_maps
        msgs = fit_maps.read_bag(uri, [ins_topic, odom_topic])
    except Exception as e:   # noqa: BLE001 — ROS 없음·메시지 정의 없음·손상 bag
        raise InputError(f'bag 을 읽을 수 없음: {type(e).__name__}: {e}')
    ins, odom = msgs[ins_topic], msgs[odom_topic]
    if ins:
        pitch = np.array([float(getattr(m, 'pitch', math.nan)) for _, m in ins])
        return {'t': np.array([x[0] for x in ins]), 'v': np.array([float(m.total_speed) for _, m in ins]),
                'fix': np.isin(np.array([int(m.status.gnss) for _, m in ins]), (4,)),
                'pitch': pitch if np.isfinite(pitch).any() else None, 'source': f'bag:{ins_topic}'}
    if odom:
        return {'t': np.array([x[0] for x in odom]),
                'v': np.array([math.hypot(m.twist.twist.linear.x, m.twist.twist.linear.y) for _, m in odom]),
                'fix': None, 'pitch': None, 'source': f'bag:{odom_topic}'}
    raise InputError(f'bag 에 {ins_topic}·{odom_topic} 메시지가 없다')


def hold(t_src, x_src, t, max_age=0.3):
    if len(t_src) == 0:
        return np.full(len(t), np.nan)
    i = np.clip(np.searchsorted(t_src, t, side='right') - 1, 0, len(t_src) - 1)
    out = x_src[i].astype(float)
    out[(t < t_src[0]) | (t - t_src[i] > max_age)] = np.nan
    return out


def smooth(x, n):
    if n <= 1:
        return x.copy()
    good = np.isfinite(x)
    v = np.convolve(np.where(good, x, 0.0), np.ones(n), 'same')
    c = np.convolve(good.astype(float), np.ones(n), 'same')
    with np.errstate(invalid='ignore', divide='ignore'):
        out = v / c
    out[~good] = np.nan
    return out


def make_trace(can, gnss, t0=None, t1=None):
    start, end = can[0x210][0, 0], can[0x210][-1, 0]
    a = start if t0 is None else start + t0
    b = end if t1 is None else min(end, start + t1)
    t = np.arange(a, b, GRID_S)
    tr = {'t': t, 'acc': hold(can[0x210][:, 0], can[0x210][:, 1], t), 'brk': hold(can[0x210][:, 0], can[0x210][:, 2], t),
          'vw': hold(can[0x201][:, 0], can[0x201][:, 1], t) / 3.6}
    tr['vg'] = hold(gnss['t'], gnss['v'], t, 0.5) if gnss else np.full(len(t), np.nan)
    v = tr['vg'] if gnss else tr['vw'] / WHEEL_SCALE
    tr['a'] = np.gradient(smooth(v, int(round(0.3 / GRID_S))), GRID_S)    # 0.3 s 이동평균 뒤 미분(지시서)
    return tr


def spans_bad(can, tr):
    """다른 조작 구간 → 표에서 뺀다(그리고 검사에서 걸린다)."""
    t = tr['t']
    bad = np.zeros(len(t), bool)
    why = []
    for cid, label in ((0x156, '앱 En=1'), (0x127, 'RES')):
        r = can[cid]
        if not len(r) or not (r[:, 1] > 0).any():
            continue
        on = r[r[:, 1] > 0, 0]
        on = on[(on >= t[0] - APP_PAD_S) & (on <= t[-1] + APP_PAD_S)]
        if not len(on):
            continue
        for x in on[np.r_[True, np.diff(on) > 1.0]]:
            why.append(f'{label} t={x - t[0]:.1f} s')
        bad |= hold(on, np.ones(len(on)), t, APP_PAD_S) > 0
    t210 = can[0x210][:, 0]
    t210 = t210[(t210 >= t[0]) & (t210 <= t[-1])]
    if len(t210):
        rate = np.histogram(t210, bins=np.arange(t[0], t[-1] + 1.0, 1.0))[0]
        over = np.where(rate > FOREIGN_HZ)[0]
        if len(over):
            why.append(f'0x210 > {FOREIGN_HZ:.0f} 프레임/s(송신자 둘) {len(over)} s, 처음 t={over[0]:.0f} s')
            for k in over:
                bad |= (t >= t[0] + k - 1) & (t <= t[0] + k + 2)
    return bad, why


def load_events(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))


def pieces(tr, bad, speed_bins):
    """페달이 ±3 % 로 유지되고 다른 축이 0 인 구간을 속도 칸 경계에서 나눈다. 앞부분(지연)은 버린다."""
    out = []
    n = len(tr['t'])
    vk = tr['vw'] * 3.6
    bins = np.array(speed_bins, float)
    for axis, cmd, other in (('acc', tr['acc'], tr['brk']), ('brake', tr['brk'], tr['acc'])):
        i = 0
        while i < n:
            if not (np.isfinite(cmd[i]) and cmd[i] > 0 and other[i] == 0 and not bad[i]):
                i += 1
                continue
            j = i
            while j + 1 < n and abs(cmd[j + 1] - cmd[i]) <= HOLD_TOL_PCT and other[j + 1] == 0 and not bad[j + 1]:
                j += 1
            k = i + int(SKIP_S[axis] / GRID_S)
            while k <= j:
                if not np.isfinite(vk[k]) or vk[k] < MIN_SPEED_KPH:
                    k += 1
                    continue
                b = int(np.argmin(np.abs(bins - vk[k])))
                e = k
                while e + 1 <= j and np.isfinite(vk[e + 1]) and vk[e + 1] >= MIN_SPEED_KPH \
                        and int(np.argmin(np.abs(bins - vk[e + 1]))) == b:
                    e += 1
                if (e - k + 1) * GRID_S >= MIN_PIECE_S:
                    s = slice(k, e + 1)
                    a = float(np.nanmean(tr['a'][s]))
                    if np.isfinite(a) and np.mean(cmd[s]) >= MIN_PEDAL_PCT:
                        out.append(dict(axis=axis, t0=float(tr['t'][k] - tr['t'][0]), t1=float(tr['t'][e] - tr['t'][0]),
                                        pedal=float(np.mean(cmd[s])), speed_kph=float(np.nanmean(vk[s])),
                                        speed_bin=float(bins[b]), a_mps2=a))
                k = e + 1
            i = j + 1
    out.sort(key=lambda p: p['t0'])
    return out


def build_cells(pcs, accels):
    """(속도 칸, 가속도 칸) → 페달 표본. 페달과 가속도 부호가 다르면(가속인데 감속 등) 쓰지 않고 센다."""
    acc = np.array(accels, float)
    cells, skipped = {}, 0
    for p in pcs:
        sign = 1 if p['axis'] == 'acc' else -1
        if p['a_mps2'] * sign <= 0:
            skipped += 1
            continue
        cols = np.where(acc * sign > 0)[0]
        j = int(cols[np.argmin(np.abs(acc[cols] - p['a_mps2']))])
        cells.setdefault((p['speed_bin'], j), []).append(sign * p['pedal'])
    return cells, skipped


def estimate_lag(tr, max_lag_s=2.0, min_overlap_s=20.0, min_corr=0.7):
    """후륜·GNSS 가속도 상호상관. (시간 차 s 또는 None, 상관, 이유). 값에는 기록 시각 차이와 센서 필터 지연이 같이 들어 있다."""
    if not np.isfinite(tr['vg']).any():
        return None, math.nan, 'GNSS 와 CAN 시간대가 겹치지 않음'
    n = int(round(0.25 / GRID_S))
    aw, ag = np.gradient(smooth(tr['vw'], n), GRID_S), np.gradient(smooth(tr['vg'], n), GRID_S)
    ok = np.isfinite(aw) & np.isfinite(ag)
    if ok.sum() * GRID_S < min_overlap_s:
        return None, math.nan, f'겹치는 시간 {ok.sum() * GRID_S:.1f} s < {min_overlap_s:.0f} s'
    if np.std(ag[ok]) < 0.05:
        return None, math.nan, '속도 변화가 거의 없어 잴 수 없음'
    m = int(round(max_lag_s / GRID_S))
    lags = np.arange(-m, m + 1)
    corr = np.full(len(lags), np.nan)
    for k, lag in enumerate(lags):
        a, b = (aw[:len(aw) - lag], ag[lag:]) if lag >= 0 else (aw[-lag:], ag[:lag])
        s = np.isfinite(a) & np.isfinite(b)
        if s.sum() * GRID_S >= min_overlap_s / 2:
            corr[k] = np.corrcoef(a[s], b[s])[0, 1]
    k = int(np.nanargmax(corr))
    if corr[k] < min_corr:
        return None, float(corr[k]), f'상관 {corr[k]:.2f} < {min_corr}'
    if k in (0, len(lags) - 1):
        return None, float(corr[k]), f'최대 상관이 탐색 범위 끝(±{max_lag_s:.0f} s)'
    return float(lags[k] * GRID_S), float(corr[k]), ''


def recommend(cfg, cells):
    """기존 표에서 표본 ≥ CELL_MIN_SPANS 칸만 측정값으로 바꾼다. (새 표, 차이 행)."""
    speeds = [float(x) for x in cfg['speed_bins_kph']]
    table = {float(k): list(v) for k, v in cfg['pedal_pct'].items()}
    new = {k: list(v) for k, v in table.items()}
    diffs, measured = [], {}
    for (sp, j), vals in sorted(cells.items()):
        if len(vals) < CELL_MIN_SPANS:
            continue
        val = round(float(np.mean(vals)), 1)
        old = table[sp][j]
        new[sp][j] = val
        measured.setdefault(sp, set()).add(j)
        rel = None if old in (None, 0) else abs(val - old) / abs(old)
        diffs.append(dict(speed_kph=sp, accel_mps2=cfg['accel_bins_mps2'][j], old=old, new=val, n=len(vals), rel=rel))
    # 측정 칸만 바꾸면 옛 복사값과 뒤집힐 수 있다 → 측정 칸과 어긋나는 옛 칸은 미측정으로 되돌린다(측정끼리 뒤집히면 표 형식 검사가 잡는다)
    for sp, cols in measured.items():
        row = new[sp]
        for j, v in enumerate(row):
            if j in cols or v is None:
                continue
            for m in cols:
                if (j < m and v > row[m]) or (j > m and v < row[m]):
                    row[j] = None
                    break
    return {int(s) if float(s).is_integer() else s: new[s] for s in speeds}, diffs


def accuracy_rows(cfg, pcs):
    """표가 약속한 가속도와 실측의 차이(±20 %, 지시서 2.4절). 표를 거꾸로 읽는다."""
    acc = np.array(cfg['accel_bins_mps2'], float)
    table = {float(k): v for k, v in cfg['pedal_pct'].items()}
    rows = {}
    for p in pcs:
        row = table.get(p['speed_bin'])
        if row is None:
            continue
        sign = 1 if p['axis'] == 'acc' else -1
        pts = sorted((abs(v), abs(a)) for a, v in zip(acc, row) if v is not None and v != 0 and a * sign > 0)
        if len(pts) < 2 or p['a_mps2'] * sign <= 0.05:
            continue
        xs = [x for x, _ in pts]
        if not xs[0] <= p['pedal'] <= xs[-1]:
            continue
        expected = float(np.interp(p['pedal'], xs, [y for _, y in pts]))
        if expected > 0:
            rows.setdefault((p['speed_bin'], p['axis']), []).append(abs(abs(p['a_mps2']) - expected) / expected)
    return [dict(speed_kph=k[0], axis=k[1], n=len(v), err_mean=float(np.mean(v)), err_max=float(np.max(v)),
                 ok=bool(np.mean(v) <= ACCURACY_MAX)) for k, v in sorted(rows.items())]


def checks_can(why_bad, events, pcs):
    res = [Check('다른 조작(CAN)', '걸림' if why_bad else '통과', '; '.join(why_bad) or '앱·RES·다른 송신자 없음')]
    if events is None:
        res.append(Check('다른 조작(ESTOP)', '확인 불가', 'can_guard 이벤트 로그 없음(--events)'))
    else:
        hit = [e for e in events if e.get('event') in ('estop_request', 'res', 'foreign_0x210')
               or '->ESTOP' in (e.get('detail') or '')]
        res.append(Check('다른 조작(ESTOP)', '걸림' if hit else '통과', f'이벤트 {len(hit)} 건'))
    over = [p for p in pcs if -p['a_mps2'] > DECEL_MAX or p['a_mps2'] > ACCEL_MAX]
    res.append(Check('물리 한계', '걸림' if over else '통과', f'감속 > {DECEL_MAX}, 가속 > {ACCEL_MAX} m/s² 구간 {len(over)} 개'
                     + (f', 처음 t={over[0]["t0"]:.1f} s' if over else '')))
    return res


def checks_gnss(tr, gnss, cells, diffs, table_err):
    res = []
    i = np.searchsorted(gnss['t'], tr['t']).clip(0, len(gnss['t']) - 1)
    if gnss['fix'] is None:
        res.append(Check('GNSS 품질', '확인 불가', 'fix 정보 없음(odom 만 있는 bag)'))
    else:
        ratio = float(np.mean(gnss['fix'][i]))
        res.append(Check('GNSS 품질', '통과' if ratio >= FIX_RATIO_MIN else '걸림',
                         f'RTK/RTX fix {ratio * 100:.1f} % (기준 ≥ {FIX_RATIO_MIN * 100:.0f} %)'))
    lag, corr, why = estimate_lag(tr)
    if lag is None:
        res.append(Check('시간 어긋남', '확인 불가', why))
    else:
        res.append(Check('시간 어긋남', '통과' if abs(lag) <= LAG_MAX_S else '걸림',
                         f'{lag * 1e3:+.0f} ms, 상관 {corr:.2f} (기준 ≤ {LAG_MAX_S * 1e3:.0f} ms)'))
    good = sum(1 for v in cells.values() if len(v) >= CELL_MIN_SPANS)
    res.append(Check('표본 부족', '통과' if good else '걸림',
                     f'표본 ≥ {CELL_MIN_SPANS} 칸 {good} 개, 모자라 제외 {len(cells) - good} 개'))
    if gnss['pitch'] is None:
        res.append(Check('경사', '확인 불가', '피치 없음'))
    else:
        pm = float(np.nanmax(np.abs(gnss['pitch'][i])))
        res.append(Check('경사', '통과' if pm <= PITCH_MAX_DEG else '걸림', f'|피치| 최대 {pm:.2f}° (기준 ≤ {PITCH_MAX_DEG}°)'))
    jump = [d for d in diffs if d['rel'] is not None and d['rel'] > ABRUPT_MAX]
    res.append(Check('급변', '걸림' if jump else '통과', f'기존 대비 > {ABRUPT_MAX * 100:.0f} % 칸 {len(jump)} 개 — 사람 확인' + (
        ': ' + ', '.join(f'{d["speed_kph"]:g} km/h·{d["accel_mps2"]:g} m/s² {d["old"]:g}→{d["new"]:g}' for d in jump[:6]) if jump else '')))
    res.append(Check('표 형식', '걸림' if table_err else '통과', table_err or '단조·부호 통과(어댑터 검사와 같음)'))
    return res


def md_table(head, rows):
    return ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)] + ['| ' + ' | '.join(str(x) for x in r) + ' |' for r in rows]


def write_outputs(a, cfg, tr, pcs, cells, skipped, checks, diffs, acc_rows, verdict, gnss):
    out = a.out
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'segments.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, ['axis', 't0', 't1', 'pedal', 'speed_kph', 'speed_bin', 'a_mps2'])
        w.writeheader()
        for p in pcs:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()})
    acc = cfg['accel_bins_mps2']
    with open(os.path.join(out, 'cells.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['speed_kph', 'accel_mps2', 'pedal_mean', 'n', 'used'])
        for (sp, j), v in sorted(cells.items()):
            w.writerow([sp, acc[j], round(float(np.mean(v)), 2), len(v), int(len(v) >= CELL_MIN_SPANS)])
    lines = [f'# 종방향 표 갱신 — {verdict}', '',
             f'입력: `{a.can}` · GNSS: {gnss["source"] if gnss else "없음(미리보기, 후륜 ÷ 0.958)"} · 기존 표 v{cfg["version"]}',
             f'구간 {len(pcs)} 개(페달·가속도 부호가 달라 뺀 것 {skipped} 개), 분석 {tr["t"][-1] - tr["t"][0]:.0f} s', '', '## 검사', '']
    lines += md_table(['검사', '결과', '내용'], [(c.name, c.status, c.detail) for c in checks])
    lines += ['', '## 측정 칸(표본 ≥ 3 만 추천에 씀)', '']
    lines += md_table(['속도 km/h', '가속도 m/s²', '페달 % 평균', '표본'],
                      [(sp, acc[j], f'{np.mean(v):.1f}', len(v)) for (sp, j), v in sorted(cells.items())])
    if diffs:
        lines += ['', '## 기존 표와 차이', '']
        lines += md_table(['속도', '가속도', '기존', '추천', '표본', '변화'], [
            (d['speed_kph'], d['accel_mps2'], d['old'], d['new'], d['n'], '-' if d['rel'] is None else f'{d["rel"] * 100:.0f} %') for d in diffs])
    if acc_rows:
        lines += ['', '## 정확도(기존 표가 약속한 가속도 vs 실측, 기준 ±20 %, 지시서 2.4절)', '']
        lines += md_table(['속도', '축', '구간', '평균 오차', '최대 오차', '판정'], [
            (r['speed_kph'], r['axis'], r['n'], f'{r["err_mean"] * 100:.0f} %', f'{r["err_max"] * 100:.0f} %', '통과' if r['ok'] else '못 미침') for r in acc_rows])
    with open(os.path.join(out, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    with open(os.path.join(out, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(verdict=verdict, checks=[c.__dict__ for c in checks], pieces=len(pcs), cells=len(cells),
                       skipped=skipped, accuracy=acc_rows), f, ensure_ascii=False, indent=1)
    plot(out, cfg, pcs)


def plot(out, cfg, pcs):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as e:     # 그래프 없이도 표·판정은 낸다
        print(f'그래프 생략: {e}', file=sys.stderr)
        return
    fig, ax = plt.subplots(figsize=(7.5, 4))
    for axis, c in (('acc', 'tab:blue'), ('brake', 'tab:red')):
        p = [x for x in pcs if x['axis'] == axis]
        sign = 1 if axis == 'acc' else -1
        ax.scatter([x['a_mps2'] for x in p], [sign * x['pedal'] for x in p], s=10, c=c, label=axis, alpha=0.6)
    row = next(iter(cfg['pedal_pct'].values()))
    ax.plot(cfg['accel_bins_mps2'], [math.nan if v is None else v for v in row], 'k--', lw=1, label='current first row')
    ax.set(xlabel='measured accel [m/s2]', ylabel='pedal [%] (brake < 0)')
    ax.grid(alpha=0.3)
    ax.legend()
    fig.savefig(os.path.join(out, 'long_map.png'), dpi=130, bbox_inches='tight')
    plt.close(fig)


def write_recommended(a, cfg, new_table):
    new = dict(cfg)
    new['pedal_pct'] = new_table
    new['version'] = int(cfg['version']) + 1 if str(cfg['version']).isdigit() else cfg['version']
    new['source'] = os.path.basename(a.can) + ' → ' + a.out
    with open(os.path.join(a.out, 'long_map_recommended.yaml'), 'w', encoding='utf-8') as f:
        f.write('# 추천 표 — 자동 반영 금지. 사람이 보고 config/long_map.yaml 을 고친 뒤 커밋, docs/engine_room_map_history.md 에 적는다.\n')
        yaml.safe_dump({'long_map': new}, f, allow_unicode=True, sort_keys=False, default_flow_style=None)


def write_blocked(out, checks):
    with open(os.path.join(out, 'UPDATE_BLOCKED.md'), 'w', encoding='utf-8') as f:
        f.write('# ⛔ 표 갱신 차단 — 추천을 내지 않았다\n\n원인을 해결할 때까지 long_map 을 바꾸지 않는다.\n\n')
        f.write('\n'.join(md_table(['검사', '결과', '내용'], [(c.name, c.status, c.detail) for c in checks if not c.ok])) + '\n')


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--can', required=True, help='CAN 로그(평문·.zst) 또는 세션 폴더')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--bag')
    g.add_argument('--gnss-csv')
    g.add_argument('--can-only-preview', action='store_true')
    ap.add_argument('--gnss-topic', default='/gsof_client/odom')
    ap.add_argument('--ins-topic', default='/gsof_client/gsof/ins_solution_49')
    ap.add_argument('--events', default='', help='can_guard 이벤트 CSV. 안 주면 로그 옆 can_guard_events*.csv')
    ap.add_argument('--current', default=DEFAULT_MAP)
    ap.add_argument('--t0', type=float, default=None, help='로그 시작 기준 s(분석 시작)')
    ap.add_argument('--t1', type=float, default=None)
    ap.add_argument('--out', required=True)
    return ap.parse_args(argv)


def run(a):
    started = time.monotonic()
    a.can = resolve_can(a.can)
    try:
        with open(a.current, encoding='utf-8') as f:
            cfg = yaml.safe_load(f)['long_map']
    except OSError as e:
        raise InputError(f'기존 표를 읽을 수 없음: {e}')
    can = parse_can(read_lines(a.can))
    gnss = load_gnss_csv(a.gnss_csv) if a.gnss_csv else (load_gnss_bag(a.bag, a.gnss_topic, a.ins_topic) if a.bag else None)
    events_path = a.events
    if not events_path:
        d = os.path.dirname(os.path.abspath(a.can))
        c = [f for f in sorted(os.listdir(d)) if f.startswith('can_guard_events')]
        events_path = os.path.join(d, c[0]) if c else ''
    tr = make_trace(can, gnss, a.t0, a.t1)
    bad, why_bad = spans_bad(can, tr)
    pcs = pieces(tr, bad, cfg['speed_bins_kph'])
    cells, skipped = build_cells(pcs, cfg['accel_bins_mps2'])
    new_table, diffs = recommend(cfg, cells)
    checks = checks_can(why_bad, load_events(events_path), pcs)
    acc_rows = accuracy_rows(cfg, pcs)
    os.makedirs(a.out, exist_ok=True)
    if a.can_only_preview:
        hard = [c for c in checks if c.status == '걸림']
        verdict = '⛔ 차단' if hard else '미리보기(추천 없음, GNSS 없음 — 값은 참고)'
        write_outputs(a, cfg, tr, pcs, cells, skipped, checks, diffs, acc_rows, verdict, None)
        if hard:
            write_blocked(a.out, checks)
        rc = EXIT_BLOCKED if hard else EXIT_OK
    else:
        err = ''
        try:
            LongMap2D(dict(cfg, pedal_pct=new_table))
        except ValueError as e:
            err = str(e)
        checks += checks_gnss(tr, gnss, cells, diffs, err)
        blocked = [c for c in checks if not c.ok]
        verdict = '⛔ 차단' if blocked else '통과 — 추천 있음(사람 확인 후 반영)'
        write_outputs(a, cfg, tr, pcs, cells, skipped, checks, diffs, acc_rows, verdict, gnss)
        if blocked:
            write_blocked(a.out, checks)
            rc = EXIT_BLOCKED
        else:
            write_recommended(a, cfg, new_table)
            rc = EXIT_OK
    print(f'{verdict}  ({time.monotonic() - started:.1f} s) → {a.out}')
    for c in checks:
        print(f'  {c.status:6s} {c.name}: {c.detail}')
    return rc


def main(argv=None):
    a = parse_args(argv)
    try:
        return run(a)
    except InputError as e:
        print(f'입력 오류: {e}', file=sys.stderr)
        return EXIT_USAGE


if __name__ == '__main__':
    sys.exit(main())
