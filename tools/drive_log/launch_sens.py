"""출발 민감도(가속 % 불감대)·주행 중 유지 % — 표 3개 + 데이터시트 sheet5_launch.png.

    python3 tools/drive_log/cannpz.py measurements/2026-11-04_drive_log/raw_can <npz>   # 먼저 한 번
    python3 tools/drive_log/launch_sens.py <npz> measurements/2026-11-04_drive_log/tables measurements/2026-11-04_drive_log/sheets

원천: 11-04 CAN 원본 전부(원격 오전 + 자율 bsw3~5 + 빠른 시험). 앱 원격 켜진 시간(±2 s) 제외. 속도 = 0x201 좌우 평균 ÷ 0.958.
  정지 출발 시도 = 1 s 이상 정지(< 0.1 km/h) 뒤 브레이크 0 + 가속 > 0 이 이어진 구간(0.3 s 이상, 3 s 안 중복은 첫 것만).
  출발 = 0x201 > 0.3 km/h.  출발 지연 = 첫 가속 ≥ 75 % 명령 → 출발.
  주행 중 유지 구간 = 가속 ±4 % 안·브레이크 0 으로 0.8 s 이상, 그동안 차속 > 0.5 km/h. 가속도 = [시작+0.5 s, 끝+0.5 s] 차속 직선 기울기.
"""
import csv, os, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cannpz import sessions, load, app_mask, runs  # noqa: E402

NPZ, TAB, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
MOVE, LAG, HOLD_MIN, TOL = 0.3, 0.5, 0.8, 4

# ───────────────────────── 1. 계산
attempts, segs = [], []
S = sessions(NPZ)
D = {k: load(p) for k, p in S.items()}
for sess, d in D.items():
    tc = d['tc']
    if len(tc) == 0:
        continue
    app = app_mask(d, tc)
    # 1-a 정지 출발 시도
    last = -1e9
    for a, b in runs((d['acc'] > 0) & (d['brk'] <= 0) & ~app):
        t0, t1 = tc[a], tc[b - 1]
        pre = (d['tw'] >= t0 - 1.0) & (d['tw'] < t0)
        if t1 - t0 < 0.3 or pre.sum() < 10 or d['v'][pre].max() > 0.1 or t0 - last < 3.0:
            continue
        last = t0
        wm = (d['tw'] >= t0) & (d['tw'] <= t1 + 0.5)
        tw, vw = d['tw'][wm], d['v'][wm]
        mv = np.where(vw > MOVE)[0]
        moved = len(mv) > 0
        tm = tw[mv[0]] if moved else np.nan
        seg = d['acc'][a:b]
        upto = seg[: max(1, np.searchsorted(tc[a:b], tm))] if moved else seg
        hi = (tc >= t0) & (tc <= (tm if moved else t1)) & (d['acc'] >= 75)
        t75 = tc[hi][0] if hi.any() else np.nan
        bm = (tc < t0) & (d['brk'] > 0)
        # 정지 중 바퀴 미세 움직임(엔코더 1 rpm·0.1 km/h)
        creep = bool(((d['lrpm'][wm] > 0) | (d['rrpm'][wm] > 0)).any()) if not moved else True
        attempts.append(dict(session=sess, t0=round(t0, 3), hold_s=round(t1 - t0, 2),
                             acc_level=float(np.median(upto)), acc_max_before_move=float(upto.max()),
                             moved=int(moved), t_move_s=round(tm - t0, 3) if moved else '',
                             lag_from_75pct_s=round(tm - t75, 3) if moved and np.isfinite(t75) else '',
                             brake_released_before_s=round(t0 - tc[bm][-1], 2) if bm.any() else '',
                             wheel_creep=int(creep), vmax_kph=round(float(vw.max()), 2) if len(vw) else 0.0))
    # 1-b 주행 중 유지 구간
    acc, brk, n, i = d['acc'], d['brk'], len(tc), 0
    while i < n:
        if brk[i] > 0 or app[i]:
            i += 1
            continue
        j, lo, hi_ = i, acc[i], acc[i]
        while (j + 1 < n and brk[j + 1] <= 0 and not app[j + 1] and tc[j + 1] - tc[j] < 0.1
               and max(hi_, acc[j + 1]) - min(lo, acc[j + 1]) <= TOL):
            j += 1
            lo, hi_ = min(lo, acc[j]), max(hi_, acc[j])
        t0, t1 = tc[i], tc[j]
        if t1 - t0 >= HOLD_MIN:
            m = (d['tw'] >= t0 + LAG) & (d['tw'] <= t1 + LAG)
            if m.sum() > 10 and d['v'][m].min() > 0.5:
                tw, v = d['tw'][m], d['v'][m]
                pm = (tc >= t0 - 1.0) & (tc < t0)
                segs.append(dict(session=sess, t0=round(t0, 2), hold_s=round(t1 - t0, 2), acc_pct=round(float(acc[i:j + 1].mean()), 1),
                                 v_start_kph=round(float(v[0]), 2), v_end_kph=round(float(v[-1]), 2), v_mean_kph=round(float(v.mean()), 2),
                                 accel_mps2=round(float(np.polyfit(tw - tw[0], v / 3.6, 1)[0]), 3),
                                 prev_acc_max=float(acc[pm].max()) if pm.any() else ''))
        i = j + 1


