#!/usr/bin/env python3
"""00 SAF-ESTOP 분석 — CAN 로그(Kvaser 미러 --log / candump -l, .zst 가능)에서 비상 정지 사건마다 지표를 뽑는다.

  python3 bsw_test/00_SAF_estop/scripts/analysis.py <로그> [--events can_guard_events.csv] [--out <폴더>] [--plot]

트리거(사건 시작 t0)는 두 군데에서 모은다.
  ① 앱(원격) — CAN 의 0x156 EPS/ACC/AEB_En 이 0 → 1 이 된 첫 프레임, 또는 0x157 ACC_Cmd ≤ −1.0 이 된 첫 프레임
  ② can_guard 이벤트 로그(--events) — `estop_request`(estop.py·SIGUSR1·앱), `state ...->HOLDING`(명령 끊김)
같은 사건이 둘 다에 잡히면(1 s 안) 하나로 합친다. 시각은 둘 다 그 PC 의 벽시계(epoch)라 그대로 비교한다.

사건마다 내는 값(goal.md 합격 기준표의 칸과 같은 이름):
  v0_kph               t0 의 후륜 평균 속도(0x201, ÷ wheel_scale 보정)
  detect_ms            can_guard 감지 지연 = 이벤트 로그 estop_request/state 시각 − t0 (이벤트 로그 있을 때)
  guard_tx_ms          t0 → 우리 0x210 의 첫 보호 프레임(가속 0·브레이크 ≥ 30 %)
  app_en_s             앱 enable 지속 시간, app_acc_min / app_acc_le_m1_s(ACC_Cmd ≤ −1 이 유지된 시간)
  decel_start_ms       t0 → 속도가 v0 보다 0.5 km/h 떨어진 첫 시각 ("제동 시작"의 차속 기준 — 문턱 통과 시간 포함)
  brake_pos_rise_ms    t0 → 0x200 break_postion 이 t0 값보다 0.5 오른 첫 시각(안 오르면 빈칸 — 원격 우선이면 안 오름)
  stop_s / stop_m      t0 → 속도 < 0.3 km/h 시간·거리, mean_decel = v0 / stop_s
  wheel_lock           감속 중 후륜 속도 감소율 > 10 m/s² 가 0.1 s 넘게 이어지면 1
  steer_jump_deg       t0 −0.1 s 와 +0.5 s 사이 0x200 핸들각 변화(원격 우선이면 앱 EPS_Cmd 쪽으로 튐)
  follows_remote       En 구간에서 0x200 핸들각이 우리 0x210 보다 앱 EPS_Cmd 에 가까우면 1
  release_*            앱 En 이 꺼진 순간(t0 기준 s)·그때 속도·그 뒤 1 s 핸들각 변화·우리 브레이크 % — 해제되면 차량은 다시
                       우리 0x210(ESTOP 래치: 진입 때 핸들각 고정)을 따르므로 조향이 튈 수 있다
  two_senders          t0 −5 ~ +10 s 에 0x210 이 1 s 에 75 프레임을 넘으면 1(우리 50 + 원격 유닛 47 — 명령 충돌)
  auto000_after        t0 이후 로그 끝까지 우리 0x210 의 auto 000 프레임 수(0 이어야 함 — 페일세이프 브레이크 풀림)

출력: 표(stdout), --out 이면 `<로그이름>_estop_events.csv` 와(--plot) `<로그이름>_estop_<n>.png`.
판정은 하지 않는다 — goal.md 기준으로 사람이 results.md 에 적는다.
"""
import argparse
import bisect
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'tools', 'signal_audit'))
import dbc_lite  # noqa: E402

APP_ACC_STOP = -1.0          # can_guard --estop-acc-mps2 기본과 같게
MERGE_S = 1.0


def read_log(path):
    """(t, id, data bytes) 목록. candump -l 과 Kvaser 미러 --log 는 같은 `(ts) ch ID#DATA` 형식."""
    if path.endswith('.zst'):
        txt = subprocess.run(['zstd', '-dc', path], check=True, capture_output=True, text=True).stdout.splitlines()
    else:
        with open(path) as f:
            txt = f.read().splitlines()
    out = []
    for ln in txt:
        p = ln.split()
        if len(p) < 3 or not p[0].startswith('(') or '#' not in p[2]:
            continue
        i, d = p[2].split('#', 1)
        try:
            out.append((float(p[0][1:-1]), int(i, 16), bytes.fromhex(d)))
        except ValueError:
            continue
    return out


