#!/usr/bin/env python3
"""2026-11-05 BSW 개선 보고서 그림 — `python3 make_figs.py <저장소 루트> .`

Fig. 1  11-04 앱 비상정지 시간 이력      ← bsw_test/00_SAF_estop/results/ref_20261104_app_estop/20261104_auto_bsw4_estop_1.png 사본
Fig. 2  어댑터 arm 상태와 can_guard 반응 (도식)
Fig. 3  시나리오 0x210 출력(follow/hold/loss/disarm/down) ← data/scenarios.csv (scenarios.py)
Fig. 4  Kvaser 채널 0 을 쓰는 핸들 구성 (도식)
Fig. 5  11-04 자율 세션 수신율 ← data/kvaser_rx.tsv (tools/drive_log/kvaser_rx_check.py)
"""
import csv
import os
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

ROOT, OUT = sys.argv[1], sys.argv[2]
FIG = os.path.join(OUT, 'figs')
os.makedirs(FIG, exist_ok=True)
for p in ('/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'):
    if os.path.exists(p):
        font_manager.fontManager.addfont(p)
        FONT = font_manager.FontProperties(fname=p).get_name()
        break
plt.rcParams.update({
    'font.family': FONT, 'font.size': 8.5, 'axes.unicode_minus': False, 'axes.linewidth': 0.7,
    'xtick.direction': 'in', 'ytick.direction': 'in', 'grid.color': '0.8', 'grid.linestyle': ':',
    'legend.frameon': False, 'legend.fontsize': 7.5, 'savefig.dpi': 220, 'savefig.bbox': 'tight', 'savefig.pad_inches': 0.03,
})
K, G1, G2, G3 = 'black', '0.35', '0.6', '0.88'


def box(ax, x, y, w, h, title, body='', fill='white', lw=1.0, ls='-'):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.01,rounding_size=0.015', fc=fill, ec=K, lw=lw, ls=ls))
    ax.text(x + w / 2, y + h - 0.025, title, ha='center', va='top', fontsize=9, fontweight='bold')
    if body:
        ax.text(x + 0.012, y + h - 0.075, body, ha='left', va='top', fontsize=7.2, linespacing=1.45)


def arrow(ax, a, b, text='', off=(0, 0), rad=0.0, ls='-', fs=7.0, ha='center'):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle='-|>', mutation_scale=9, lw=0.9, color=K, ls=ls,
                                 connectionstyle=f'arc3,rad={rad}'))
    if text:
        ax.text((a[0] + b[0]) / 2 + off[0], (a[1] + b[1]) / 2 + off[1], text, ha=ha, va='center', fontsize=fs,
                bbox=dict(fc='white', ec='none', pad=0.6), linespacing=1.35)


def fig1():
    src = os.path.join(ROOT, 'bsw_test/00_SAF_estop/results/ref_20261104_app_estop/20261104_auto_bsw4_estop_1.png')
    shutil.copy(src, os.path.join(FIG, 'fig1_app_estop.png'))


def fig2():
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    ax.set_xlim(-0.015, 1.015)
    ax.set_ylim(0, 1.06)
    ax.axis('off')
    ax.text(0.0, 1.06, '어댑터 (a1_control_adapter)', fontsize=8, color=G1, va='top')
    ax.text(0.0, 0.30, 'can_guard → 0x210', fontsize=8, color=G1, va='top')
    box(ax, 0.0, 0.47, 0.25, 0.34, 'DISARM (시작)',
        '공유메모리: auto 000·명령 0\n하트비트는 계속\ncan_guard: ACTIVE, 침묵\n(0x210 안 보냄)\n차량: 사람·원격·페일세이프')
    box(ax, 0.375, 0.47, 0.25, 0.34, 'ARMED',
        '조향·종방향 둘 다\n표 변환 → 50 Hz 기록\n조향: 0x200 실제값에서\n  120 °/s 로 출발\ncan_guard: auto 111 송신', fill=G3)
    box(ax, 0.75, 0.47, 0.25, 0.34, 'LATCHED (고장 래치)',
        '기록·하트비트 멈춤\n입력이 돌아와도 그대로\ncan_guard: 50 ms 뒤 HOLDING\n  가속 0·조향 고정\n  브레이크 30→60 %\n  2 s / ≤ 3 km/h → STOPPED')
    arrow(ax, (0.25, 0.72), (0.375, 0.72))
    ax.text(0.3125, 0.93, '자동 arm (한 번만)\n입력 0.3 s 연속 신선 + can_guard\n공유메모리 + 0x200 ≤ 0.5 s', ha='center',
            va='center', fontsize=6.6, linespacing=1.35)
    ax.plot([0.3125, 0.3125], [0.87, 0.73], color=K, lw=0.6, ls=':')
    arrow(ax, (0.25, 0.55), (0.375, 0.55), 'arm 서비스', off=(0, -0.04), fs=6.5)
    arrow(ax, (0.625, 0.72), (0.75, 0.72))
    ax.text(0.6875, 0.90, '입력 150 ms 끊김\nNaN·범위 밖 값', ha='center', va='center', fontsize=6.6)
    ax.plot([0.6875, 0.6875], [0.86, 0.73], color=K, lw=0.6, ls=':')
    arrow(ax, (0.50, 0.47), (0.14, 0.47), 'disarm → 이후 자동 arm 안 함', off=(0, -0.055), rad=-0.30, fs=6.5)
    arrow(ax, (0.875, 0.47), (0.10, 0.47), 'disarm(래치 해제) — can_guard 가 STOPPED 면 재시작', off=(0.04, -0.16), rad=-0.38, fs=6.5)
    # can_guard row
    xs = [0.01, 0.205, 0.40, 0.595, 0.79]
    names = ['INIT\n(침묵)', 'ACTIVE\n명령 전달', 'HOLDING\n브레이크 30→60', 'STOPPED\n브레이크 유지', 'ESTOP\n30→82 %·래치']
    for x, n in zip(xs, names):
        ax.add_patch(FancyBboxPatch((x, 0.06), 0.185, 0.17, boxstyle='round,pad=0.005', fc='white', ec=K, lw=0.8))
        ax.text(x + 0.0925, 0.145, n, ha='center', va='center', fontsize=7.4)
    for a, b, t in [(0, 1, '첫 명령'), (1, 2, '명령 > 50 ms'), (2, 3, '2 s / 정지')]:
        arrow(ax, (xs[a] + 0.185, 0.145), (xs[b], 0.145), t, off=(0, 0.075), fs=6.3)
    ax.text(0.882, 0.265, '앱 En · estop.py · SIGUSR1 (어느 상태에서든)', ha='center', fontsize=6.2)
    ax.text(0.5, 0.005, '보호 상태·ESTOP 은 auto 비트를 유지한다(auto 000 금지). STOPPED·ESTOP 은 can_guard 재시작으로만 빠져나간다.',
            ha='center', fontsize=6.8, color=G1)
    fig.savefig(os.path.join(FIG, 'fig2_arm_states.png'))
    plt.close(fig)


