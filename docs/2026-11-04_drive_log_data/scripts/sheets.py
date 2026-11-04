"""인터페이스 데이터시트 PNG 4장 — tables/*.csv + candump 원본 일부 파형."""
import sys, os, csv, numpy as np
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

HERE = os.path.dirname(os.path.abspath(__file__)); TAB = sys.argv[1]; OUT = sys.argv[2]; os.makedirs(OUT, exist_ok=True)
for f in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc']:
    if os.path.exists(f): fm.fontManager.addfont(f)
plt.rcParams.update({'font.family': 'Noto Sans CJK JP', 'font.size': 10, 'axes.unicode_minus': False,
                     'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e',
                     'axes.grid': True, 'grid.color': '#e6e5e0', 'grid.linewidth': 0.8, 'axes.spines.top': False, 'axes.spines.right': False,
                     'figure.facecolor': '#fcfcfb', 'axes.facecolor': '#fcfcfb', 'legend.frameon': False})
C1, C2, C3 = '#2a78d6', '#eb6834', '#1baf7a'      # 기본 팔레트 1~3번 슬롯(검증된 순서)
INK, INK2, MUTED, GRID = '#0b0b0b', '#52514e', '#8a8984', '#d9d8d2'

# candump 파형용: iface.py 앞부분(읽기·함수) 재사용
src = open(os.path.join(HERE, 'iface.py')).read()
sys.argv = ['iface', os.path.join(HERE, 'tables_unused')]
_OUT = OUT
exec(compile(src[:src.index('# =====================================================================\n# 1. 주기')], 'iface_head', 'exec'))
OUT = _OUT


def rd(name):
    rows = [r for r in csv.reader(l for l in open(os.path.join(TAB, name)) if not l.startswith('#'))]
    h = rows[0]; out = []
    for r in rows[1:]:
        d = {}
        for k, v in zip(h, r):
            try: d[k] = float(v)
            except ValueError: d[k] = v
        out.append(d)
    return out


def header(fig, title, sub):
    fig.text(0.04, 0.975, title, fontsize=17, fontweight='bold', color=INK, va='top')
    fig.text(0.04, 0.948, sub, fontsize=9.5, color=INK2, va='top')


def footer(fig, text):
    fig.text(0.04, 0.012, text, fontsize=8, color=MUTED, va='bottom')


def table(ax, rows, col_w=None, title=None):
    ax.axis('off')
    if title: ax.set_title(title, loc='left', fontsize=11, fontweight='bold', color=INK)
    t = ax.table(cellText=[r[1:] for r in rows[1:]], colLabels=rows[0][1:], rowLabels=[r[0] for r in rows[1:]], loc='upper left',
                 cellLoc='left', rowLoc='left', colWidths=col_w, bbox=[0.28, 0, 0.72, 1])
    t.auto_set_font_size(False); t.set_fontsize(9)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID); cell.set_linewidth(0.6)
        cell.set_facecolor('#f1f0ec' if r == 0 or c == -1 else '#fcfcfb')
        cell.get_text().set_color(INK if r > 0 else INK2)
        if r == 0: cell.get_text().set_fontweight('bold')
    return t


def ktable(ax, title, items):
    """키-값 표: items = [(항목, 값, 근거)]"""
    ax.axis('off'); ax.set_title(title, loc='left', fontsize=11, fontweight='bold', color=INK)
    t = ax.table(cellText=[[k, v, n] for k, v, n in items], colLabels=['항목', '값', '조건·근거'],
                 cellLoc='left', bbox=[0, 0, 1, 1], colWidths=[0.17, 0.25, 0.58])
    t.auto_set_font_size(False); t.set_fontsize(8.6)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID); cell.set_linewidth(0.6)
        cell.set_facecolor('#f1f0ec' if r == 0 or c == 0 else '#fcfcfb')
        if c == 1 and r > 0: cell.get_text().set_fontweight('bold')
        if r == 0: cell.get_text().set_fontweight('bold')
        cell.get_text().set_color(INK if c != 2 else INK2)


med = lambda xs: float(np.nanmedian(xs)) if len(xs) else float('nan')
SUB = '2026-11-04 용인 트랙 · Kvaser 원본(candump) 시각 기준 · 원격 주행(오전) + 자율 주행 5세션(오후) + 오후 빠른 시험 · 잠정값'

