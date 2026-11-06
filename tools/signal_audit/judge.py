#!/usr/bin/env python3
"""녹화 원본 → 신호별 자동 판정표(results.md). cards.md 의 '판정' 칸을 채울 근거.

  python3 tools/signal_audit/judge.py measurements/2026-11-XX_signal_audit
  python3 tools/signal_audit/judge.py <세션폴더> --raw ~/a1_race_capture/vehicle_20261007   # 원본이 다른 곳일 때

입력: <raw>(기본 <세션폴더>/raw) 안의 candump -L 로그(kvaser_ch0*.log 우선, 없으면 candump-*.log)와 lift_cmd_*.log.
읽는 규칙은 tools/race_day/summarize_session.py 와 같다(그 함수를 그대로 가져다 쓴다).
출력: <세션폴더>/results.md, <세션폴더>/limits.md(명령 한계 초안 — limits.py). 그림은 plot.py.
원격 0x156/0x157 도 같이 해석한다(dbc_lite.remote_messages).

자동으로 하는 것(사람 판단 없이 숫자로 나오는 것만):
  1. ID별 개수·주기·DLC, DBC 밖 ID
  2. DBC 모든 신호: 개수·min·max·서로 다른 값 수·범위 위반 — '변화 없음'이면 자극이 없었다는 뜻
  3. live_counter: +1 / 반복 / 건너뜀 / 정지 비율
  4. 명령→상태 응답: 0x210 조향·브레이크 계단 → 0x200 위치, 가속 구간 → 0x201 바퀴, auto 비트 → 0x200 수락 지연
  5. lift_cmd 입력 이력(우리가 보낸 명령의 근거)
최종 O / X 는 사람이 cards.md 에 적는다 — 0x210 은 송신자를 구분할 수 없어서(외부 송신자도 같은 ID) 자동 판정을 믿지 말 것.
"""
import argparse
import bisect
import datetime
import os
import statistics
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'tools', 'race_day'))
import audit_data  # noqa: E402
import limits  # noqa: E402
from audit_data import load_lift_cmd  # noqa: E402

DEFAULT_DBC = os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc')
SETTLE_S = 1.0        # 계단 뒤 이 시간의 위치를 정착값으로 본다(10-07 63 % 응답 약 0.2 s)
ACC_WINDOW_S = 3.0    # 가속 명령 시작부터 이 시간 안의 바퀴 최대 속도
MIN_HOLD_S = 0.3      # 이보다 짧게 유지된 명령 값은 계단으로 보지 않는다(송신자 둘일 때의 깜빡임)


def hms(t):
    return datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S.%f')[:-3]


def pct(a, b):
    return f'{100 * a / b:.1f} %' if b else '—'


class Series:
    """한 ID 의 (t, 해석값) 시계열 + 시각으로 찾기."""

    def __init__(self, frames, msg):
        self.t = [t for t, _, _ in frames]
        self.v = [msg.decode(d) for _, _, d in frames]

    def at(self, t):
        i = bisect.bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else None

    def window(self, t0, t1):
        i, j = bisect.bisect_left(self.t, t0), bisect.bisect_right(self.t, t1)
        return list(zip(self.t[i:j], self.v[i:j]))


def section_ids(frames, msgs, w):
    by = {}
    for t, fid, d in frames:
        by.setdefault(fid, []).append((t, d))
    w('## 1. ID별 개수·주기\n')
    w('| ID | DBC 이름 | 개수 | DLC | 주기 중앙값 / p99 (ms) | 최대 공백 (s) |')
    w('|---|---|---|---|---|---|')
    for fid in sorted(by):
        rows = by[fid]
        dt = [(b[0] - a[0]) * 1000 for a, b in zip(rows, rows[1:])]
        q = sorted(dt)
        per = f'{statistics.median(q):.1f} / {q[int(0.99 * (len(q) - 1))]:.1f}' if q else '—'
        gap = f'{max(q) / 1000:.1f}' if q else '—'
        dl = ','.join(str(k) for k in sorted(Counter(len(d) for _, d in rows)))
        name = msgs[fid].name if fid in msgs else '**DBC 밖**'
        if fid in msgs and dl != str(msgs[fid].dlc):
            dl += f' ⚠️ DBC {msgs[fid].dlc}'
        w(f'| 0x{fid:03X} | {name} | {len(rows)} | {dl} | {per} | {gap} |')
    unseen = [f'0x{f:03X} {m.name}' for f, m in sorted(msgs.items()) if f not in by]
    w(f'\n**DBC 에 있으나 안 보인 메시지**: {", ".join(unseen) or "없음"}\n')
    return by


