#!/usr/bin/env python3
"""명령 한계 분석 → limits.md 초안. 제어기 출력 클램프를 정하는 근거 표.

  python3 tools/signal_audit/limits.py <세션폴더> [--raw <원본폴더>]

judge.py 가 같이 부르므로 보통 따로 실행할 필요는 없다. 숫자 정의는 audit_data.py 한곳에 있다.
원격(0x157)으로 얻는 것은 **차량이 낼 수 있는 물리 한계**, 0x210 으로 얻는 것은 **우리 명령값과 반응의 관계표**다 —
제어기는 0x210 으로 나가므로 클램프는 결국 0x210 단위(°, %)로 적는다. m/s² ↔ % 표는 GNSS 가 필요하면
tools/track_day/fit_maps.py 결과를 4절에 붙인다.
"""
import argparse
import datetime
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audit_data as ad  # noqa: E402


def _rng(v):
    v = v[~np.isnan(v)] if len(v) else v
    return (float(v.min()), float(v.max())) if len(v) else None


def render(s):
    out = []
    w = out.append
    w('# 명령 한계 (초안)\n')
    w('> `tools/signal_audit/limits.py` 생성. 숫자는 자동, **"제어기 클램프" 열은 사람이 정한다**. '
      '그림: `plots/limits.png`, `plots/timeline*.png` (`plot.py`).\n')

    # 1. 차량 상태로 본 물리 범위
    w('## 1. 차량이 실제로 낸 범위 (누가 명령했든)\n')
    w('| 항목 | 최소 | 최대 | 비고 |')
    w('|---|---|---|---|')
    r = _rng(s.sig(0x200, 'steer_postion')[1])
    w(f'| 조향 위치 (°, 0x200) | {r[0]:+.1f} | {r[1]:+.1f} | DBC ±200 |' if r else '| 조향 위치 | — | — | 0x200 없음 |')
    t, rate = ad.steer_rate(s)
    if len(rate):
        a = np.abs(rate)
        w(f'| 조향 속도 (°/s) | — | p99 {np.percentile(a, 99):.0f} / 최대 {a.max():.0f} | 0.1 s 차분 — 제어기 변화율 제한 근거 |')
    r = _rng(s.sig(0x200, 'break_postion')[1])
    if r:
        w(f'| 브레이크 위치 (0x200) | {r[0]:.1f} | {r[1]:.1f} | 10.0 = 명령 100 % (10-07) |')
    t, v = ad.wheel_speed(s)
    if len(v):
        w(f'| 바퀴 속도 평균 (km/h, 0x201) | {v.min():.1f} | {v.max():.1f} | 0 뿐이면 0x201 미검증 |')
    t, a = ad.wheel_accel(s)
    if len(a) and np.nanmax(np.abs(a)) > 0:
        w(f'| 바퀴 선가속도 (m/s²) | {np.percentile(a, 0.5):+.2f} (p0.5) | {np.percentile(a, 99.5):+.2f} (p99.5) | '
          f'바퀴 속도 0.5 s 차분. **공회전이면 차량 가속도 아님**(5절) |')
    w('')

    # 2. 명령 → 응답 표
    w(f'## 2. 명령 → 응답 (명령이 {ad.HOLD_S:g} s 이상 그대로였던 정착 상태만, {ad.CMD_SAMPLE_S:g} s 간격 표본)\n')
    sat_rows = []
    for p in ad.pairs(s):
        rows = ad.binned(p['cmd'], p['resp'], ad.bin_width(p['cmd'], p['cmd_unit']))
        w(f'### {p["label"]}\n')
        w(f'명령 관측 범위 {p["cmd"].min():+g} ~ {p["cmd"].max():+g} {p["cmd_unit"]} (표본 {len(p["cmd"])}) · '
          f'명령 한계 {p["dbc"][0]:g} ~ {p["dbc"][1]:g}\n')
        w(f'| 명령 ({p["cmd_unit"]}) | 표본 | 응답 중앙값 ({p["resp_unit"]}) | 응답 최소 | 응답 최대 |')
        w('|---|---|---|---|---|')
        for c, n, med, lo, hi in rows:
            w(f'| {c:+g} | {n} | {med:+.2f} | {lo:+.2f} | {hi:+.2f} |')
        for sign, c, top in ad.saturation(rows) or []:
            edge = max(r[0] * sign for r in rows)
            note = ' (관측 끝 — 더 큰 명령을 안 해 봄, 미확정)' if c * sign >= edge else ''
            sat_rows.append((p['label'], '+' if sign > 0 else '−', f'{c:+g} {p["cmd_unit"]}{note}', top, p['resp_unit']))
        w('')

    # 3. 포화
    w('## 3. 포화 추정 (응답 중앙값이 최대의 95 % 에 처음 닿는 명령)\n')
    w('> 그 명령 위로는 키워도 응답이 거의 안 는다 → 제어기 클램프 후보. 관측 범위 끝이면 "더 큰 명령을 안 해 봤을 뿐"일 수 있다.\n')
    w('| 관계 | 방향 | 포화 시작 명령 | 그때 최대 응답 |')
    w('|---|---|---|---|')
    for lab, sg, c, top, ru in sat_rows:
        w(f'| {lab} | {sg} | {c} | {top:+.2f} {ru} |')
    if not sat_rows:
        w('| (표본 부족) | | | |')
    w('')

    # 4. 제어기 클램프 — 사람이 채움 (5절 공회전은 아래에 붙인다)
    w('## 4. 제어기 클램프 (사람이 정함)\n')
    w('| 제어기 출력 | 보낼 곳 | 클램프 | 변화율 제한 | 근거(위 절·그림·fit_maps) |')
    w('|---|---|---|---|---|')
    w('| 조향 핸들각 (°) | 0x210 steer_command | ± | °/s | |')
    w('| 목표 감속 (m/s²) | 0x210 break_command % | 0 ~ % | | |')
    w('| 목표 가속 (m/s²) | 0x210 acc_command % | 0 ~ % | | |')
    w('')
    out.append(render_freespin(s))
    return '\n'.join(out)