def wcsv(name, rows, note):
    with open(os.path.join(TAB, name), 'w', newline='') as f:
        f.write(f'# {note}\n')
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


wcsv('launch_attempts.csv', attempts, '정지 출발 시도(1 s 정지 → 브레이크 0 + 가속 > 0). 앱 원격 시간 제외. 출발 = 0x201÷0.958 > 0.3 km/h. launch_sens.py')
wcsv('launch_hold_segments.csv', segs, '주행 중 가속 % 유지 구간(±4 %, ≥ 0.8 s, 브레이크 0, v > 0.5 km/h) → 가속도(지연 0.5 s 직선 맞춤). launch_sens.py')

A = np.array([[s['acc_pct'], s['v_mean_kph'], s['accel_mps2']] for s in segs])
BINS = [(0, 1, '0 (타행)'), (1, 50, '1–50'), (50, 55, '50–55'), (55, 60, '55–60'), (60, 65, '60–65'), (65, 70, '65–70'),
        (70, 75, '70–75'), (75, 80, '75–80'), (80, 85, '80–85'), (85, 90, '85–90'), (90, 95, '90–95'), (95, 101, '95–100')]
binrows = []
for lo, hi, lab in BINS:
    m = (A[:, 0] >= lo) & (A[:, 0] < hi)
    if m.sum():
        binrows.append(dict(acc_bin=lab, n_segments=int(m.sum()), accel_p50=round(float(np.median(A[m, 2])), 2),
                            accel_p25=round(float(np.percentile(A[m, 2], 25)), 2), accel_p75=round(float(np.percentile(A[m, 2], 75)), 2),
                            v_range_kph=f'{A[m, 1].min():.0f}–{A[m, 1].max():.0f}'))
wcsv('launch_rolling_bins.csv', binrows, '주행 중 유지 구간 가속도, 가속 % 구간별(모든 속도). launch_sens.py')
for r in binrows:
    print(r)

# ───────────────────────── 2. 그림
for f in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc']:
    if os.path.exists(f):
        fm.fontManager.addfont(f)
plt.rcParams.update({'font.family': 'Noto Sans CJK JP', 'font.size': 10, 'axes.unicode_minus': False,
                     'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e',
                     'axes.grid': True, 'grid.color': '#e6e5e0', 'grid.linewidth': 0.8, 'axes.spines.top': False, 'axes.spines.right': False,
                     'figure.facecolor': '#fcfcfb', 'axes.facecolor': '#fcfcfb', 'legend.frameon': False})
C1, C2, C3, C4 = '#2a78d6', '#eb6834', '#1baf7a', '#8a5cd6'
INK, INK2, MUTED, GRID, BAND = '#0b0b0b', '#52514e', '#8a8984', '#d9d8d2', '#f1f0ec'