def section_signals(by, msgs, w):
    w('## 2. 신호별 값 (DBC 전 신호)\n')
    w('> "변화 없음" = 이 녹화에서 그 신호를 움직인 자극이 없었다 → 카드의 자극을 다음에 해야 함. '
      '범위 위반 = DBC [min, max] 밖 값 개수.\n')
    w('| ID | 신호 | 개수 | min | max | 서로 다른 값 | 범위 위반 | 자동 표시 |')
    w('|---|---|---|---|---|---|---|---|')
    for fid in sorted(msgs):
        m = msgs[fid]
        rows = by.get(fid, [])
        for s in m.signals:
            if not rows:
                w(f'| 0x{fid:03X} | `{s.name}` | 0 | | | | | 안 보임 |')
                continue
            vals = [round(s.phys(d), 6) for _, d in rows]
            lo, hi = min(vals), max(vals)
            viol = sum(1 for v in vals if v < s.minimum - 1e-9 or v > s.maximum + 1e-9)
            nd = len(set(vals))
            mark = '변화 없음' if nd == 1 else ('⚠️ 범위 위반' if viol else '변화 관측')
            w(f'| 0x{fid:03X} | `{s.name}` | {len(vals)} | {lo:g} | {hi:g} | {nd} | {viol} | {mark} |')
    w('')


def section_counters(by, msgs, w):
    w('## 3. live_counter\n')
    w('| ID | 신호 | +1 | 반복(같은 값) | 건너뜀 | 최장 정지 (프레임) |')
    w('|---|---|---|---|---|---|')
    for fid in sorted(msgs):
        rows = by.get(fid, [])
        for s in msgs[fid].signals:
            if 'live_counter' not in s.name or len(rows) < 2:
                continue
            mod = 1 << s.length
            vals = [s.raw(d) for _, d in rows]
            inc = Counter((b - a) % mod for a, b in zip(vals, vals[1:]))
            n = len(vals) - 1
            skip = n - inc[1] - inc[0]
            run = best = 1
            for a, b in zip(vals, vals[1:]):
                run = run + 1 if a == b else 1
                best = max(best, run)
            w(f'| 0x{fid:03X} | `{s.name}` | {pct(inc[1], n)} | {pct(inc[0], n)} | {pct(skip, n)} | {best} |')
    w('')


def steps(series_cmd, key, auto_key, min_hold=MIN_HOLD_S):
    """명령 시계열에서 (auto 켜진 상태의) 값이 바뀌어 min_hold 이상 유지된 계단 [(t, 이전값, 새값)].

    0x210 송신자가 둘이면(10-07 11:03~11:09) 값이 프레임마다 번갈아 바뀐다 — 그런 깜빡임은 계단이 아니므로 버린다.
    """
    held = []   # [t0, t_last, value] — 같은 값이 이어진 구간
    for t, v in zip(series_cmd.t, series_cmd.v):
        cur = v[key] if auto_key is None or v[auto_key] else None
        if held and held[-1][2] == cur:
            held[-1][1] = t
        else:
            held.append([t, t, cur])
    stable = [h for h in held if h[2] is not None and h[1] - h[0] >= min_hold]
    return [(b[0], a[2], b[2]) for a, b in zip(stable, stable[1:]) if a[2] != b[2]]