# =========================== 1. 조향 ===========================
pts = np.loadtxt(os.path.join(TAB, 'steer_ratio_points.csv'), delimiter=',')
rb = rd('steer_ratio_gnss.csv'); fit = {r['item']: r for r in rd('steer_ratio_fit.csv')}
steps = rd('steer_steps.csv'); trk = rd('steer_tracking.csv'); rate = rd('steer_rate.csv'); stat = rd('steer_static.csv')
fa = fit['fit |wheel|<40: wheel = a*tire + b']; fl = fit['fit left (wheel>3)']; fr = fit['fit right (wheel<-3)']

fig = plt.figure(figsize=(12, 15), dpi=130)
header(fig, '조향 인터페이스 데이터시트 — 0x210 steer_command → 0x200 steer_postion → 실제 타이어각', SUB)
gs = fig.add_gridspec(3, 2, left=0.07, right=0.97, top=0.92, bottom=0.05, hspace=0.38, wspace=0.25, height_ratios=[1.15, 1, 1.05])

ax = fig.add_subplot(gs[0, :])
ax.scatter(pts[:, 0], pts[:, 1], s=4, color='#c9c8c2', label='GNSS 요레이트 기반 유효 타이어각(표본)', rasterized=True)
w = np.array([r['wheel_mean_deg'] for r in rb]); ti = np.array([r['tire_mean_deg'] for r in rb])
lo = np.array([r['tire_p10'] for r in rb]); hi = np.array([r['tire_p90'] for r in rb])
ax.errorbar(w, ti, yerr=[ti - lo, hi - ti], fmt='o', ms=6, color=C1, ecolor=C1, elinewidth=1.5, capsize=3, label='구간 평균 (막대 = p10~p90)')
xx = np.linspace(-150, 150, 3)
ax.plot(xx, xx / 7.5, '--', color=C2, lw=2, label='현재 steer_map (핸들 150° = 타이어 20°, 비 7.5)')
xl = np.linspace(-150, -3, 2); ax.plot(xl, (xl - fr['intercept_wheel_deg']) / fr['slope_wheel_per_tire'], color=C3, lw=2, label=f"회귀 우측: 핸들 = {fr['slope_wheel_per_tire']:.1f}·타이어 {fr['intercept_wheel_deg']:+.1f}")
xr = np.linspace(3, 150, 2); ax.plot(xr, (xr - fl['intercept_wheel_deg']) / fl['slope_wheel_per_tire'], color=C3, lw=2, ls=':', label=f"회귀 좌측: 핸들 = {fl['slope_wheel_per_tire']:.1f}·타이어 {fl['intercept_wheel_deg']:+.1f}")
ax.axhline(0, color=GRID, lw=1); ax.axvline(0, color=GRID, lw=1)
ax.annotate(f"직진(타이어 0)일 때 핸들 ≈ {fa['intercept_wheel_deg']:+.1f}°\n핸들 0 일 때 타이어 ≈ {-fa['intercept_wheel_deg'] / fa['slope_wheel_per_tire']:+.1f}° (좌로 쏠림)",
            xy=(fa['intercept_wheel_deg'], 0), xytext=(-140, 9), fontsize=9.5, color=INK, arrowprops=dict(arrowstyle='->', color=INK2))