fig = plt.figure(figsize=(15.6, 21.5))
gs = fig.add_gridspec(5, 2, height_ratios=[1.0, 1.0, 1.15, 1.0, 0.95], hspace=0.42, wspace=0.18,
                      left=0.06, right=0.97, top=0.935, bottom=0.03)
fig.text(0.04, 0.975, '출발 민감도 데이터시트 — 0x210 acc_command 불감대 · 주행 중 유지 %', fontsize=17, fontweight='bold', color=INK, va='top')
fig.text(0.04, 0.955, '2026-11-04 용인 · CAN 원본(Kvaser) 전부: 원격(오전) + 자율 bsw3~5 + 빠른 시험 · 앱 원격 켜진 시간 제외 · '
         '속도 0x201 ÷ 0.958 · 평지 가정(경사 미보정) · 잠정값', fontsize=9.5, color=INK2, va='top')


def band(ax, lo, hi, color, label, y=0.97):
    ax.axvspan(lo, hi, color=color, alpha=0.13, lw=0)
    ax.text((lo + hi) / 2, y, label, transform=ax.get_xaxis_transform(), ha='center', va='top', fontsize=8.5, color=INK2)


# ① 정지 출발 시도
ax = fig.add_subplot(gs[0, :])
band(ax, 48, 60, C4, '타 팀 문의값\n48–60 %')
band(ax, 70, 80, C2, '11-04 정지 출발 경계\n70 % 실패 · 80 % 성공')
for at in attempts:
    x, y = at['acc_max_before_move'], at['hold_s']   # 출발 전(못 했으면 전체) 최대 명령
    if at['moved']:
        ax.scatter(x, min(y, 6), s=46, color=C1, zorder=3, edgecolor='white', lw=0.6)
    else:
        ax.scatter(x, min(y, 6), s=60, marker='x', color=C2 if at['wheel_creep'] else MUTED, zorder=3, lw=1.8)
quick = [a for a in attempts if a['session'].startswith('quick_QUICK') and a['hold_s'] >= 3]
for a in quick:
    ax.annotate(f"{a['acc_level']:.0f} %", (a['acc_max_before_move'], min(a['hold_s'], 6)), xytext=(0, 9), textcoords='offset points',
                ha='center', fontsize=8.5, color=INK)
