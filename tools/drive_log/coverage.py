"""인터페이스 완성도 데이터시트 sheet0_interface_status.png — 항목별 상태표 + CAN 원본 표본 분포(어디가 채워졌고 어디가 비었나).

    python3 tools/drive_log/cannpz.py measurements/2026-11-04_drive_log/raw_can <npz>
    python3 tools/drive_log/coverage.py <npz> measurements/2026-11-04_drive_log/tables measurements/2026-11-04_drive_log/sheets
표본 시간 = 0x210 프레임(20 ms)마다 그때 차속(0x201 ÷ 0.958)·명령·조향 위치로 칸에 더한 것. 앱 원격 시간(±2 s) 제외, 차속 > 0.5 km/h.
상태표의 값은 measurements/2026-11-04_drive_log/README.md 5장·tables/*.csv·launch_rolling_bins.csv 에서 옮겼다(이 스크립트가 계산하지 않음).
"""
import csv, os, sys
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.patches import Patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cannpz import sessions, load, app_mask  # noqa: E402

NPZ, TAB, OUT = sys.argv[1], sys.argv[2], sys.argv[3]

# ───────────────────────── 1. 표본 분포
VB = [0.5, 3, 8, 13, 18, 25, 40]
AX = np.arange(0, 105, 5)
SX = np.arange(-160, 170, 20)
Hacc = np.zeros((len(VB) - 1, len(AX) - 1)); Hbrk = np.zeros_like(Hacc); Hst = np.zeros((len(VB) - 1, len(SX) - 1))
for sess, p in sessions(NPZ).items():
    d = load(p)
    tc = d['tc']
    if len(tc) == 0:
        continue
    v = np.interp(tc, d['tw'], d['v'])
    st = np.interp(tc, d['ts'], d['steer'])
    ok = ~app_mask(d, tc) & (v > 0.5)
    dt = np.clip(np.diff(np.r_[tc, tc[-1] + 0.02]), 0, 0.05)
    vi = np.searchsorted(VB, v) - 1
    ok &= (vi >= 0) & (vi < len(VB) - 1)
    for k in np.where(ok)[0]:
        if d['brk'][k] > 0:
            Hbrk[vi[k], min(int(d['brk'][k] // 5), 19)] += dt[k]
        else:
            Hacc[vi[k], min(int(d['acc'][k] // 5), 19)] += dt[k]
        si = np.searchsorted(SX, st[k]) - 1
        if 0 <= si < len(SX) - 1:
            Hst[vi[k], si] += dt[k]

# ───────────────────────── 2. 상태표 (값 출처: 요약 5장 · tables)
OKC, MID, LOW, NONE = '#1baf7a', '#2a78d6', '#eb6834', '#b5b4ae'
STATUS = {'확정': OKC, '잠정·근거 충분': MID, '잠정·근거 부족': LOW, '미측정': NONE}
R = [  # 영역, 항목, 현재 설정, 11-04 실측, 상태, 표본, 다음
    ('조향', '부호 (+ = 좌)', 'steer_sign +1', '핸들↔요레이트 상관 +0.988', '확정', '자율 5세션', '— ("부호 미확인" 표시 해제 가능)'),
    ('조향', '직진 0 점', '핸들 0°', '핸들 −13.7° (핸들 0 → 타이어 +1.5° 좌)', '잠정·근거 충분', '직선 7,000+ 점', '01: 노면 기울기·맵 오프셋과 분리'),
    ('조향', '핸들→타이어 (우)', '÷ 7.5 (150↔20°)', '유효 조향비 10.9 (큰 각 8.6–14)', '잠정·근거 충분', '11,303 점', '05: 저속 우 최대, GNSS 원'),
    ('조향', '핸들→타이어 (좌)', '÷ 7.5', '유효 조향비 13.6, 측정 최대 +96°', '잠정·근거 부족', '700 점', '05: 좌 최대(+150) 저속'),
    ('조향', '최대 타이어각', '20° (follower)', '우 −10.6° (핸들 −145) / 좌 미측정', '잠정·근거 부족', '114 점', '05 → max_steer_tire_deg'),
    ('조향', '지연 · 속도', '175 ms · 120 °/s', '무반응 107 ms · 추종 130–160 ms · 240 °/s', '확정', '계단 34 · 추종 4세션', '—'),
    ('조향', '불감대', '±1.5° (리프트)', '주행 중 |명령−위치| 4.0° (p50)', '잠정·근거 충분', '자율 4세션', '01 오차 예산에 반영'),
    ('가속', '정지 출발 문턱', 'accel_map 0 점 75 %', '70 % 실패 · 80 % 성공 (평지)', '잠정·근거 부족', '각 1회', '03(a): 50–75 % 5 % 계단 × 10 s'),
    ('가속', '주행 중 구동 시작', '(없음)', '55–60 % 부터 감속 줄고 60 % ≈ 0', '잠정·근거 부족', '유지 구간 10개', '03(a): 내림 시험'),
    ('가속', '% → m/s² (80–100 %)', '75/80/100 → 0.10/0.13/0.57', '100 %: +0.67(5–10) → +0.50(15–20) → +0.09(25–35)', '잠정·근거 충분', '유지 구간 150+', '속도 × % 2차원 표로'),
    ('가속', '% → m/s² (저속 · 50–80 %)', '—', '표본 없음', '미측정', '0', '03(a)'),
    ('가속', '타행 감속', '(없음)', '−0.3 ~ −0.6 m/s² (경사 미보정)', '잠정·근거 충분', '유지 구간 93', '평지 왕복으로 경사 상쇄'),
    ('가속', '출발 지연', '0.64–0.74 s', '브레이크 직후 625 ms · 오래전 해제 530 ms', '확정', '출발 33회', '—'),
    ('브레이크', '% → m/s² (0–60 %)', '0/40 → 0.1/1.0', '40 % ≈ 0.6–0.9 · 55 % ≈ 1.9 (타행 포함)', '잠정·근거 충분', '계단 39 + 구간', '04(a) 로 재확인'),
    ('브레이크', '% → m/s² (60–95 %)', '70/90 → 2.0/3.0', '깨끗한 표본 없음', '미측정', '0', '04(a): 60/70/80/90 % 계단'),
    ('브레이크', '100 %', '4.0 m/s²', '약 3.0 m/s²', '잠정·근거 충분', '원격 505 구간', 'brake_map 100 % = 3.0'),
    ('브레이크', '지연 (걸기 / 풀기)', '56–112 ms', '걸기 109 ms · 풀기 329 ms (90 %: 593 / 777 ms)', '확정', '계단 39', '—'),
    ('브레이크', '위치 피드백 0x200', '명령 ÷ 10', '82 % 이하는 맞음, 정지 100 % 포화 5.0–8.3', '확정', '15 세션', '피드백으로 쓰지 않음'),
    ('공통', '0x201 속도 배율', '1.0', '÷ 0.958 (2–25 km/h 일정)', '확정', 'GNSS 대조 5세션', '바퀴 지름 설정 확인(V10)'),
    ('공통', '0x210 송신 주기', '20 ms', '중앙값 19.98 ms · p99.9 22.8 ms', '확정', '11 세션', '—'),
    ('공통', 'ROS 입력 공백', 'stale 0.15 · odom 0.2 s', '/localization/odom 최대 336–348 ms (세션당 1–4회)', '잠정·근거 충분', '3 세션', '원인 = localization 쪽 (Q4)'),
    ('미정', '0x210 끊김 시 차량 동작', '—', '문서 충돌(TODO-2)', '미측정', '—', '실무자 확인'),
    ('미정', '앱 비상정지 실제 제동', 'ESTOP 30→82 %', '차량은 앱 원격 우선, 감속 ≈ 0.8 m/s² · 5 s', '잠정·근거 부족', '1회', '00 재정의 · 업체 확인'),
    ('미정', '경사 · 30 km/h 이상 · 고속 조향', '—', '30 km/h 넘은 시간 80 s (원격, 거의 100 %)', '미측정', '—', '속도 상한 올린 뒤'),
]
with open(os.path.join(TAB, 'interface_status.csv'), 'w', newline='') as f:
    f.write('# 인터페이스 항목별 상태(2026-11-05 정리). 값 출처: measurements/2026-11-04_drive_log/README.md 5장, tables/*.csv, launch_rolling_bins.csv. coverage.py\n')
    w = csv.writer(f)
    w.writerow(['area', 'item', 'current_setting', 'measured_1104', 'status', 'samples', 'next'])
    w.writerows(R)

# ───────────────────────── 3. 그림
for fp in ['/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc']:
    if os.path.exists(fp):
        fm.fontManager.addfont(fp)
plt.rcParams.update({'font.family': 'Noto Sans CJK JP', 'font.size': 10, 'axes.unicode_minus': False,
                     'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e',
                     'axes.grid': False, 'axes.spines.top': False, 'axes.spines.right': False,
                     'figure.facecolor': '#fcfcfb', 'axes.facecolor': '#fcfcfb', 'legend.frameon': False})
INK, INK2, MUTED, GRID, BAND = '#0b0b0b', '#52514e', '#8a8984', '#d9d8d2', '#f1f0ec'

fig = plt.figure(figsize=(15.6, 25))
gs = fig.add_gridspec(4, 2, height_ratios=[2.55, 0.35, 0.95, 0.95], hspace=0.22, wspace=0.16, left=0.07, right=0.97, top=0.95, bottom=0.04)
fig.text(0.04, 0.985, '인터페이스 완성도 — 11-04 실측으로 채운 것 · 아직 빈 것', fontsize=17, fontweight='bold', color=INK, va='top')
fig.text(0.04, 0.971, '2026-11-04 용인 · CAN 원본(Kvaser) + 자율 세션 bag(GNSS·INS) · 근거 문서 measurements/2026-11-04_drive_log/README.md 5장 · '
         'sheet1~5 · 모든 값 잠정(“확정” = 표본·재현성 충분, 판정 아님)', fontsize=9.5, color=INK2, va='top')

# ① 상태표
ax = fig.add_subplot(gs[0, :]); ax.axis('off')
ax.set_title('① 항목별 상태 — 현재 설정(track.yaml · follower · can_guard) vs 11-04 실측', loc='left', fontsize=11, fontweight='bold', color=INK)
cols = ['영역', '항목', '현재 설정', '11-04 실측 ★', '상태', '표본', '다음 (테스트)']
tb = ax.table(cellText=[list(r) for r in R], colLabels=cols, loc='upper left', cellLoc='left',
              colWidths=[0.05, 0.14, 0.14, 0.27, 0.09, 0.10, 0.21], bbox=[0, 0, 1, 1])
tb.auto_set_font_size(False); tb.set_fontsize(8.6)
for (r, c), cell in tb.get_celld().items():
    cell.set_edgecolor(GRID); cell.set_linewidth(0.6)
    cell.set_facecolor(BAND if r == 0 else '#fcfcfb')
    if r == 0:
        cell.get_text().set_fontweight('bold'); cell.get_text().set_color(INK2)
    elif c == 4:
        col = STATUS[R[r - 1][4]]
        cell.set_facecolor(col); cell.get_text().set_color('white'); cell.get_text().set_fontweight('bold')
    elif c in (0, 1):
        cell.get_text().set_fontweight('bold')

# ② 영역별 요약 막대
ax = fig.add_subplot(gs[1, :])
areas = ['조향', '가속', '브레이크', '공통', '미정']
left = np.zeros(len(areas))
for st, col in STATUS.items():
    cnt = np.array([sum(1 for r in R if r[0] == a and r[4] == st) for a in areas])
    ax.barh(areas, cnt, left=left, color=col, height=0.6, label=st)
    for i, c in enumerate(cnt):
        if c:
            ax.text(left[i] + c / 2, i, str(c), ha='center', va='center', color='white', fontsize=9, fontweight='bold')
    left += cnt
ax.invert_yaxis(); ax.set_xlabel('항목 수'); ax.set_xlim(0, 8.5)
ax.legend(ncol=4, loc='lower right', fontsize=9)
ax.set_title('② 영역별 상태', loc='left', fontsize=11, fontweight='bold', color=INK)


def heat(ax, H, edges, xlabel, title, gap=None, vmax=60):
    im = ax.imshow(np.where(H > 0, H, np.nan), origin='lower', aspect='auto', cmap='Blues',
                   extent=[edges[0], edges[-1], 0, len(VB) - 1], vmin=0, vmax=vmax)
    ax.set_yticks(np.arange(len(VB) - 1) + 0.5); ax.set_yticklabels([f'{a:g}–{b:g}' for a, b in zip(VB[:-1], VB[1:])])
    ax.set_ylabel('차속 [km/h]'); ax.set_xlabel(xlabel)
    w = edges[1] - edges[0]
    for yi in range(H.shape[0]):
        for xi in range(H.shape[1]):
            if H[yi, xi] >= 0.5:
                ax.text(edges[xi] + w / 2, yi + 0.5, f'{H[yi, xi]:.0f}', ha='center', va='center', fontsize=7,
                        color=INK if H[yi, xi] < vmax * 0.6 else 'white')
    if gap:
        for x0, x1, y0, y1, lab in gap:
            ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec='#eb6834', lw=1.8, ls='--'))
            ax.text((x0 + x1) / 2, y1 - 0.15, lab, ha='center', va='top', fontsize=8.5, color='#eb6834', fontweight='bold')
    ax.set_title(title, loc='left', fontsize=11, fontweight='bold', color=INK)


heat(fig.add_subplot(gs[2, 0]), Hacc, AX, '가속 명령 [%] (브레이크 0)', '③ 가속 % × 차속 — 표본 시간 [s]',
     gap=[(50, 80, 0, 2, '저속 × 50–80 %\n거의 없음(칸당 ≤ 1 s)'), (5, 75, 5, 6, '25 km/h 이상 중간 %\n거의 없음')])
heat(fig.add_subplot(gs[2, 1]), Hbrk, AX, '브레이크 명령 [%] (주행 중)', '④ 브레이크 % × 차속 — 표본 시간 [s]',
     gap=[(60, 95, 0, 6, '60–95 %\n비어 있음')], vmax=15)
heat(fig.add_subplot(gs[3, 0]), Hst, SX, '조향 위치 0x200 [핸들 °] (+ = 좌)', '⑤ 조향 위치 × 차속 — 표본 시간 [s]',
     gap=[(100, 160, 0, 6, '좌 100° 이상\n저속 소량(GNSS 없음)')], vmax=120)

# ⑥ 조향 변환표: 현재 vs 실측
ax = fig.add_subplot(gs[3, 1]); ax.grid(True, color='#e6e5e0')
rows = [r for r in csv.reader(l for l in open(os.path.join(TAB, 'steer_ratio_gnss.csv')) if not l.startswith('#'))]
h = rows[0]; rows = [dict(zip(h, r)) for r in rows[1:]]
x = np.array([float(r['wheel_mean_deg']) for r in rows]); y = np.array([float(r['tire_mean_deg']) for r in rows])
n = np.array([float(r['n']) for r in rows])
xx = np.linspace(-150, 150, 3)
ax.plot(xx, xx / 7.5, color=MUTED, ls='--', lw=1.5, label='현재 변환표 ÷ 7.5 (0 점 0°)')
ax.scatter(x, y, s=np.clip(n / 12, 12, 260), color='#2a78d6', alpha=0.75, edgecolor='white', label='11-04 실측 (원 크기 = 표본 수)')
ax.axvline(-13.7, color='#eb6834', lw=1, ls=':'); ax.text(-11, -14, '직진 핸들 −13.7°', color='#eb6834', fontsize=8.5)
ax.axvspan(100, 155, color='#eb6834', alpha=0.08, lw=0); ax.text(127, -15, '좌 큰 각\n미측정', ha='center', color='#eb6834', fontsize=8.5)
ax.set_xlim(-155, 155); ax.set_ylim(-21, 21)
ax.set_xlabel('조향 위치 [핸들 °]'); ax.set_ylabel('유효 타이어각 [타이어 °] (INS 요레이트)')
ax.set_title('⑥ 핸들 → 타이어 변환 — 현재 표 vs 실측', loc='left', fontsize=11, fontweight='bold', color=INK)
ax.legend(loc='upper left', fontsize=8.5)

fig.text(0.04, 0.008, '근거: interface_status.csv · steer_ratio_gnss.csv · CAN 원본 raw_can/*.zst (③~⑤는 이 스크립트가 직접 계산) | '
         '스크립트: tools/drive_log/cannpz.py → coverage.py', fontsize=8, color=MUTED)
fig.savefig(os.path.join(OUT, 'sheet0_interface_status.png'), dpi=100)
print('saved; acc s', Hacc.sum().round(), 'brk s', Hbrk.sum().round(), 'steer s', Hst.sum().round())