class Series:
    def __init__(self):
        self.t, self.v = [], []

    def add(self, t, v):
        self.t.append(t)
        self.v.append(v)

    def at(self, t):
        i = bisect.bisect_right(self.t, t) - 1
        return self.v[i] if i >= 0 else None

    def window(self, t0, t1):
        a, b = bisect.bisect_left(self.t, t0), bisect.bisect_right(self.t, t1)
        return list(zip(self.t[a:b], self.v[a:b]))


def decode(frames, wheel_scale):
    db = dbc_lite.load(os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc'))
    db.update(dbc_lite.remote_messages())
    s = {k: Series() for k in ('v', 'steer_pos', 'brake_pos', 'cmd', 'en', 'acc_cmd', 'eps_cmd')}
    for t, i, d in frames:
        m = db.get(i)
        if m is None:
            continue
        x = m.decode(d)
        if i == 0x201:
            s['v'].add(t, (x['left_speed'] + x['right_speed']) / 2 / wheel_scale)
        elif i == 0x200:
            s['steer_pos'].add(t, x['steer_postion'])
            s['brake_pos'].add(t, x['break_postion'])
        elif i == 0x210:
            auto = (int(x['steer_is_auto_command']), int(x['break_is_auto_command']), int(x['acc_is_auto_command']))
            s['cmd'].add(t, (x['steer_command'], x['break_command'], x['acc_command'], auto))
        elif i == 0x156:
            s['en'].add(t, int(x['EPS_En'] or x['ACC_En'] or x['AEB_En']))
        elif i == 0x157:
            s['acc_cmd'].add(t, round(x['ACC_Cmd'], 2))
            s['eps_cmd'].add(t, x['EPS_Cmd'])
    return s


def app_triggers(s):
    out = []
    prev_en, prev_acc = 0, 0.0
    for t, en in zip(s['en'].t, s['en'].v):
        if en and not prev_en:
            out.append((t, 'app_en'))
        prev_en = en
    for t, a in zip(s['acc_cmd'].t, s['acc_cmd'].v):
        if a <= APP_ACC_STOP < prev_acc:
            out.append((t, 'app_acc'))
        prev_acc = a
    return out


def event_triggers(path):
    out, rows = [], []
    with open(path) as f:
        for r in csv.reader(f):
            if len(r) < 4 or r[0].startswith('#') or r[1] == 'wall_s':
                continue
            # 이벤트 로그 원본(wall_s,mono_s,event,detail) 과 drive_log 묶음표(session,wall_s,event,detail) 둘 다 받는다
            try:
                t = float(r[0]) if _isnum(r[0]) and _isnum(r[1]) else float(r[1])
            except ValueError:
                continue
            ev, det = r[-2], r[-1]
            rows.append((t, ev, det))
            if ev == 'estop_request':
                out.append((t, 'guard:' + det.split()[0]))
            elif ev == 'state' and '->HOLDING' in det:
                out.append((t, 'guard:holding'))
    return out, rows


def _isnum(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


def merge(trigs):
    trigs.sort()
    out = []
    for t, src in trigs:
        if out and t - out[-1][0] < MERGE_S:
            out[-1][1].append(src)
        else:
            out.append((t, [src]))
    return out


def first(seq, pred):
    for t, v in seq:
        if pred(v):
            return t
    return None


def analyse(s, t0, srcs, ev_rows, t_end):
    r = {'t0': round(t0, 6), 'sources': '+'.join(srcs)}
    v0 = s['v'].at(t0)
    r['v0_kph'] = None if v0 is None else round(v0, 2)
    # can_guard 감지
    det = [t for t, ev, d in ev_rows if t0 - 0.05 <= t <= t0 + 2 and
           (ev == 'estop_request' or (ev == 'state' and ('->ESTOP' in d or '->HOLDING' in d)))]
    r['detect_ms'] = round((min(det) - t0) * 1e3, 1) if det else None
    cmd = s['cmd'].window(t0, t0 + 2)
    tg = first(cmd, lambda c: c[2] == 0 and c[1] >= 30)
    r['guard_tx_ms'] = round((tg - t0) * 1e3, 1) if tg else None
    # 앱
    en_win = s['en'].window(t0 - 0.05, t_end)
    t_en_off = first([(t, v) for t, v in en_win if t > t0], lambda v: v == 0)
    on = s['en'].at(t0 + 0.02)
    r['app_en_s'] = round(((t_en_off or t_end) - t0), 2) if on else 0.0
    accw = s['acc_cmd'].window(t0 - 0.05, t0 + 15)
    r['app_acc_min'] = min((a for _, a in accw), default=None)
    le = [t for t, a in accw if a <= APP_ACC_STOP]
    r['app_acc_le_m1_s'] = round(le[-1] - le[0] + 0.01, 2) if le else 0.0
    # 차량 응답
    vw = s['v'].window(t0, t0 + 30)
    if v0 is not None and v0 > 0.5:
        td = first(vw, lambda v: v <= v0 - 0.5)
        r['decel_start_ms'] = round((td - t0) * 1e3) if td else None
        ts = first(vw, lambda v: v < 0.3)
        r['stop_s'] = round(ts - t0, 2) if ts else None
        dist, prev = 0.0, None
        for t, v in vw:
            if ts and t > ts:
                break
            if prev:
                dist += prev[1] / 3.6 * (t - prev[0])
            prev = (t, v)
        r['stop_m'] = round(dist, 1)
        r['mean_decel'] = round(v0 / 3.6 / (ts - t0), 2) if ts else None
        lock, run = 0, 0.0
        for (ta, va), (tb, vb) in zip(vw, vw[1:]):
            if tb - ta > 0 and (va - vb) / 3.6 / (tb - ta) > 10:
                run += tb - ta
                lock |= run > 0.1
            else:
                run = 0.0
        r['wheel_lock'] = int(lock)
    else:
        r.update(decel_start_ms=None, stop_s=None, stop_m=None, mean_decel=None, wheel_lock=None)
    b0 = s['brake_pos'].at(t0)
    tb = first(s['brake_pos'].window(t0, t0 + 2), lambda b: b0 is not None and b >= b0 + 0.5)
    r['brake_pos0'] = None if b0 is None else round(b0, 1)
    r['brake_pos_rise_ms'] = round((tb - t0) * 1e3) if tb else None
    sa, sb = s['steer_pos'].at(t0 - 0.1), s['steer_pos'].at(t0 + 0.5)
    r['steer_jump_deg'] = round(sb - sa, 1) if sa is not None and sb is not None else None
    # 원격 우선 판정: En 구간 0x200 핸들각이 0x210 보다 앱 EPS_Cmd 에 가까운가
    fr = None
    if on:
        dg, da = [], []
        for t, p in s['steer_pos'].window(t0 + 0.3, min(t_en_off or t_end, t0 + 3)):
            c, e = s['cmd'].at(t), s['eps_cmd'].at(t)
            if c is not None and e is not None:
                dg.append(abs(p - c[0]))
                da.append(abs(p - e))
        if dg:
            fr = int(sum(da) / len(da) < sum(dg) / len(dg))
    r['follows_remote'] = fr
    # 앱 해제 순간: 차량이 다시 우리 0x210(ESTOP 래치 = 진입 때 핸들각 + 브레이크)을 따른다 → 조향이 튀는지
    if on and t_en_off:
        r['release_s'] = round(t_en_off - t0, 2)
        vr = s['v'].at(t_en_off)
        r['release_v_kph'] = None if vr is None else round(vr, 2)
        sa, sb = s['steer_pos'].at(t_en_off), s['steer_pos'].at(t_en_off + 1.0)
        r['release_steer_jump_deg'] = round(sb - sa, 1) if sa is not None and sb is not None else None
        c = s['cmd'].at(t_en_off + 0.05)
        r['release_guard_brake'] = None if c is None else c[1]
    else:
        r.update(release_s=None, release_v_kph=None, release_steer_jump_deg=None, release_guard_brake=None)
    r['two_senders'] = int(any(n > 75 for n in rate_bins(s['cmd'].t, t0 - 5, t0 + 10)))
    r['auto000_after'] = sum(1 for _, c in s['cmd'].window(t0, t_end) if c[3] == (0, 0, 0))
    return r


def rate_bins(ts, t_a, t_b):
    """[t_a, t_b) 를 1 s 칸으로 나눈 0x210 프레임 수. can_guard 50 /s + 원격 유닛 47 /s 면 약 97 /s."""
    out, t = [], t_a
    while t < t_b:
        out.append(bisect.bisect_left(ts, t + 1) - bisect.bisect_left(ts, t))
        t += 1
    return out


def plot(s, t0, r, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for p in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'):
        if os.path.exists(p):
            font_manager.fontManager.addfont(p)
            matplotlib.rcParams['font.family'] = font_manager.FontProperties(fname=p).get_name()
            matplotlib.rcParams['axes.unicode_minus'] = False
            break
    rng = (t0 - 2, t0 + 10)
    fig, ax = plt.subplots(4, 1, figsize=(9, 9), sharex=True)
    rel = lambda w: ([t - t0 for t, _ in w], [v for _, v in w])  # noqa: E731
    ax[0].plot(*rel(s['v'].window(*rng)), 'k', lw=1.2)
    ax[0].set_ylabel('후륜 속도 [km/h]')
    w = s['acc_cmd'].window(*rng)
    ax[1].plot(*rel(w), 'C3', lw=1.2, label='앱 ACC_Cmd [m/s²]')
    ax[1].plot(*rel(s['en'].window(*rng)), 'C7', lw=1, ls='--', label='앱 En (0x156)')
    ax[1].legend(loc='lower right', fontsize=8)
    cw = s['cmd'].window(*rng)
    ax[2].plot([t - t0 for t, _ in cw], [c[1] for _, c in cw], 'C0', lw=1.2, label='우리 0x210 브레이크 [%]')
    bw = s['brake_pos'].window(*rng)
    ax[2].plot([t - t0 for t, _ in bw], [b * 10 for _, b in bw], 'C1', lw=1.2, label='0x200 브레이크 위치 ×10')
    ax[2].legend(loc='upper right', fontsize=8)
    ax[3].plot([t - t0 for t, _ in cw], [c[0] for _, c in cw], 'C0', lw=1.2, label='우리 0x210 핸들 °')
    ax[3].plot(*rel(s['steer_pos'].window(*rng)), 'C2', lw=1.2, label='0x200 핸들 °')
    ax[3].plot(*rel(s['eps_cmd'].window(*rng)), 'C3', lw=1, ls='--', label='앱 EPS_Cmd 핸들 °')
    ax[3].legend(loc='upper right', fontsize=8)
    ax[3].set_xlabel('트리거 기준 시간 [s]')
    for a in ax:
        a.axvline(0, color='r', lw=0.8)
        a.grid(alpha=0.3)
    fig.suptitle(f"비상 정지 {r['sources']}  v0 {r['v0_kph']} km/h  정지 {r['stop_s']} s / {r['stop_m']} m", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log')
    ap.add_argument('--events', help='can_guard --event-log CSV (또는 drive_log tables/canguard_events.csv)')
    ap.add_argument('--session', help='--events 가 여러 세션 묶음표일 때 고를 세션 이름(첫 열)')
    ap.add_argument('--wheel-scale', type=float, default=0.958, help='0x201 ÷ 이 값(11-04 GNSS 대비 0.958, 잠정)')
    ap.add_argument('--out')
    ap.add_argument('--name', help='출력 파일 이름 앞부분(기본: 로그 파일 이름) — 예 20261104_SAF-ESTOP-01_r1')
    ap.add_argument('--plot', action='store_true')
    a = ap.parse_args()

    frames = read_log(a.log)
    if not frames:
        sys.exit('프레임 0 — 형식 확인')
    s = decode(frames, a.wheel_scale)
    trigs, ev_rows = app_triggers(s), []
    if a.events:
        if a.session:
            tmp = []
            with open(a.events) as f:
                for r in csv.reader(f):
                    if r and r[0] == a.session:
                        tmp.append(r)
            ev_rows = [(float(r[1]), r[2], r[3]) for r in tmp]
            trigs += [(t, 'guard:' + d.split()[0]) for t, e, d in ev_rows if e == 'estop_request']
            trigs += [(t, 'guard:holding') for t, e, d in ev_rows if e == 'state' and '->HOLDING' in d]
        else:
            et, ev_rows = event_triggers(a.events)
            trigs += et
    t_end = frames[-1][0]
    rows = [analyse(s, t0, srcs, ev_rows, t_end) for t0, srcs in merge(trigs)]
    base = a.name or os.path.basename(a.log).split('.')[0]
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    keys = list(rows[0]) if rows else ['t0']
    print(f'# {a.log}  프레임 {len(frames)}  사건 {len(rows)}  (0x201 ÷ {a.wheel_scale})')
    ct = s['cmd'].t
    two = sum(1 for n in rate_bins(ct, ct[0], ct[-1]) if n > 75) if ct else 0
    nf = sum(1 for _, e, _ in ev_rows if e == 'foreign_0x210')
    if two or nf:
        print(f'# ⚠️ 0x210 송신자 둘(1 s 에 75 프레임 초과) {two} s, can_guard foreign_0x210 이벤트 {nf} 줄 — 원격 유닛이 켜져 있었다')
    print('\t'.join(keys))
    for i, r in enumerate(rows, 1):
        print('\t'.join('' if r[k] is None else str(r[k]) for k in keys))
        if a.out and a.plot:
            plot(s, r['t0'], r, os.path.join(a.out, f'{base}_estop_{i}.png'))
    if a.out:
        with open(os.path.join(a.out, f'{base}_estop_events.csv'), 'w', newline='') as f:
            f.write(f'# 00 SAF-ESTOP analysis.py — 원본 {a.log}, 0x201 ÷ {a.wheel_scale}, 판정 아님\n')
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)


if __name__ == '__main__':
    main()