def fig3():
    rows = [r for r in csv.reader(open(os.path.join(OUT, 'data/scenarios.csv'))) if r and not r[0].startswith('#')][1:]
    d = {}
    for r in rows:
        d.setdefault(r[0], []).append((float(r[1]), float(r[2]), int(r[3]), int(r[4])))
    fig, ax = plt.subplots(2, 2, figsize=(7.2, 4.6))
    a = ax[0, 0]
    for name, st, lab in (('follow', '-', 'follow(새 기본)'), ('hold', '--', 'hold(예전)')):
        w = d[name]
        a.plot([x[0] for x in w], [x[1] for x in w], st, color=K if name == 'follow' else G2, lw=1.3, label=lab)
    a.axvspan(1.0, 2.5, color=G3, lw=0)
    a.text(1.75, -38, '앱 En = 1\n(차량 핸들 +5°)', ha='center', fontsize=7)
    a.axhline(5, color=G1, lw=0.6, ls=':')
    a.set_title('(a) 앱 비상정지: 우리 0x210 핸들각', fontsize=8.5)
    a.set_ylabel('핸들 °')
    a.legend(loc='center left', bbox_to_anchor=(0.0, 0.55))
    a.text(3.25, -25, '해제(2.5 s) 뒤 차량은\n이 값을 따른다\n→ hold 는 54.8° 튐', fontsize=6.8, ha='center')
    a = ax[0, 1]
    w = d['loss']
    a.plot([x[0] for x in w], [x[2] for x in w], color=K, lw=1.3, label='브레이크 %')
    a.plot([x[0] for x in w], [x[3] for x in w], color=G2, lw=1.1, label='가속 %')
    a.axvline(1.0, color=G1, lw=0.6, ls=':')
    a.text(1.07, 70, '입력 끊김(1.0 s)\nHOLDING +64 ms\nSTOPPED +2.08 s', fontsize=6.8, va='top')
    a.set_title('(b) 제어기 입력 끊김 = 고장 래치', fontsize=8.5)
    a.set_ylim(-3, 100)
    a.legend(loc='center right')
    a = ax[1, 0]
    w = d['disarm']
    a.plot([x[0] for x in w], [x[3] for x in w], color=G2, lw=1.1, label='가속 %')
    a.plot([x[0] for x in w], [x[2] for x in w], color=K, lw=1.3, label='브레이크 %')
    a.axvline(1.0, color=G1, lw=0.6, ls=':')
    a.set_xlim(0, 3)
    a.set_ylim(-3, 100)
    a.text(1.08, 95, 'disarm(1.0 s) → 0x210 송신 끊김\n브레이크를 걸지 않는다\n(0x210 끊김 때 차량 동작 = 미확정)', fontsize=6.8, va='top')
    a.set_title('(c) 주행 중 disarm', fontsize=8.5)
    a.set_xlabel('시간 [s]')
    a.legend(loc='center right', bbox_to_anchor=(1.0, 0.4))
    a = ax[1, 1]
    w = d['down']
    a.plot([x[0] for x in w], [x[2] for x in w], color=K, lw=1.3, label='브레이크 %')
    a.plot([x[0] for x in w], [x[3] for x in w], color=G2, lw=1.1, label='가속 %')
    for t, s in ((1.0, 'estop.py'), (3.0, 'can_guard 종료')):
        a.axvline(t, color=G1, lw=0.6, ls=':')
        a.text(t + 0.04, 95, s, fontsize=6.8, va='top')
    a.set_xlim(0, 3.6)
    a.set_ylim(-3, 100)
    a.set_title('(d) bsw_down: ESTOP → 종료', fontsize=8.5)
    a.set_xlabel('시간 [s]')
    a.legend(loc='center right')
    for a in ax.flat:
        a.grid(True)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig3_scenarios.png'))
    plt.close(fig)


