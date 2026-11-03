#!/usr/bin/env python3
"""녹화 → 그림. 명령과 차량 반응을 같은 시간축에 겹쳐 보고(timeline), 명령-응답 한계를 산점도로 본다(limits).

  python3 tools/signal_audit/plot.py <세션폴더>                                  # 전체
  python3 tools/signal_audit/plot.py <세션폴더> --from 11:26:00 --to 11:34:00 --tag brake   # 구간 확대
  python3 tools/signal_audit/plot.py <세션폴더> --raw ~/a1_race_capture/vehicle_20261007    # 원본이 다른 곳

출력 <세션폴더>/plots/:
  timeline[_tag].png  5단: 조향(0x210·원격 0x157 명령 vs 0x200 위치) / 브레이크(명령 ÷ 10 vs 위치) /
                      가속(0x210 % · 원격 m/s²) / 바퀴 속도(0x201) / auto 비트·원격 En. 세로선 = notes.txt 메모
  limits.png          명령 → 응답 산점도(정착 상태만, limits.md 와 같은 표본) + 구간 중앙값 + DBC 명령 한계
  freespin.png        뒷바퀴 공회전 계단 응답 겹쳐 보기(명령 시작 = 0 s, rpm) — 바퀴가 돈 녹화에서만
명령 선은 해당 축 auto(원격은 En)가 켜진 동안만 그린다 — 꺼져 있으면 차량이 무시하므로.
"""
import argparse
import datetime
import os
import re
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.font_manager  # noqa: E402,F401
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_data as ad  # noqa: E402

# 한글 글꼴: matplotlib 3.5 는 글자별 대체를 안 하므로 한글이 들어 있는 글꼴 하나를 골라 맨 앞에 둔다
# (Noto CJK 는 .ttc 라 'Noto Sans CJK JP' 이름으로만 등록되기도 한다 — 한글도 들어 있음)
_have = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
plt.rcParams['font.family'] = [f for f in ('Noto Sans CJK KR', 'Noto Sans CJK JP', 'NanumGothic', 'Malgun Gothic',
                                           'AppleGothic') if f in _have][:1] + ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

C = {'cmd': '#d62728', 'remote': '#9467bd', 'resp': '#1f77b4', 'aux': '#2ca02c', 'grid': '#cccccc'}


def to_dt(t):
    """epoch 초 → matplotlib 용 로컬 시각 datetime64."""
    if not len(t):
        return t
    off = datetime.datetime.fromtimestamp(t[0]) - datetime.datetime.utcfromtimestamp(t[0])
    return (np.asarray(t) * 1e6).astype('datetime64[us]') + np.timedelta64(int(off.total_seconds()), 's')


def parse_hms(day_t, hms):
    d = datetime.datetime.fromtimestamp(day_t).date()
    return datetime.datetime.combine(d, datetime.time.fromisoformat(hms)).timestamp()


def masked(s, fid, name, gate_fid, gate_name):
    t, v = s.sig(fid, name)
    if not len(t):
        return t, v
    g = s.at(gate_fid, gate_name, t) == 1
    v = v.astype(float).copy()
    v[~g] = np.nan
    return t, v


def window(t, v, t0, t1):
    if not len(t):
        return t, v
    i = (t >= t0) & (t <= t1)
    return t[i], v[i]


def notes(raw):
    out = []
    import glob
    for p in sorted(glob.glob(os.path.join(raw, '**', 'notes.txt'), recursive=True)):
        for line in open(p, errors='replace'):
            m = re.match(r'(\d+\.\d+) \d\d:\d\d:\d\d (.*)', line.strip())
            if m:
                out.append((float(m.group(1)), m.group(2)))
    return out