def section_response(by, msgs, w):
    w('## 4. 명령 → 차량 응답\n')
    if 0x210 not in by or 0x200 not in by:
        w('0x210 또는 0x200 이 없어 생략.\n')
        return
    cmd = Series([(t, 0x210, d) for t, d in by[0x210]], msgs[0x210])
    info = Series([(t, 0x200, d) for t, d in by[0x200]], msgs[0x200])
    wheel = Series([(t, 0x201, d) for t, d in by[0x201]], msgs[0x201]) if 0x201 in by else None

    w(f'### 4-1. 조향 계단 → 0x200 steer_postion ({SETTLE_S:g} s 뒤)\n')
    w('| 시각 | 명령 이전 → 이후 (°) | 위치 시작 → 정착 (°) | 위치 변화 / 명령 변화 |')
    w('|---|---|---|---|')
    for t, a, b in steps(cmd, 'steer_command', 'steer_is_auto_command'):
        p0, p1 = info.at(t), info.at(t + SETTLE_S)
        if p0 is None or p1 is None:
            continue
        r = (p1['steer_postion'] - p0['steer_postion']) / (b - a) if b != a else float('nan')
        w(f'| {hms(t)} | {a:+.1f} → {b:+.1f} | {p0["steer_postion"]:+.1f} → {p1["steer_postion"]:+.1f} | {r:.2f} |')

    w(f'\n### 4-2. 브레이크 계단 → 0x200 break_postion ({SETTLE_S:g} s 뒤, 기대 = 명령 %)\n')
    w('| 시각 | 명령 이전 → 이후 (%) | 위치 % 시작 → 정착 | 정착 / 명령 |')
    w('|---|---|---|---|')
    for t, a, b in steps(cmd, 'break_command', 'break_is_auto_command'):
        p0, p1 = info.at(t), info.at(t + SETTLE_S)
        if p0 is None or p1 is None:
            continue
        r = f'{p1["break_postion"] / b:.2f}' if b else '—'
        w(f'| {hms(t)} | {a:g} → {b:g} | {p0["break_postion"]:.0f} → {p1["break_postion"]:.0f} | {r} |')

    w(f'\n### 4-3. 가속 명령 → 0x201 바퀴 ({ACC_WINDOW_S:g} s 안 최대)\n')
    w('| 시각 | 가속 (%) | 그때 break_postion (%) | 좌 / 우 최대 km/h | 좌 / 우 최대 rpm |')
    w('|---|---|---|---|---|')
    for t, a, b in steps(cmd, 'acc_command', 'acc_is_auto_command'):
        if b <= 0:
            continue
        br = info.at(t)
        brs = f'{br["break_postion"]:.0f}' if br else '?'
        if wheel:
            win = [v for _, v in wheel.window(t, t + ACC_WINDOW_S)]
            ls = max((v['left_speed'] for v in win), default=0)
            rs = max((v['right_speed'] for v in win), default=0)
            lr = max((v['left_rpm'] for v in win), default=0)
            rr = max((v['right_rpm'] for v in win), default=0)
            wh = f'{ls:.1f} / {rs:.1f} | {lr:g} / {rr:g}'
        else:
            wh = '0x201 없음 | '
        w(f'| {hms(t)} | {b:g} | {brs} | {wh} |')

    w('\n### 4-4. auto 비트 → 0x200 수락 지연\n')
    w('| 시각 | 축 | 명령 | 수락까지 (ms) |')
    w('|---|---|---|---|')
    pairs = [('steer_is_auto_command', 'steer_is_auto'), ('break_is_auto_command', 'break_is_auto'),
             ('acc_is_auto_command', 'acc_is_auto')]
    for ck, sk in pairs:
        for t, a, b in steps(cmd, ck, None):
            lat = '안 따라옴(1 s)'
            for t2, st in info.window(t, t + 1.0):
                if st[sk] == b:
                    lat = f'{(t2 - t) * 1000:.0f}'
                    break
            w(f'| {hms(t)} | {sk.split("_")[0]} | {int(a)} → {int(b)} | {lat} |')
    w('')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session', help='measurements/<날짜>_signal_audit 폴더')
    ap.add_argument('--raw', help='원본 폴더(기본 <session>/raw)')
    ap.add_argument('--dbc', default=DEFAULT_DBC)
    ap.add_argument('-o', '--output', help='기본 <session>/results.md')
    a = ap.parse_args()
    raw = a.raw or os.path.join(a.session, 'raw')
    sess = audit_data.Session(raw, a.dbc)
    msgs, files, frames = sess.msgs, sess.files, sess.frames
    if not frames:
        print(f'{raw} 에 candump 로그(kvaser_ch0*.log / candump-*.log)가 없다', file=sys.stderr)
        return 2
    out = []
    w = out.append
    w('# 자동 판정표\n')
    w(f'> `tools/signal_audit/judge.py` 생성. DBC `{os.path.relpath(a.dbc, REPO)}`, 원본 {len(files)}개 파일 '
      f'({", ".join(os.path.relpath(p, raw) for p in files)}), 프레임 {len(frames):,}, '
      f'{hms(frames[0][0])} ~ {hms(frames[-1][0])}.')
    w('> 숫자만 낸다 — O / X 는 cards.md 에 사람이 적는다. 0x210 은 송신자 구분 불가(외부 송신자 섞일 수 있음).\n')
    by = section_ids(frames, msgs, w)
    section_signals(by, msgs, w)
    section_counters(by, msgs, w)
    section_response(by, msgs, w)
    w('## 5. lift_cmd 입력 이력\n')
    cmds = load_lift_cmd(raw)
    w('\n'.join(f'- {hms(t)} {txt}' for t, _, txt in cmds) or '없음')
    path = a.output or os.path.join(a.session, 'results.md')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w') as f:
        f.write('\n'.join(out) + '\n')
    print(f'{path} 작성 — 프레임 {len(frames):,}', file=sys.stderr)
    if not a.output:
        print(f'{limits.write(sess, a.session)} 작성 — 그림은 plot.py', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
