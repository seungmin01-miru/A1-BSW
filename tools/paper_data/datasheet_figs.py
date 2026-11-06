#!/usr/bin/env python3
"""제어 데이터시트(04) 그림·CSV — 값은 아래 출처 md 의 표를 그대로 옮긴 것(원본 재계산 아님).

출처
  S2  measurements/2026-11-03_signal_audit/analysis/stage2_static.md  (2단계 자율 명령 0x210 정지 시험, 21:41~)
  EP  measurements/2026-11-03_signal_audit/analysis/episodes.md      (원격 주행 에피소드, '방향 판정' 절로 전/후진 확정)
  R7  measurements/2026-10-07_vehicle_lift/analysis_report.md        (10-07 리프트, 지연 표 1-2)
  VC  docs/a1_vehicle_characteristics.md                             (SIL 종단 지연)

    python3 tools/paper_data/datasheet_figs.py --out reports/2026-11-03_paper_data
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# S2 조향 표: 명령, (+쪽 지연, 90%, 정착, 최대), (−쪽 …)
STEER = [  # cmd, delay+, t90+, settle+, peak+, delay-, t90-, settle-, peak-
    (5, 0.21, 0.24, 5.7, 8.9, 0.19, 0.23, -6.9, -8.5),
    (10, 0.20, 0.24, 11.4, 12.8, 0.22, 0.26, -11.8, -13.5),
    (20, 0.21, 0.32, 19.0, 23.3, 0.21, 0.31, -21.5, -22.9),
    (30, 0.19, 0.40, 28.9, 34.0, 0.21, 0.40, -31.1, -32.9),
    (60, 0.21, 0.63, 60.3, 63.1, 0.19, 0.54, -58.3, -63.9),
    (90, 0.21, 0.75, 88.0, 95.2, 0.20, 0.60, -88.3, -94.0),
    (120, 0.18, 0.81, 120.0, 123.6, 0.20, 0.67, -120.6, -123.5),
    (150, 0.22, 0.87, 149.9, 153.3, 0.20, 0.73, -149.5, -153.0),
]
# S2 브레이크: 명령 %, 이전 %, 지연, 90%, 정착 위치 (내림 = 해제 방향, 올림 = 거는 방향)
BRAKE_DOWN = [(90, 100, None, None, 8.20), (80, 90, None, None, 8.20), (70, 80, 1.32, 1.61, 6.90), (60, 70, 0.50, 0.52, 5.90),
              (50, 60, 0.27, 0.48, 4.90), (40, 50, 0.30, 0.34, 3.90), (30, 40, 0.34, 0.39, 3.04), (20, 30, 0.10, 0.18, 2.00),
              (10, 20, 0.10, 0.23, 0.90)]
BRAKE_UP = [(30, None, 0.08, 0.19, 3.00), (50, None, 0.13, 0.25, 4.90), (70, None, 0.13, 0.28, 7.00), (100, None, 0.18, 0.26, 8.20)]
# EP: 방향 판정 절에 나열된 에피소드 (시각, 방향, 가속 100 % m/s², 타력 m/s², 브레이크 시작 속도, 브레이크 감속)
LONG = [
    ('16:17:30', '전진', 0.82, -0.41, 1.40, -1.87), ('16:18:03', '전진', 0.80, -0.42, 1.73, -2.05),
    ('16:18:31', '전진', 0.76, -0.21, 2.08, -2.83), ('16:19:10', '전진', 0.81, -0.38, 1.83, -2.22),
    ('16:20:33', '전진', 0.87, None, 2.71, -2.43), ('16:20:57', '전진', 0.81, -0.38, 1.81, -2.14),
    ('16:21:19', '전진', 0.81, -0.34, 2.06, -2.26), ('16:32:13', '전진', 0.88, -0.37, 1.83, -2.17),
    ('16:17:48', '후진', 0.77, -0.19, 1.20, -1.75), ('16:18:16', '후진', 0.84, -0.21, 1.52, -1.79),
    ('16:18:43', '후진', 0.82, -0.23, 2.10, -2.63), ('16:20:44', '후진', 0.90, -0.19, 1.56, -1.93),
    ('16:21:07', '후진', 0.90, -0.18, 1.50, -1.97), ('16:21:28', '후진', 0.91, None, 1.90, -2.04),
]
LAUNCH_DELAY = [0.65, 0.51, 0.65, 0.61, 0.61, 0.69, 0.53, 0.57, 0.67, 0.68, 0.60, 0.64, 0.64, 0.64]  # EP 위 에피소드들의 출발 지연


def stats(x):
    x = np.array([v for v in x if v is not None], float)
    return dict(n=len(x), mean=x.mean(), sd=x.std(ddof=1) if len(x) > 1 else 0, min=x.min(), max=x.max())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='reports/2026-11-03_paper_data')
    a = ap.parse_args()
    from _style import plt, C
    os.makedirs(os.path.join(a.out, 'fig'), exist_ok=True)
    os.makedirs(os.path.join(a.out, 'data'), exist_ok=True)

    # CSV
    with open(os.path.join(a.out, 'data', 'steer_step_S2.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['cmd_deg', 'side', 'delay_s', 't90_s', 'settle_deg', 'peak_deg', 'overshoot_deg', 'mean_slew_dps'])
        for c, d1, t1, s1, p1, d2, t2, s2, p2 in STEER:
            for side, d, t, s, p in (('+', d1, t1, s1, p1), ('-', d2, t2, -s2, -p2)):
                w.writerow([c, side, d, t, s, p, round(p - c, 1), round(0.9 * c / (t - d), 0)])
    with open(os.path.join(a.out, 'data', 'brake_map_S2.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['cmd_pct', 'direction', 'prev_pct', 'delay_s', 't90_s', 'position_pct'])
        for r in BRAKE_DOWN:
            w.writerow([r[0], 'release', r[1], r[2], r[3], round(r[4] * 10, 1)])
        for r in BRAKE_UP:
            w.writerow([r[0], 'apply', '', r[2], r[3], round(r[4] * 10, 1)])
    with open(os.path.join(a.out, 'data', 'longitudinal_EP.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['time', 'direction', 'acc100_mps2', 'coast_mps2', 'brake_v0_mps', 'brake100_mps2'])
        w.writerows(LONG)

    # 그림 3a: 조향 계단
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    cmd = np.array([r[0] for r in STEER])
    for side, idx, mk, col in (('+', (1, 2, 3, 4), 'o', C['tx']), ('−', (5, 6, 7, 8), 's', C['rx'])):
        d, t90, st, pk = (np.abs(np.array([r[i] for r in STEER])) for i in idx)
        ax[0].plot(cmd, t90, mk + '-', color=col, label=f'90 % 도달 ({side})')
        ax[0].plot(cmd, d, mk + ':', color=col, alpha=0.6, label=f'지연 ({side})')
        ax[1].plot(cmd, 0.9 * cmd / (t90 - d), mk + '-', color=col, label=side)
        ax[2].plot(cmd, pk - cmd, mk + '-', color=col, label=f'오버슛 ({side})')
        ax[2].plot(cmd, st - cmd, mk + ':', color=col, alpha=0.6, label=f'정착 오차 ({side})')
    ax[0].set_ylabel('시간 (s)')
    ax[1].set_ylabel('평균 선회 속도 0.9·A / (t90 − 지연) (°/s)')
    ax[2].set_ylabel('각도 (°)')
    for x, t in zip(ax, ('(a) 지연·90 % 도달 시간', '(b) 평균 선회 속도', '(c) 오버슛·정착 오차')):
        x.set_xlabel('명령 크기 |A| (° 핸들각)')
        x.set_title(t, fontsize=9, loc='left')
        x.legend(fontsize=7)
    ax[2].axhspan(-2, 2, color=C['other'], alpha=0.15)
    fig.suptitle('그림 3a. 조향 계단 응답 — 0x210 자율 명령, 정지 차량(앞바퀴 접지), 2026-11-03', fontsize=10, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, 'fig', 'fig3a_steer_step.png'), dpi=200)
    plt.close(fig)

    # 그림 3b: 브레이크 맵
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.4))
    xd = [r[0] for r in BRAKE_DOWN]
    ax[0].plot(xd, [r[4] * 10 for r in BRAKE_DOWN], 'o-', color=C['rx'], label='해제 방향(100→10)')
    ax[0].plot([r[0] for r in BRAKE_UP], [r[4] * 10 for r in BRAKE_UP], 's', color=C['tx'], ms=7, mfc='none', label='거는 방향')
    xx = np.linspace(0, 100, 101)
    ax[0].plot(xx, np.minimum(xx, 82), '--', color=C['ref'], lw=0.8, label='위치 = min(명령 %, 82 %)')
    ax[0].axvline(82, color=C['other'], ls=':')
    ax[0].text(83, 10, '포화 ≈ 82 %', fontsize=8)
    ax[0].set_xlabel('break_command (%)')
    ax[0].set_ylabel('break_postion (0x200, %)')
    ax[0].set_title('(a) 정적 맵', fontsize=9, loc='left')
    ax[0].legend(fontsize=7)
    rd = [r for r in BRAKE_DOWN if r[2] is not None]
    ax[1].plot([r[0] for r in rd], [r[3] for r in rd], 'o-', color=C['rx'], label='해제 90 %')
    ax[1].plot([r[0] for r in rd], [r[2] for r in rd], 'o:', color=C['rx'], alpha=0.6, label='해제 지연')
    ax[1].plot([r[0] for r in BRAKE_UP], [r[3] for r in BRAKE_UP], 's-', color=C['tx'], label='걸기 90 %')
    ax[1].plot([r[0] for r in BRAKE_UP], [r[2] for r in BRAKE_UP], 's:', color=C['tx'], alpha=0.6, label='걸기 지연')
    ax[1].set_xlabel('목표 명령 (%)')
    ax[1].set_ylabel('시간 (s)')
    ax[1].set_title('(b) 걸기 빠름 · 풀기 느림 (70 % 해제는 포화 탈출)', fontsize=9, loc='left')
    ax[1].legend(fontsize=7)
    fig.suptitle('그림 3b. 브레이크 명령 → 위치 — 0x210 자율 명령, 정지, 2026-11-03', fontsize=10, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, 'fig', 'fig3b_brake_map.png'), dpi=200)
    plt.close(fig)

    # 그림 3c: 종방향
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.4))
    for k, (dirn, col, mk) in enumerate((('전진', C['tx'], 'o'), ('후진', C['rx'], 's'))):
        rows = [r for r in LONG if r[1] == dirn]
        acc = [r[2] for r in rows]
        coast = [r[3] for r in rows if r[3] is not None]
        ax[0].scatter(np.full(len(acc), k) + np.random.default_rng(1).uniform(-.08, .08, len(acc)), acc, color=col, marker=mk)
        ax[0].scatter(np.full(len(coast), k + 2.5) + np.random.default_rng(2).uniform(-.08, .08, len(coast)), coast, color=col, marker=mk)
        ax[1].scatter([r[4] for r in rows], [r[5] for r in rows], color=col, marker=mk, label=dirn)
    ax[0].set_xticks([0, 1, 2.5, 3.5])
    ax[0].set_xticklabels(['가속100\n전진', '가속100\n후진', '타력\n전진', '타력\n후진'])
    ax[0].axhline(0, color=C['ref'], lw=0.6)
    ax[0].set_ylabel('구간 평균 가감속 (m/s²)')
    ax[0].set_title('(a) 가속 100 % · 타력', fontsize=9, loc='left')
    v = np.array([r[4] for r in LONG])
    dcl = np.array([r[5] for r in LONG])
    p = np.polyfit(v, dcl, 1)
    vv = np.linspace(1.1, 2.8, 10)
    ax[1].plot(vv, np.polyval(p, vv), '--', color=C['ref'], lw=0.8, label=f'선형 맞춤 {p[0]:+.2f}·v {p[1]:+.2f}')
    ax[1].set_xlabel('브레이크 시작 속도 (m/s)')
    ax[1].set_ylabel('브레이크 100 % 구간 평균 감속 (m/s²)')
    ax[1].set_title('(b) 브레이크 100 % — 속도 의존', fontsize=9, loc='left')
    ax[1].legend(fontsize=7)
    fig.suptitle('그림 3c. 종방향 응답 — 원격 0/100 % 명령, 땅 위 저속(≤ 2.9 m/s), 2026-11-03', fontsize=10, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, 'fig', 'fig3c_longitudinal.png'), dpi=200)
    plt.close(fig)

    # 그림 3d: 지연 예산
    fig, ax = plt.subplots(figsize=(9, 3.0))
    parts = [('제어기→0x210 (SIL 최대)', 33.6, C['tx']), ('버스 중재·전송', 1.4, C['other']),
             ('차량 수락 (0x200 주기)', 22, C['rx2'])]
    for row, (name, act, col) in enumerate((('조향', 210, '#E69F00'), ('브레이크', 180, '#CC79A7'), ('구동', 690, '#009E73'))):
        x = 0
        for lbl, val, c in parts:
            ax.barh(row, val, left=x, color=c, edgecolor='white', label=lbl if row == 0 else None)
            x += val
        ax.barh(row, act, left=x, color=col, edgecolor='white', label=f'{name} 무반응 {act} ms')
        ax.text(x + act + 8, row, f'{x + act:.0f} ms', va='center', fontsize=8)
    ax.set_yticks([0, 1, 2])
    ax.set_yticklabels(['조향', '브레이크', '구동'])
    ax.invert_yaxis()
    ax.set_xlabel('제어기 출력 → 움직임 시작까지 최악 (ms)')
    ax.legend(fontsize=7, ncol=3, loc='upper center', bbox_to_anchor=(0.5, -0.35), frameon=False)
    ax.set_xlim(0, 820)
    ax.set_title('그림 3d. 종단 지연 예산 (최악값 합)', fontsize=10, loc='left')
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, 'fig', 'fig3d_latency_budget.png'), dpi=200)
    plt.close(fig)

    # 요약 수치 출력 (04 에 옮김)
    for dirn in ('전진', '후진'):
        rows = [r for r in LONG if r[1] == dirn]
        print(dirn, 'acc', stats([r[2] for r in rows]), 'coast', stats([r[3] for r in rows]))
    print('brake fit', p, 'launch', stats(LAUNCH_DELAY))
    sl = [0.9 * r[0] / (r[2] - r[1]) for r in STEER] + [0.9 * r[0] / (r[6] - r[5]) for r in STEER]
    print('steer slew by cmd', [round(x) for x in sl])
    print('delay', stats([r[1] for r in STEER] + [r[5] for r in STEER]))
    print('overshoot', [round(r[4] - r[0], 1) for r in STEER], [round(-r[8] - r[0], 1) for r in STEER])


if __name__ == '__main__':
    main()