def timeline(s, raw, t0, t1, path):
    fig, ax = plt.subplots(5, 1, figsize=(16, 13), sharex=True,
                           gridspec_kw={'height_ratios': [3, 2, 2, 2, 1.4]})

    def line(a, t, v, **kw):
        t, v = window(t, v, t0, t1)
        if len(t):
            a.plot(to_dt(t), v, drawstyle='steps-post', lw=1.1, **kw)

    # 1 조향
    line(ax[0], *masked(s, 0x210, 'steer_command', 0x210, 'steer_is_auto_command'), color=C['cmd'], label='0x210 명령 (auto 조향)')
    line(ax[0], *masked(s, 0x157, 'EPS_Cmd', 0x156, 'EPS_En'), color=C['remote'], label='원격 EPS_Cmd (En=1)')
    line(ax[0], *s.sig(0x200, 'steer_postion'), color=C['resp'], label='0x200 위치')
    ax[0].set_ylabel('조향 핸들각 (°)')
    # 2 브레이크
    t, v = masked(s, 0x210, 'break_command', 0x210, 'break_is_auto_command')
    line(ax[1], t, v / 10 if len(v) else v, color=C['cmd'], label='0x210 브레이크 % ÷ 10 (auto)')
    line(ax[1], *s.sig(0x200, 'break_postion'), color=C['resp'], label='0x200 브레이크 위치')
    ax[1].set_ylabel('브레이크 위치')
    # 3 가속
    line(ax[2], *masked(s, 0x210, 'acc_command', 0x210, 'acc_is_auto_command'), color=C['cmd'], label='0x210 가속 % (auto)')
    ax[2].set_ylabel('가속 명령 (%)')
    r = ax[2].twinx()
    line(r, *masked(s, 0x157, 'ACC_Cmd', 0x156, 'ACC_En'), color=C['remote'], label='원격 ACC_Cmd (En=1)')
    r.set_ylabel('원격 가감속 (m/s²)', color=C['remote'])
    r.axhline(0, color=C['remote'], lw=0.5, ls=':')
    h1, l1 = ax[2].get_legend_handles_labels()
    h2, l2 = r.get_legend_handles_labels()
    ax[2].legend(h1 + h2, l1 + l2, loc='upper left', fontsize=8)
    # 4 바퀴
    line(ax[3], *s.sig(0x201, 'left_speed'), color=C['resp'], label='0x201 왼쪽')
    line(ax[3], *s.sig(0x201, 'right_speed'), color=C['aux'], label='0x201 오른쪽')
    ax[3].set_ylabel('바퀴 속도 (km/h)')
    r3 = ax[3].twinx()
    tw, wv = ad.wheel_omega(s)
    line(r3, tw, wv * 60 / (2 * np.pi) if len(wv) else wv, color='#7f7f7f', ls=':', label='rpm 평균')
    r3.set_ylabel('rpm', color='#7f7f7f')
    h1, l1 = ax[3].get_legend_handles_labels()
    h2, l2 = r3.get_legend_handles_labels()
    ax[3].legend(h1 + h2, l1 + l2, loc='upper left', fontsize=8)
    # 5 auto 비트 / En
    for k, (fid, name, lab, col) in enumerate([
            (0x200, 'steer_is_auto', '차량 auto 조향', C['resp']), (0x200, 'break_is_auto', '차량 auto 브레이크', C['resp']),
            (0x200, 'acc_is_auto', '차량 auto 가속', C['resp']), (0x156, 'EPS_En', '원격 EPS_En', C['remote']),
            (0x156, 'ACC_En', '원격 ACC_En', C['remote'])]):
        t, v = s.sig(fid, name)
        line(ax[4], t, v * 0.8 + k if len(v) else v, color=col)
        ax[4].text(1.002, (k + 0.4) / 5, lab, transform=ax[4].transAxes, fontsize=8, va='center', color=col)
    ax[4].set_yticks([])
    ax[4].set_ylim(-0.3, 5)
    ax[4].set_ylabel('auto / En')

    for a in ax[:4]:
        if not a.get_legend():
            a.legend(loc='upper left', fontsize=8)
    for a in ax:
        a.grid(True, color=C['grid'], lw=0.5)
    memo = [(t, m) for t, m in notes(raw) if t0 <= t <= t1]
    for t, m in memo:
        x = to_dt(np.array([t]))[0]
        for a in ax:
            a.axvline(x, color='#888888', lw=0.6, ls='--')
        if len(memo) <= 30:
            m = re.sub(r'[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F]', '', m).strip()   # 이모지(글꼴에 없음)
            ax[0].text(x, 1.0, ' ' + m[:28], transform=ax[0].get_xaxis_transform(), rotation=60,
                       fontsize=7, va='bottom', ha='left', color='#555555')
    ax[4].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    fig.suptitle(f'{datetime.datetime.fromtimestamp(t0):%Y-%m-%d %H:%M:%S} ~ {datetime.datetime.fromtimestamp(t1):%H:%M:%S}',
                 y=0.995 if len(memo) > 30 or not memo else 1.06)
    fig.tight_layout()
    fig.savefig(path, dpi=110, bbox_inches='tight')
    plt.close(fig)