def fig4():
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    ax.set_xlim(-0.015, 1.015)
    ax.set_ylim(0, 1)
    ax.axis('off')
    box(ax, 0.0, 0.30, 0.19, 0.42, '차량 CAN', '0x200·0x201 21 ms\n앱 0x156·0x157\n  10 ms\n대회측 0x128·0x7xx')
    box(ax, 0.26, 0.22, 0.17, 0.58, 'Kvaser Leaf\nCANlib 채널 0', '\n\n하드웨어 1개를\n핸들 2개가 공유\n(드라이버가 받은\n프레임을 핸들마다\n따로 넘김)', fill=G3)
    box(ax, 0.52, 0.56, 0.27, 0.40, '① kvaser_mirror', 'normal 모드(ACK 함)\nsingle_handle, 로컬 에코 켬\n버스에 아무것도 안 보냄\n→ kvaser_ch0.log (원본)\n→ kv0 (vcan 사본)')
    box(ax, 0.52, 0.06, 0.27, 0.42, '② can_guard', 'CPU 8 · SCHED_FIFO 90\n0x210 송신 50 Hz\n0x200/0x201/0x156/0x157 수신\n자기 송신은 안 받음\n다른 0x210 → foreign 경고')
    box(ax, 0.83, 0.56, 0.17, 0.40, 'kv0 읽기만', 'candump → log\n상태 브리지(ROS)\nbsw_monitor\n(Kvaser 를\n 열지 않음)', ls='--')
    arrow(ax, (0.19, 0.53), (0.26, 0.53))
    arrow(ax, (0.26, 0.47), (0.19, 0.47))
    arrow(ax, (0.43, 0.70), (0.52, 0.76))
    ax.text(0.468, 0.90, '수신 전부 +\n②의 송신 에코', ha='center', fontsize=6.2)
    arrow(ax, (0.43, 0.32), (0.52, 0.28))
    ax.text(0.475, 0.235, '수신', ha='center', fontsize=6.4)
    arrow(ax, (0.52, 0.40), (0.43, 0.40))
    ax.text(0.475, 0.43, '0x210', ha='center', fontsize=6.4)
    arrow(ax, (0.79, 0.76), (0.83, 0.76))
    ax.text(0.5, -0.03, '11-03 실패 조합: ①을 python-can 기본(핸들 2개) + 로컬 에코로 열었을 때 ②의 송신 타임아웃·에러프레임 약 350/s.  '
            '①을 silent(listen-only)로 연 채 송신하는 것도 피한다.', ha='center', fontsize=6.5, color=G1)
    fig.savefig(os.path.join(FIG, 'fig4_kvaser.png'))
    plt.close(fig)


def fig5():
    rows = [ln.rstrip('\n').split('\t') for ln in open(os.path.join(OUT, 'data/kvaser_rx.tsv'))][1:]
    rows = [r for r in rows if r[0].startswith('auto_bsw') and r[2] in ('0x200', '0x201', '0x156')]
    fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.3))
    labels = [f'{r[0].replace("auto_", "")}\n{r[2]}' for r in rows]
    ax[0].bar(range(len(rows)), [float(r[4]) for r in rows], color=G2, ec=K, lw=0.6)
    ax[0].set_ylim(90, 101)
    ax[0].set_ylabel('기대 대비 수신 %')
    ax[0].set_xticks(range(len(rows)))
    ax[0].set_xticklabels(labels, fontsize=6)
    ax[0].set_title('(a) can_guard 송신 중 수신율', fontsize=8.5)
    ax[1].bar(range(len(rows)), [float(r[5]) for r in rows], color=G2, ec=K, lw=0.6)
    ax[1].axhline(21, color=K, lw=0.6, ls=':')
    ax[1].axhline(10, color=K, lw=0.6, ls=':')
    ax[1].set_ylabel('최대 공백 ms')
    ax[1].set_xticks(range(len(rows)))
    ax[1].set_xticklabels(labels, fontsize=6)
    ax[1].set_title('(b) 가장 긴 수신 공백 (점선 = 정상 주기 21·10 ms)', fontsize=8.5)
    for a in ax:
        a.grid(True, axis='y')
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, 'fig5_kvaser_rx.png'))
    plt.close(fig)


if __name__ == '__main__':
    fig1()
    fig2()
    fig3()
    fig4()
    fig5()
    print('그림 →', FIG)