ax.set_xlabel('0x200 핸들각 [핸들 °]  (+ = 좌)'); ax.set_ylabel('유효 타이어각 δ = atan(L·r / v) [타이어 °]')
ax.set_title('① 핸들각 → 실제 타이어각 (조향비·오프셋, 자율 5세션, RTK, v > 5.4 km/h, 준정상, 횡가속 < 2 m/s²)', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.set_xlim(-155, 155); ax.set_ylim(-22, 22); ax.legend(loc='lower right', fontsize=8.5)

# ② 계단 응답 파형(빠른 시험 s3, 정지 상태)
ax = fig.add_subplot(gs[1, 0])
t0, s0, t1, s1, t2, s2 = frames('quick_QUICK-STEER-LON_s3')
cands = [r for r in steps if r['source'] == 'quick_QUICK-STEER-LON_s3']
for r, col, lab in [(cands[0], C1, '0 → +30'), (cands[2], C2, '−30 → +150'), (cands[4], C3, '−150 → 0')]:
    ts = r['t_unix']; m2 = (t2 >= ts - 0.2) & (t2 <= ts + 1.4); m0 = (t0 >= ts - 0.2) & (t0 <= ts + 1.4)
    ax.step(t2[m2] - ts, s2['steer_command'][m2], where='post', color=col, lw=1.2, ls='--', alpha=0.9)
    ax.plot(t0[m0] - ts, s0['steer_postion'][m0], color=col, lw=2, label=f'{lab} (명령 점선 / 위치 실선)')
ax.set_xlabel('계단 후 시간 [s]'); ax.set_ylabel('핸들 °'); ax.legend(fontsize=8, loc='lower right')
ax.set_title('② 계단 응답 예 (정지, lift_cmd 직접 %, 11-04 15:37)', loc='left', fontsize=11, fontweight='bold', color=INK)

# ③ 연속 추종(자율 D 일부)
ax = fig.add_subplot(gs[1, 1])
t0, s0, t1, s1, t2, s2 = frames('auto_bsw5')
ta = 1793779400; m2 = (t2 >= ta) & (t2 <= ta + 20); m0 = (t0 >= ta) & (t0 <= ta + 20)
ax.plot(t2[m2] - ta, s2['steer_command'][m2], color=C1, lw=2, label='0x210 명령 (can_guard)')
ax.plot(t0[m0] - ta, s0['steer_postion'][m0], color=C2, lw=2, label='0x200 위치')
ax.set_xlabel('시간 [s] (17:03:20 부터, 랩 2회차 약 15~20 km/h)'); ax.set_ylabel('핸들 °'); ax.legend(fontsize=8.5)
ax.set_title('③ 주행 중 연속 추종 — 위치가 계단식(불감대)', loc='left', fontsize=11, fontweight='bold', color=INK)

pos_dead = [r['dead_ms(>2deg)'] for r in steps if r['dead_ms(>2deg)'] == r['dead_ms(>2deg)'] and r['dead_ms(>2deg)'] > 0]
rates = [r['rate_deg_s(10-90)'] for r in steps if r['rate_deg_s(10-90)'] == r['rate_deg_s(10-90)']]
t90s = [r['t90_ms'] for r in steps if r['t90_ms'] == r['t90_ms']]
ovs = [r['overshoot_deg'] for r in steps]
tr = {(r['source'], r['speed_kph']): r for r in trk}
rt = {r['source']: r for r in rate}
st0 = [r for r in stat if r['cmd_from_deg'] == -0.5][0]
db_ = [r for r in rd('steer_deadband_driving.csv') if r['source'] == 'auto_bsw5'][0]
ax = fig.add_subplot(gs[2, :])
ktable(ax, '④ 조향 인터페이스 수치 (어댑터·can_guard 파라미터 근거)', [
    ('부호', '+ 핸들 = 좌회전 (확인)', f"핸들각과 INS 요레이트 상관 {fit['corr wheel vs tire']['slope_wheel_per_tire']:.3f}(+) → steer_sign = +1 확인"),
    ('직진 핸들각(오프셋)', f"{fa['intercept_wheel_deg']:+.1f} 핸들 ° ≈ {-fa['intercept_wheel_deg'] / fa['slope_wheel_per_tire']:+.1f} 타이어 °", '핸들 0 이면 좌로 감 → steer_map 0 점을 이만큼 옮기거나 01 LAT-ALIGN 보정'),
    ('유효 조향비 (오프셋 제거)', f"우 {fr['slope_wheel_per_tire']:.1f} / 좌 {fl['slope_wheel_per_tire']:.1f} (표 7.5)", '큰 각(|핸들| 60~150°) 구간 비 8.6~14 → 실제 타이어각이 표의 55~85 %. 미끄럼·언더스티어 포함'),
    ('주행 중 불감대', f"약 ±{db_['p50']:.0f} 핸들 ° (≈ ±{db_['p50'] / 11:.2f} 타이어 °)", f"위치가 {db_['frac_time_still'] * 100:.0f} % 시간 정지, |명령−위치| 중앙값 {db_['p50']:.1f}°(p75 {db_['p75']:.1f}°)에서 한 번에 이동(③ 계단 모양)"),
    ('정적 정확도', f"오차 평균 −1.3~+1.1°, p5~p95 ±2°", '명령 1 s 유지 뒤 0x200 − 0x210 (steer_static.csv). 명령 0 일 때 위치 평균 %.1f°' % st0['pos_mean_wheel_deg']),
    ('무반응 시간(계단)', f"중앙값 {med(pos_dead):.0f} ms", '0x210 계단 → 0x200 이 2° 움직일 때까지 (n=%d, 원격+빠른시험)' % len(pos_dead)),
    ('계단 90 % 도달', f"중앙값 {med(t90s):.0f} ms", '20~300° 계단. 오버슈트 중앙값 %.1f°' % med(ovs)),
    ('최대 조향 속도', f"계단 10~90 % {med(rates):.0f} °/s (최대 {np.nanmax(rates):.0f})", f"0x200 미분 p99.9 = {rt['remote']['p99.9']:.0f} °/s(원격). 어댑터 제한 120 °/s 는 액추에이터 능력의 약 1/2"),
    ('연속 추종 지연', f"{tr[('auto_bsw5', '10-40')]['best_lag_ms']:.0f} ms, rms {tr[('auto_bsw5', '10-40')]['rms_at_lag_deg']:.1f}°", '자율 10~40 km/h (bsw5). bsw3 130 ms / 2.4°. 저속 0.5~10 km/h 150~240 ms'),
])
footer(fig, '근거 CSV: steer_ratio_gnss.csv · steer_ratio_fit.csv · steer_ratio_points.csv · steer_static.csv · steer_steps.csv · steer_tracking.csv · steer_rate.csv · steer_deadband_driving.csv  |  L = 2.36 m, INS 요레이트·속도')
fig.savefig(os.path.join(OUT, 'sheet1_steer.png')); plt.close(fig); print('sheet1')

# =========================== 2. 가속·타행 ===========================
am = rd('long_accel_map.csv'); coast = rd('long_coast.csv'); launch = rd('long_launch.csv')
fig = plt.figure(figsize=(12, 15), dpi=130)
header(fig, '가속 인터페이스 데이터시트 — 0x210 acc_command % → 종가속', SUB)
gs = fig.add_gridspec(3, 2, left=0.07, right=0.97, top=0.92, bottom=0.05, hspace=0.38, wspace=0.25, height_ratios=[1.15, 1, 1.05])
ax = fig.add_subplot(gs[0, :])
mid = lambda s: (lambda a, b: (a + b) / 2)(*map(float, s.split('-')))
bands = [('0-10 km/h', lambda v: v < 10, C1), ('10-20 km/h', lambda v: 10 <= v < 20, C2), ('20 km/h 이상', lambda v: v >= 20, C3)]
for lab, f, col in bands:
    agg = {}
    for r in am:
        vlo = float(r['speed_kph(true)'].split('-')[0])
        if r['speed_kph(true)'] == '0-1' or not f(vlo + 0.01): continue
        x = mid(r['acc_pct']) if r['acc_pct'] != '0-1' else 0.0
        agg.setdefault(x, []).append((r['n(0.05s)'], r['a_p50_mps2']))
    xs = sorted(agg); ys = [sum(n * a for n, a in agg[x]) / sum(n for n, _ in agg[x]) for x in xs]
    ax.plot(xs, ys, '-o', color=col, lw=2, ms=7, label=f'실측 {lab} (구간 중앙값, 출처 가중 평균)')
ax.plot([75, 80, 100], [0.1, 0.13, 0.57], '--', color=INK2, lw=2, label='현재 accel_map (75 %→0.10, 80→0.13, 100→0.57)')
ax.axhline(0, color=INK2, lw=0.8); ax.axvspan(0, 75, color='#f1f0ec', zorder=0)
ax.text(37, -0.93, '75 % 미만: 구동 없음(타행과 같음)', fontsize=9.5, color=INK2, ha='center')
ax.set_xlabel('가속 명령 [%] (0.5 s 전 값, 1 s 유지)'); ax.set_ylabel('종가속 [m/s²]'); ax.set_xlim(-2, 102); ax.set_ylim(-1.0, 1.0)
ax.legend(loc='upper left', fontsize=8.5)
ax.set_title('① 가속 % → 가속도 (속도대별, 원격 0x201÷0.958 · 자율 GNSS · 빠른시험)', loc='left', fontsize=11, fontweight='bold', color=INK)

ax = fig.add_subplot(gs[1, 0])
for grp, col in [('remote', C1), ('auto', C2)]:
    rr = [r for r in coast if r['group'] == grp]
    ax.plot([mid(r['speed_kph']) for r in rr], [r['a_p50_mps2'] for r in rr], 'o', color=col, ms=8, label=f'{"원격" if grp == "remote" else "자율"} (중앙값)')
    for r in rr: ax.plot([mid(r['speed_kph'])] * 2, [r['a_p25'], r['a_p75']], color=col, lw=1.5)
cf = [r for r in coast if r['group'] == 'fit all'][0]
allc = [r['a_p50_mps2'] for r in coast if r['group'] in ('remote', 'auto')]; cmed = float(np.median(allc))
ax.axhline(cmed, color=INK2, lw=1.5, ls='--', label=f"전체 구간 중앙값 {cmed:.2f} m/s² (속도 의존성 불명확)")
ax.axhline(0, color=INK2, lw=0.8); ax.set_ylim(-1.1, 0.1)
ax.set_xlabel('속도 [km/h]'); ax.set_ylabel('타행 가속도 [m/s²]'); ax.legend(fontsize=8.5, loc='lower right')
ax.set_title('② 타행 감속 (가속 0 · 브레이크 0, 1 s 유지)', loc='left', fontsize=11, fontweight='bold', color=INK)

ax = fig.add_subplot(gs[1, 1])
ld = [(r['source'], r['first_move_ms(0x201>0)']) for r in launch]
grp = [('원격', [d for s, d in ld if s == 'remote'], C1), ('자율·어댑터', [d for s, d in ld if s != 'remote'], C2)]
for i, (lab, ds, col) in enumerate(grp):
    y = np.full(len(ds), i) + np.random.default_rng(1).uniform(-0.15, 0.15, len(ds))
    ax.scatter(ds, y, s=40, color=col, edgecolor='#fcfcfb', linewidth=1.5, zorder=3)
    ax.plot([np.median(ds)] * 2, [i - 0.3, i + 0.3], color=INK, lw=2)
    ax.text(np.median(ds) + 15, i + 0.3, f'중앙값 {np.median(ds):.0f} ms (n={len(ds)})', fontsize=9, color=INK, va='bottom')
ax.set_yticks([0, 1]); ax.set_yticklabels([g[0] for g in grp]); ax.set_ylim(-0.6, 1.8)
ax.set_xlabel('가속 ≥ 75 % 명령 → 0x201 첫 움직임 [ms]')
ax.set_title('③ 정지 출발 지연', loc='left', fontsize=11, fontweight='bold', color=INK)

def pick(grp, acc, spd):
    r = [x for x in am if x['group'] == grp and x['acc_pct'] == acc and x['speed_kph(true)'] == spd]
    return r[0]['a_p50_mps2'] if r else float('nan')
ax = fig.add_subplot(gs[2, :])
lr = [d for s, d in ld if s == 'remote']; la = [d for s, d in ld if s != 'remote']
ktable(ax, '④ 가속 인터페이스 수치 (accel_map · long_deadband · 피드포워드 근거)', [
    ('불감대', '75 % 미만 = 구동 없음', '0~75 % 구간 가속도 = 타행과 같음(원격·자율 공통). 80 % 부근부터 구동'),
    ('평형(속도 유지) 명령', '80~85 % (15~25 km/h)', '자율: 75~85 % 에서 15~25 km/h 가속도 ≈ 0.00 m/s²'),
    ('100 % 가속도', f"{pick('remote', '95-101', '5-10'):+.2f} → {pick('remote', '95-101', '15-20'):+.2f} → {pick('remote', '95-101', '25-35'):+.2f} m/s²", f"원격 5~10 → 15~20 → 25~35 km/h. 자율 GNSS {pick('auto', '95-101', '5-10'):+.2f}~{pick('auto', '95-101', '15-20'):+.2f} (5~20 km/h). 속도 높을수록 감소"),
    ('타행 감속', f"약 {cmed:+.2f} m/s²", '가속·브레이크 0. 구간 중앙값 −0.08~−0.88(경사 미보정) → 상수 피드포워드부터'),
    ('출발 지연', f"원격 {np.median(lr):.0f} ms / 자율 {np.median(la):.0f} ms", '가속 ≥ 75 % → 0x201 > 0 (0x201 분해능 0.1 km/h). 11-04 오후 표: 0.64~0.74 s(어댑터 경유)'),
    ('제어기 설정 대비', 'a_max +0.6 은 20 km/h 이상에서 달성 불가', '100 % 로도 20 km/h 이상 +0.1~0.5 → follower 가속 목표가 상한에 붙음(A 41 % 시간)'),
])
footer(fig, '근거 CSV: long_accel_map.csv · long_coast.csv · long_launch.csv   |   가속도 = 속도 미분(자율 INS 속도 1.25 s 평활, 원격 0x201÷0.958 0.6 s 평활), 경사·바람 미보정 → 잠정')
fig.savefig(os.path.join(OUT, 'sheet2_accel.png')); plt.close(fig); print('sheet2')

# =========================== 3. 브레이크 ===========================
bm = rd('long_brake_map.csv'); bsteps = rd('brake_steps.csv'); bpos = rd('brake_position_by_session.csv')
fig = plt.figure(figsize=(12, 15), dpi=130)
header(fig, '브레이크 인터페이스 데이터시트 — 0x210 break_command % → 감속 · 0x200 break_postion', SUB)
gs = fig.add_gridspec(3, 2, left=0.07, right=0.97, top=0.92, bottom=0.05, hspace=0.38, wspace=0.28, height_ratios=[1.1, 1, 1.1])
ax = fig.add_subplot(gs[0, 0])
for lab, sp, col in [('2-10 km/h', '2-10', C1), ('10-20 km/h', '10-20', C2), ('20-35 km/h', '20-35', C3)]:
    rr = [r for r in bm if r['group'] == 'all' and r['speed_kph(true)'] == sp]
    ax.plot([mid(r['brake_pct']) for r in rr], [-r['a_p50_mps2'] for r in rr], 'o', color=col, ms=8, label=f'실측 {lab}')
    for r in rr: ax.plot([mid(r['brake_pct'])] * 2, [-r['a_p25'], -r['a_p75']], color=col, lw=1.5)
ax.plot([0, 40, 70, 90, 100], [0.1, 1.0, 2.0, 3.0, 4.0], '--', color=INK2, lw=2, label='현재 brake_map')
ax.set_xlabel('브레이크 명령 [%] (0.3 s 전 값, 0.5 s 유지)'); ax.set_ylabel('감속 [m/s²] (타행 포함)'); ax.set_xlim(-2, 102); ax.set_ylim(0, 4.3)
ax.legend(fontsize=8.5, loc='upper left')
ax.set_title('① 브레이크 % → 감속', loc='left', fontsize=11, fontweight='bold', color=INK)

ax = fig.add_subplot(gs[0, 1])
rr = [r for r in bpos if r['cmd_pct'] == '95-101' and r['state'] == 'stopped'] + [r for r in bpos if r['cmd_pct'] == '81.5-95' and r['state'] == 'stopped']
names = [r['source'].replace('quick_', '').replace('auto_', '자율 ') + f" ({r['cmd_mean']:.0f}%)" for r in rr]
yy = np.arange(len(rr))
ax.barh(yy, [r['pos_mean'] for r in rr], color=C1, height=0.6)
for y, r in zip(yy, rr): ax.text(r['pos_mean'] + 0.1, y, f"{r['pos_mean']:.1f}", va='center', fontsize=8.5, color=INK)
ax.axvline(8.2, color=INK2, ls='--', lw=1.2); ax.text(8.25, len(rr) - 0.5, '기대 8.2', fontsize=8.5, color=INK2)
ax.set_yticks(yy); ax.set_yticklabels(names, fontsize=8); ax.set_xlim(0, 9.5); ax.invert_yaxis()
ax.set_xlabel('0x200 break_postion (정지, 1.5 s 유지)')
ax.set_title('② 높은 명령에서 위치 포화값 (세션별)', loc='left', fontsize=11, fontweight='bold', color=INK)

ax = fig.add_subplot(gs[1, :])
t0, s0, t1, s1, t2, s2 = frames('quick_QUICK-LON_s6')
rel = [r for r in bsteps if r['source'] == 'quick_QUICK-LON_s6']
for r, col, lab in [(rel[0], C1, '100 → 0 % (해제)'), (rel[2], C2, '0 → 100 % (적용, 3 km/h)')]:
    ts = r['t_unix']; m2 = (t2 >= ts - 0.3) & (t2 <= ts + 2.0); m0 = (t0 >= ts - 0.3) & (t0 <= ts + 2.0)
    ax.step(t2[m2] - ts, s2['break_command'][m2] / 10, where='post', color=col, ls='--', lw=1.3)
    ax.plot(t0[m0] - ts, s0['break_postion'][m0], color=col, lw=2.2, label=f'{lab}: 명령÷10 점선 / 위치 실선')
ax.set_xlabel('계단 후 시간 [s]'); ax.set_ylabel('브레이크 (명령 % ÷ 10, 위치 0~10)'); ax.legend(fontsize=9, loc='center right')
ax.set_title('③ 계단 응답 예 (QUICK-LON_s6, 11-04 15:4x)', loc='left', fontsize=11, fontweight='bold', color=INK)

app = [r for r in bsteps if r['cmd_to'] > r['cmd_from']]; rls = [r for r in bsteps if r['cmd_to'] < r['cmd_from'] and r['cmd_to'] == 0]
g = lambda rows, k: [r[k] for r in rows if r[k] == r[k]]
pick_b = lambda b, s: [r for r in bm if r['group'] == 'all' and r['brake_pct'] == b and r['speed_kph(true)'] == s]
ax = fig.add_subplot(gs[2, :])
ktable(ax, '④ 브레이크 인터페이스 수치 (brake_map · 정지 유지 · ESTOP 램프 근거)', [
    ('적용 무반응', f"중앙값 {med(g(app, 'dead_ms(>0.2)')):.0f} ms (n={len(g(app, 'dead_ms(>0.2)'))})", '0x210 증가 계단 → 0x200 위치 +0.2. 10-07 리프트 56~112 ms 와 일치'),
    ('해제 무반응 / 90 %', f"{med(g(rls, 'dead_ms(>0.2)')):.0f} ms / {med(g(rls, 't90_ms')):.0f} ms", f'→ 0 % 계단(n={len(rls)}). 해제가 적용보다 약 3배 느리다 → 출발 지연의 일부'),
    ('적용 90 % 도달', f"{med(g(app, 't90_ms')):.0f} ms", '100 % 적용은 위치가 기대 8.2 에 못 미쳐 90 % 미도달 사례 다수(nan)'),
    ('감속: 10~50 %', '0.25~0.87 m/s² (타행 포함)', '자율 속도 제어 구간(10~20 km/h). 현재 표는 40 % = 1.0, 거의 맞음'),
    ('감속: 50~60 %', f"{-pick_b('50-60', '2-10')[0]['a_p50_mps2']:.2f} m/s²", '현재 표 55 % ≈ 1.5 → 실제 더 셈(11-04 오후 ADAPTER-LON_s2 1.92 와 같음)'),
    ('감속: 95~100 %', f"{-pick_b('95-101', '10-20')[0]['a_p50_mps2']:.2f}~{-pick_b('95-101', '20-35')[0]['a_p50_mps2']:.2f} m/s²", '원격 주행. 현재 표 100 % = 4.0 → 실측 약 3.0. 휠락 없이 약 3 m/s² 가 상한'),
    ('위치 포화값', '정지·≥ 90 % 에서 5.0~8.3 (세션별)', '명령 ÷ 10 관계는 82 % 이하에서만 성립. 위치로 제동력 판단 금지(압력 신호 없음)'),
])
footer(fig, '근거 CSV: long_brake_map.csv · brake_steps.csv · brake_position_static.csv · brake_position_by_session.csv   |   앱 원격 개입 구간(0x156 EPS_En/ACC_En = 1 ± 2 s) 제외')
fig.savefig(os.path.join(OUT, 'sheet3_brake.png')); plt.close(fig); print('sheet3')

# =========================== 4. 타이밍·센서 ===========================
ti = rd('timing_can_ids.csv'); tx = rd('timing_canguard_tx.csv'); tros = rd('timing_ros.csv'); ws = rd('wheel_speed_vs_gnss.csv')
fig = plt.figure(figsize=(12, 15), dpi=130)
header(fig, '타이밍·센서 데이터시트 — CAN 주기, can_guard 송신, ROS 경로, 0x201 바퀴 속도', SUB)
gs = fig.add_gridspec(3, 2, left=0.07, right=0.97, top=0.92, bottom=0.05, hspace=0.38, wspace=0.28, height_ratios=[1, 1, 1.15])
ax = fig.add_subplot(gs[0, 0])
t2 = CAN['auto_bsw5'][0x210][0]; d = np.diff(t2) * 1000
ax.hist(np.clip(d, 15, 25), bins=np.arange(15, 25.1, 0.25), color=C1, edgecolor='#fcfcfb', linewidth=0.8)
ax.set_yscale('log'); ax.set_xlabel('0x210 송신 간격 [ms] (15 미만은 15 칸에 모음 — 늦은 프레임 뒤 따라잡기)'); ax.set_ylabel('프레임 수 (로그)')
ax.set_title(f'① can_guard 0x210 간격 (bsw5, n={len(t2)})', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.text(0.98, 0.95, f'중앙값 {np.median(d):.2f} ms\np99 {np.percentile(d, 99):.2f} ms\n최대 {d.max():.1f} ms', transform=ax.transAxes, ha='right', va='top', fontsize=9.5, color=INK)

ax = fig.add_subplot(gs[0, 1])
for s, col in [('auto_bsw3', C1), ('auto_bsw4', C2), ('auto_bsw5', C3)]:
    rr = [r for r in ws if r['source'] == s]
    ax.plot([mid(r['gnss_speed_kph']) for r in rr], [r['ratio_avg'] for r in rr], '-o', color=col, lw=2, ms=7, label=s.replace('auto_', '자율 '))
ax.axhline(0.958, color=INK2, ls='--', lw=1.2); ax.text(23, 0.9595, '0.958', fontsize=9, color=INK2)
ax.set_ylim(0.93, 0.98); ax.set_xlabel('GNSS 속도 [km/h]'); ax.set_ylabel('0x201 좌우 평균 ÷ GNSS'); ax.legend(fontsize=8.5)
ax.set_title('② 바퀴 속도 배율 (0x201 / GNSS)', loc='left', fontsize=11, fontweight='bold', color=INK)

ax = fig.add_subplot(gs[1, :])
rows = [['', 'ID', '이름', '주기 중앙값 ms', 'p99 ms', '최대 ms']]
names = {0x128: 'DBC 밖', 0x156: 'REMOTE_01(앱)', 0x157: 'REMOTE_02(앱)', 0x200: 'control_info', 0x201: 'wheel_info', 0x202: 'DBC 밖', 0x210: 'control_command', 0x710: 'DBC 밖', 0x711: 'DBC 밖', 0x712: 'DBC 밖'}
for r in ti:
    if r['source'] != 'auto_bsw5': continue
    cid = int(r['id'], 16)
    rows.append([r['id'], r['id'], names.get(cid, ''), f"{r['period_med_ms']:.1f}", f"{r['period_p99_ms']:.1f}", f"{r['period_max_ms']:.0f}"])
ax.axis('off'); ax.set_title('③ CAN ID 주기 (자율 bsw5, 17:02~17:12, Kvaser 수신 시각)', loc='left', fontsize=11, fontweight='bold', color=INK)
t = ax.table(cellText=[r[1:] for r in rows[1:]], colLabels=rows[0][1:], cellLoc='left', bbox=[0.0, 0, 1.0, 1])
t.auto_set_font_size(False); t.set_fontsize(9)
for (rr_, c), cell in t.get_celld().items():
    cell.set_edgecolor(GRID); cell.set_linewidth(0.6); cell.set_facecolor('#f1f0ec' if rr_ == 0 or c == -1 else '#fcfcfb')

tr = {(r['session'], r['topic']): r for r in tros}
txr = {r['source']: r for r in tx}
ax = fig.add_subplot(gs[2, :])
ktable(ax, '④ 타이밍·센서 수치 (period_s · stale_timeout · odom_timeout · 속도 보정 근거)', [
    ('can_guard 0x210', f"{txr['auto_bsw5']['med_ms']:.2f} ms, p99.9 {txr['auto_bsw5']['p999_ms']:.1f} ms", f"자율·빠른시험 11세션 40 ms 넘는 공백 1회(bsw3 시작 시 {txr['auto_bsw3']['max_ms'] / 1000:.1f} s, 주행 전)"),
    ('차량 0x200 / 0x201', '21.0 ms (47.6 Hz)', '원격·자율 같음. 0x156/0x157(앱) 10 ms'),
    ('/control/target', f"{tr[('bsw5', '/control/target')]['med_ms']:.0f} ms, 최대 {tr[('bsw5', '/control/target')]['max_ms']:.0f} ms", 'follower 50 Hz. 최대 공백 약 340 ms 가 세션마다 있음(어댑터 stale 150 ms 보다 김)'),
    ('어댑터 입력 나이(arm)', f"p99 {tr[('bsw5', 'adapter input_age (armed)')]['p99_ms']:.0f} ms, 최대 {tr[('bsw5', 'adapter input_age (armed)')]['max_ms']:.0f} ms", f"bsw5. bsw3 은 최대 {tr[('bsw3', 'adapter input_age (armed)')]['max_ms']:.0f} ms 로 stale(150 ms) 초과 2회·writing=false 2회"),
    ('/localization/odom', f"{tr[('bsw5', '/localization/odom')]['med_ms']:.0f} ms, 최대 {tr[('bsw5', '/localization/odom')]['max_ms']:.0f} ms", 'follower odom_timeout 0.2 s 보다 긴 공백이 세션마다 1~4회'),
    ('bag CAN 상태 토픽', '최대 52 s 지연(누적)', 'can_raw_bridge 504 프레임/s < 버스 약 580. 제어 무관, 분석은 헤더 stamp 또는 candump'),
    ('0x201 배율', '0.958 (2~25 km/h 일정)', '거리·속도 ÷ 0.958 로 경로 길이와 1 % 안. 좌우 개별은 0.89~0.99 로 흩어짐(회전 영향)'),
    ('0x201 분해능', '0.1 km/h', '정지 판정·출발 감지 하한'),
])
footer(fig, '근거 CSV: timing_can_ids.csv · timing_canguard_tx.csv · timing_ros.csv · wheel_speed_vs_gnss.csv · canguard_events.csv')
fig.savefig(os.path.join(OUT, 'sheet4_timing.png')); plt.close(fig); print('sheet4')