def limits(s, path):
    ps = ad.pairs(s)
    if not ps:
        return False
    n = len(ps)
    cols = 3 if n > 4 else 2
    rows = (n + cols - 1) // cols
    fig, axs = plt.subplots(rows, cols, figsize=(6 * cols, 4.6 * rows), squeeze=False)
    for a, p in zip(axs.flat, ps):
        x, y = p['cmd'], p['resp']
        a.scatter(x, y, s=6, alpha=0.35, color=C['remote'] if '원격' in p['label'] else C['cmd'])
        rows_b = ad.binned(x, y, ad.bin_width(x, p['cmd_unit']))
        if rows_b:
            a.plot([r[0] for r in rows_b], [r[2] for r in rows_b], '-o', color='black', ms=3, lw=1, label='구간 중앙값')
        lo, hi = float(np.nanmin(x)), float(np.nanmax(x))
        span = max(hi - lo, 1e-6)
        if p['ident']:
            xs = np.array([lo, hi])
            a.plot(xs, xs, ls=':', color='#888888', lw=1, label='응답 = 명령')
        for lim in p['dbc']:
            if lo - 0.5 * span <= lim <= hi + 0.5 * span:
                a.axvline(lim, color='#ff7f0e', ls='--', lw=1, label=f'명령 한계 {lim:g}')
        a.set_title(f'{p["label"]}  (n={len(x)})', fontsize=10)
        a.set_xlabel(f'명령 ({p["cmd_unit"]})')
        a.set_ylabel(f'응답 ({p["resp_unit"]})')
        a.grid(True, color=C['grid'], lw=0.5)
        a.legend(fontsize=7, loc='best')
    for a in list(axs.flat)[n:]:
        a.axis('off')
    fig.suptitle(f'명령 → 응답 (명령이 {ad.HOLD_S:g} s 이상 그대로였던 정착 상태만)')
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def freespin(s, path):
    """공회전 계단 응답 겹쳐 보기 — 명령 시작을 0 초로 맞추고 rpm 을 그린다(명령별 색). 출처별 한 칸."""
    steps = ad.freespin_steps(s)
    if not steps:
        return False
    tw, wv = ad.wheel_omega(s)
    rpm = wv * 60 / (2 * np.pi)
    srcs = sorted({x['src'] for x in steps})
    fig, axs = plt.subplots(1, len(srcs), figsize=(6.5 * len(srcs), 4.8), squeeze=False)
    for a, src in zip(axs[0], srcs):
        xs = [x for x in steps if x['src'] == src]
        cmds = sorted({x['cmd'] for x in xs})
        cmap = plt.get_cmap('viridis')
        for x in xs:
            i0, i1 = np.searchsorted(tw, x['t0'] - 0.3), np.searchsorted(tw, x['t1'] + 1.0, side='right')
            col = cmap(cmds.index(x['cmd']) / max(len(cmds) - 1, 1))
            a.plot(tw[i0:i1] - x['t0'], rpm[i0:i1], color=col, lw=1.2,
                   label=f'{x["cmd"]:g} {x["unit"]} ({datetime.datetime.fromtimestamp(x["t0"]):%H:%M:%S})')
            a.axvline(x['t1'] - x['t0'], color=col, lw=0.6, ls=':')
        a.axvline(0, color='black', lw=0.8)
        a.set_title(f'{src} — 계단 응답 (점선 = 명령 끝)', fontsize=10)
        a.set_xlabel('명령 시작부터 (s)')
        a.set_ylabel('바퀴 rpm (좌우 평균)')
        a.grid(True, color=C['grid'], lw=0.5)
        a.legend(fontsize=7, loc='best')
    fig.suptitle('뒷바퀴 공회전 — 출력 상한·불감대·지연 확인용 (차량 가감속도 아님)')
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session')
    ap.add_argument('--raw', help='원본 폴더(기본 <session>/raw)')
    ap.add_argument('--dbc', default=ad.DEFAULT_DBC)
    ap.add_argument('--from', dest='t_from', help='HH:MM:SS (녹화 날짜 기준)')
    ap.add_argument('--to', dest='t_to', help='HH:MM:SS')
    ap.add_argument('--tag', help='timeline_<tag>.png 로 저장(구간 확대용)')
    a = ap.parse_args()
    raw = a.raw or os.path.join(a.session, 'raw')
    s = ad.Session(raw, a.dbc)
    if not s.frames:
        print(f'{raw} 에 원본 없음', file=sys.stderr)
        return 2
    t0, t1 = s.frames[0][0], s.frames[-1][0]
    if a.t_from:
        t0 = parse_hms(t0, a.t_from)
    if a.t_to:
        t1 = parse_hms(s.frames[0][0], a.t_to)
    out = os.path.join(a.session, 'plots')
    os.makedirs(out, exist_ok=True)
    tl = os.path.join(out, f'timeline_{a.tag}.png' if a.tag else 'timeline.png')
    timeline(s, raw, t0, t1, tl)
    print(tl, file=sys.stderr)
    if not a.tag:
        lp = os.path.join(out, 'limits.png')
        if limits(s, lp):
            print(lp, file=sys.stderr)
        fp = os.path.join(out, 'freespin.png')
        if freespin(s, fp):
            print(fp, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