ax.scatter([], [], s=46, color=C1, label='출발함(> 0.3 km/h)')
ax.scatter([], [], marker='x', color=C2, label='출발 못 함 — 바퀴 엔코더 1 rpm 움찔(토크는 나옴)')
ax.scatter([], [], marker='x', color=MUTED, label='출발 못 함 — 움직임 없음')
ax.set_xlim(-2, 103); ax.set_ylim(0, 7.4)
ax.set_xlabel('가속 명령 [%] (출발 전까지 최대값, 못 했으면 구간 최대값) · 6 s 넘는 유지는 6 에 표시'); ax.set_ylabel('브레이크 0 + 가속 유지 시간 [s]')
ax.set_title('① 정지 출발 시도 — 몇 % 를 얼마나 줘야 출발하나', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.legend(loc='upper left', fontsize=9, ncol=3)

# ② 정지 출발 파형 (빠른 시험)
ax = fig.add_subplot(gs[1, 0])
cols = {10: '#c9c8c2', 15: '#c9c8c2', 20: '#c9c8c2', 30: '#b5b4ae', 40: '#8a8984', 60: C4, 70: C2, 80: C3, 100: C1}
seen = set()
for a in sorted(quick + [x for x in attempts if x['session'].startswith('quick_QUICK') and x['acc_level'] in (80, 100)],
                key=lambda r: r['acc_level']):
    lv = int(a['acc_level'])
    if lv in seen or lv not in cols:
        continue
    seen.add(lv)
    d = D[a['session']]
    m = (d['tw'] >= a['t0'] - 0.5) & (d['tw'] <= a['t0'] + 5.0)
    ax.plot(d['tw'][m] - a['t0'], d['v'][m], color=cols[lv], lw=2.0 if lv >= 60 else 1.2, label=f'{lv} %')
ax.axvline(0, color=INK2, lw=0.8)
ax.set_xlim(-0.5, 5); ax.set_ylim(-0.1, 4)
ax.set_xlabel('가속 명령 시작부터 [s]  (브레이크는 1.5~16 s 전에 이미 0)'); ax.set_ylabel('차속 [km/h]')
ax.set_title('② 정지 출발 파형 — 빠른 시험(평지, 5 s 유지)', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.legend(ncol=3, fontsize=8.5, loc='upper left')
ax.text(4.9, 2.4, '60 %·70 %: 0.3 s 에 오른 뒷바퀴 1 rpm 한 번(엔코더 1칸)\n→ 토크는 나오지만 정지 마찰을 못 넘음',
        ha='right', fontsize=8.5, color=C2)

# ③ 출발 지연
ax = fig.add_subplot(gs[1, 1])
lagrows = [a for a in attempts if a['moved'] and a['lag_from_75pct_s'] != '']
for a in lagrows:
    br = a['brake_released_before_s']
    recent = br != '' and br < 2.5
    ax.scatter(a['acc_max_before_move'], a['lag_from_75pct_s'] * 1000, s=40, color=C2 if recent else C1, edgecolor='white', lw=0.6)
ax.scatter([], [], color=C2, label='브레이크를 2.5 s 안에 풀고 출발(자율·빠른 시험)')
ax.scatter([], [], color=C1, label='브레이크 오래전에 풀림(원격 대부분)')
for grp, c, y in [([a for a in lagrows if a['brake_released_before_s'] != '' and a['brake_released_before_s'] < 2.5], C2, 0.93),
                  ([a for a in lagrows if not (a['brake_released_before_s'] != '' and a['brake_released_before_s'] < 2.5)], C1, 0.86)]:
    if grp:
        med = np.median([g['lag_from_75pct_s'] for g in grp]) * 1000
        ax.axhline(med, color=c, lw=1, ls='--')
        ax.text(0.02, y, f'중앙값 {med:.0f} ms (n={len(grp)})', transform=ax.transAxes, color=c, fontsize=9)
ax.set_xlim(73, 102); ax.set_ylim(250, 900)
ax.set_xlabel('출발 직전 가속 명령 최대 [%]'); ax.set_ylabel('첫 ≥ 75 % 명령 → 출발 [ms]')
ax.set_title('③ 출발 지연 — 명령 % 보다 브레이크 해제 시점이 좌우', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.legend(loc='lower right', fontsize=8.5)

# ④ 주행 중 유지 구간
ax = fig.add_subplot(gs[2, :])
band(ax, -1, 50, '#b5b4ae', '구동 없음\n(타행과 같음)', y=0.98)
band(ax, 50, 60, C4, '구동 시작\n(감속이 줄어듦)', y=0.98)
band(ax, 60, 85, C3, '속도 유지 근처\n(≈ 0 m/s²)', y=0.98)
band(ax, 85, 101, C1, '가속', y=0.98)
vcol = [(0.5, 8, C1, '0.5–8 km/h'), (8, 18, C2, '8–18 km/h'), (18, 40, C3, '18–40 km/h')]
for lo, hi, c, lab in vcol:
    m = (A[:, 1] >= lo) & (A[:, 1] < hi)
    ax.scatter(A[m, 0] + np.random.default_rng(0).uniform(-0.6, 0.6, m.sum()), A[m, 2], s=22, color=c, alpha=0.75, label=lab, edgecolor='none')
for lo, hi, lab in BINS:
    m = (A[:, 0] >= lo) & (A[:, 0] < hi)
    if m.sum() >= 2:
        x = (lo + min(hi, 100)) / 2 if lo > 0 else 0
        ax.plot([lo if lo > 0 else -1, min(hi, 100)], [np.median(A[m, 2])] * 2, color=INK, lw=2.2)
        ax.text(x, np.median(A[m, 2]) + 0.07, f'{np.median(A[m, 2]):+.2f}\nn={m.sum()}', ha='center', fontsize=7.5, color=INK)
ax.axhline(0, color=INK2, lw=0.8)
ax.set_xlim(-2, 102); ax.set_ylim(-1.3, 1.25)
ax.set_xlabel('유지한 가속 명령 [%] (±4 %, ≥ 0.8 s, 브레이크 0)'); ax.set_ylabel('가속도 [m/s²]')
ax.set_title('④ 이미 굴러가는 중(> 0.5 km/h) — 몇 % 부터 구동이 걸리나 (점 = 유지 구간 1개, 검은 선 = 구간 중앙값)',
             loc='left', fontsize=11, fontweight='bold', color=INK)
ax.legend(loc='lower right', fontsize=9, title='구간 평균 속도', title_fontsize=9)

# ⑤ 모델: 100 % 로 T 초 → L % 로 내림
ax = fig.add_subplot(gs[3, 0])


def med(lo, hi):
    m = (A[:, 0] >= lo) & (A[:, 0] < hi)
    return float(np.median(A[m, 2])) if m.sum() else np.nan


lowv = (A[:, 1] < 8) & (A[:, 0] >= 95)
a100 = float(np.median(A[lowv, 2]))
lag100 = float(np.median([a['lag_from_75pct_s'] for a in lagrows]))
T = 1.5
levels = [('≤ 50 %', med(1, 50), MUTED), ('55–60 %', med(55, 60), C4), ('65–75 %', med(65, 75), C2), ('60–65 %', med(60, 65), C3)]
t = np.arange(0, 12, 0.02)
for lab, aL, c in levels:
    v = np.zeros_like(t)
    for k in range(1, len(t)):
        if t[k] < lag100:
            a = 0.0
        elif t[k] < T + LAG:
            a = a100
        else:
            a = aL
        v[k] = max(0.0, v[k - 1] + a * 0.02)
    stop = t[(t > T + LAG) & (v <= 0)]
    ax.plot(t, v * 3.6, color=c, lw=2, label=f'{lab}: {aL:+.2f} m/s² → ' + (f'{stop[0]:.1f} s 에 정지' if len(stop) else '유지'))
ax.axvspan(0, T, color=C1, alpha=0.08, lw=0)
ax.text(T / 2, 0.97, f'100 % {T:g} s', transform=ax.get_xaxis_transform(), ha='center', va='top', fontsize=9, color=C1)
ax.set_xlim(0, 12); ax.set_ylim(0, None)
ax.set_xlabel('시간 [s]'); ax.set_ylabel('차속 [km/h] ★')
ax.set_title(f'⑤ 모델 ★ — 100 % 로 {T:g} s 출발 후 % 를 내리면', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.legend(fontsize=8.5, loc='upper right', title=f'내린 뒤 %  (출발 지연 {lag100:.2f} s, 100 % 저속 {a100:+.2f} m/s²)', title_fontsize=8.5)

# ⑥ 표본이 있는 곳 (가속 % × 속도)
ax = fig.add_subplot(gs[3, 1])
xb = np.arange(0, 105, 5); yb = [0.5, 3, 8, 13, 18, 25, 40]
H = np.zeros((len(yb) - 1, len(xb) - 1))
for s in segs:
    xi = min(int(s['acc_pct'] // 5), len(xb) - 2); yi = np.searchsorted(yb, s['v_mean_kph']) - 1
    if 0 <= yi < len(yb) - 1:
        H[yi, xi] += s['hold_s']
im = ax.imshow(np.where(H > 0, H, np.nan), origin='lower', aspect='auto', cmap='Blues', extent=[0, 100, 0, len(yb) - 1], vmin=0, vmax=30)
ax.set_yticks(np.arange(len(yb) - 1) + 0.5); ax.set_yticklabels([f'{a:g}–{b:g}' for a, b in zip(yb[:-1], yb[1:])])
ax.set_xlabel('가속 명령 [%]'); ax.set_ylabel('차속 [km/h]'); ax.grid(False)
for yi in range(H.shape[0]):
    for xi in range(H.shape[1]):
        if H[yi, xi] > 0:
            ax.text(xb[xi] + 2.5, yi + 0.5, f'{H[yi, xi]:.0f}', ha='center', va='center', fontsize=7, color=INK if H[yi, xi] < 18 else 'white')
ax.axvspan(50, 80, ymax=2 / 6, color=C2, alpha=0.12, lw=0)
ax.text(65, 0.95, '저속 × 50–80 %\n데이터 없음', ha='center', fontsize=8.5, color=C2)
ax.set_title('⑥ 유지 구간 표본 시간 [s] — 빈 칸 = 아직 모름', loc='left', fontsize=11, fontweight='bold', color=INK)

# ⑦ 결론 표
ax = fig.add_subplot(gs[4, :]); ax.axis('off')
ax.set_title('⑦ 결론 (잠정) — 출발 민감도 · 유지 % · 다음 시험', loc='left', fontsize=11, fontweight='bold', color=INK)
rows = [
    ['항목', '값', '근거 · 한계'],
    ['토크 시작 %', '약 50–60 %  (타 팀 48–60 % 와 맞음)', '정지: 60·70 % 에서 바퀴 1 rpm 움찔, 40 % 이하 없음 / 주행 중: 55–60 % 에서 감속이 타행보다 줄어듦'],
    ['정지 출발 문턱 (평지)', '70 % 초과 ~ 80 % 이하', '빠른 시험 1회씩: 70 % 5 s 실패, 80 % 성공(0.8 s). 원격도 75 % 미만으로는 출발 0회. 경사·노면 따라 달라짐'],
    ['출발 뒤 내려도 되는 %', '60–85 % ≈ 속도 유지, 55 % 이하 ≈ 타행 감속', f'유지 구간 {len(segs)}개(8–25 km/h 위주). 1–50 % {med(1, 50):+.2f}(타행보다 셈 — 원인 미확인), 60–65 % {med(60, 65):+.2f} m/s²'],
    ['"얼마나 유지해야"의 답', '정해진 시간 없음 — 바퀴가 구르기 시작하면(≈ 0.6 s) 60 % 이상으로 유지 ★',
     '정지 마찰만 넘으면 이후는 내린 % 의 가속도(⑤)가 결정. 0.5–8 km/h × 50–80 % 표본 없음(⑥) → 저속 재정지 미확인'],
    ['제어기 반영 제안', '출발 = 100 % → 0x201 > 0.3 km/h 확인 → 피드백', '정지 중 50–75 % 는 안 움직이고 적분만 쌓임. 주행 중 accel_map 0 점 후보 ≈ 60 %(현재 75 %)'],
    ['다음 시험 (03(a) 추가)', '정지 계단 50~75 %(5 % 간격) × 10 s + 저속 내림 시험', '100 % 1.0/1.5/2.0 s → 50/55/60/65/70 % 로 내림 × 8 s(평지, 같은 방향 2회). 경사 반대 방향으로 한 번 더'],
]
tb = ax.table(cellText=[r[1:] for r in rows[1:]], colLabels=rows[0][1:], rowLabels=[r[0] for r in rows[1:]], loc='upper left',
              cellLoc='left', rowLoc='left', colWidths=[0.40, 0.60], bbox=[0.17, 0, 0.83, 1])
tb.auto_set_font_size(False); tb.set_fontsize(8.8)
for (r, c), cell in tb.get_celld().items():
    cell.set_edgecolor(GRID); cell.set_linewidth(0.6)
    cell.set_facecolor(BAND if r == 0 or c == -1 else '#fcfcfb')
    cell.get_text().set_color(INK if r > 0 else INK2)
    if r == 0 or c == 0:
        cell.get_text().set_fontweight('bold')
fig.text(0.04, 0.008, '근거 CSV: launch_attempts.csv · launch_hold_segments.csv · launch_rolling_bins.csv  |  원본: raw_can/*.zst  |  '
         '★ = 실측 중앙값으로 만든 모델  |  스크립트: tools/drive_log/cannpz.py → launch_sens.py', fontsize=8, color=MUTED)
os.makedirs(OUT, exist_ok=True)
fig.savefig(os.path.join(OUT, 'sheet5_launch.png'), dpi=100)
print('saved', len(attempts), 'attempts', len(segs), 'segments')