def render_freespin(s):
    """5절 — 뒷바퀴 공회전 계단 응답. 0x201 rpm 이 한 번도 0 을 넘지 않으면 그 사실만 적는다."""
    out = []
    w = out.append
    w('## 5. 공회전 (뒷바퀴 띄움) — 출력 상한·불감대·지연\n')
    w('> 구동계 관성만 돌린 값이라 **차량 가감속도(m/s²)가 아니다**. 여기서 쓰는 것: 최고 속도·최대 각가속도(출력 상한), '
      '불감대(처음 도는 명령), 지연, 응답 모양. m/s² 매칭은 주행(11-04, TRACK_MANUAL T3·T4).\n')
    t, wv = ad.wheel_omega(s)
    if not len(wv) or np.nanmax(wv) <= 0:
        w('0x201 rpm 이 녹화 내내 0 — 바퀴가 안 돌았거나 엔코더가 반응하지 않음. 바퀴가 눈으로 돌았는데 0 이면 0x201 카드 X.\n')
        return '\n'.join(out)
    rpm = wv * 60 / (2 * np.pi)
    _, v = ad.wheel_speed(s)
    _, al = ad.wheel_alpha(s)
    w('| 항목 | 값 | 비고 |')
    w('|---|---|---|')
    w(f'| 최고 바퀴 속도 | {rpm.max():.0f} rpm / {v.max():.1f} km/h | 공회전 상한(무부하) — 도로 최고 속도는 이보다 낮다 |')
    if len(al):
        w(f'| 최대 각가속도 / 각감속도 | {np.percentile(al, 99.5):+.1f} / {np.percentile(al, 0.5):+.1f} rad/s² (p99.5 / p0.5) | rpm 0.5 s 차분 |')
    for side, (d, n) in ad.wheel_diameter(s).items():
        w(f'| 바퀴 지름 역산 ({"왼" if side == "left" else "오른"}) | {d:.1f} cm (표본 {n}) | speed ÷ rpm. 줄자 실측·0x301 설정과 대조 |')
    w('')
    steps = ad.freespin_steps(s)
    if not steps:
        w('명령 계단(0.5 s 이상 유지된 가속·브레이크·원격 ACC_Cmd)이 없다.\n')
        return '\n'.join(out)
    w('### 명령 계단별 응답\n')
    w('> 지연 = 명령 시작 → rpm 이 5 이상 변할 때까지. 모양: **정착** = 끝 1 s 각가속도가 최대의 15 % 미만 '
      '(일정 속도에서 멈춤 → 속도 제어 의심) / **계속 증가** = 끝까지 가속(토크 제어 의심) / 무반응 = 불감대 아래.\n')
    w('| 시각 | 명령 | 유지 (s) | 지연 (s) | 시작 → 끝 rpm | 최고 km/h | 최대 각가속도 (rad/s²) | 끝 1 s 각가속도 | 모양 |')
    w('|---|---|---|---|---|---|---|---|---|')
    for x in steps:
        lat = f'{x["latency_s"]:.2f}' if x['latency_s'] is not None else '—'
        k = 60 / (2 * np.pi)
        w(f'| {datetime.datetime.fromtimestamp(x["t0"]):%H:%M:%S} | {x["src"]} {x["cmd"]:g} {x["unit"]} | '
          f'{x["t1"] - x["t0"]:.1f} | {lat} | {x["w0"] * k:.0f} → {x["w_end"] * k:.0f} | {x["kmh_max"]:.1f} | '
          f'{x["alpha_peak"]:+.1f} | {x["alpha_end"]:+.1f} | {x["shape"]} |')
    w('')
    acc = [x for x in steps if x['src'] == '0x210 가속']
    if acc:
        moved = sorted(x['cmd'] for x in acc if x['shape'] != '무반응')
        still = sorted(x['cmd'] for x in acc if x['shape'] == '무반응')
        lats = [x['latency_s'] for x in acc if x['latency_s'] is not None]
        shapes = sorted({x['shape'] for x in acc if x['shape'] != '무반응'})
        w('### 요약 (0x210 가속)\n')
        w(f'- 불감대: 반응한 최소 명령 {moved[0]:g} % / 무반응 최대 {still[-1]:g} %' if moved and still else
          f'- 불감대: 반응한 최소 명령 {moved[0]:g} %' if moved else '- 불감대: 전부 무반응')
        if lats:
            w(f'- 지연: 중앙값 {np.median(lats):.2f} s (최소 {min(lats):.2f} / 최대 {max(lats):.2f})')
        if shapes:
            w(f'- 응답 모양: {", ".join(shapes)} — "정착"만 나오면 가속 % = 목표 속도일 가능성(업체 V3 확인)')
        w('')
    return '\n'.join(out)


def write(s, session):
    path = os.path.join(session, 'limits.md')
    with open(path, 'w') as f:
        f.write(render(s) + '\n')
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session')
    ap.add_argument('--raw')
    ap.add_argument('--dbc', default=ad.DEFAULT_DBC)
    a = ap.parse_args()
    s = ad.Session(a.raw or os.path.join(a.session, 'raw'), a.dbc)
    if not s.frames:
        print('원본 없음', file=sys.stderr)
        return 2
    os.makedirs(a.session, exist_ok=True)
    print(write(s, a.session), datetime.datetime.now().strftime('%T'), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
