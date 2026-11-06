"""r_race.py 결과 → README.md·rounds.csv·figs/. 판정은 하지 않는다(r_race.py 가 낸 결과를 옮긴다). 다시 만들기: r_race.py --report-only --work <폴더>."""
import csv
import json
import os

MODE_TEXT = {'virtual': 'virtual(같은 프로세스)', 'socketcan': 'socketcan 프로세스'}
COND_TEXT = {'direct': '어댑터 없음(hold_after 0)', 'emu': '어댑터 흉내(hold_after 0.10, 쓰기만 그만큼 늦게 멈춤)', 'ros': '실제 어댑터(hold_after 0.10)'}
GAPS = (0.1, 0.2, 0.3, 0.6, 1.0)
SPEEDS = (30.0, 9.0)
INK, INK2, MUTED, GRID, BASE, SURFACE = '#0b0b0b', '#52514e', '#898781', '#e1e0d9', '#c3c2b7', '#fcfcfb'
SERIES = ('#2a78d6', '#eb6834', '#1baf7a')     # 표준 팔레트 1~3 슬롯, validate_palette.js 통과(대비 WARN → 직접 라벨 + README 표)
STATUS = {'복구': '#0ca30c', '무변화': '#c3c2b7', '정지 확정': '#d03b3b', '미완': '#fab219'}
MARK = {'복구': '✓ 복구', '무변화': '– 무변화', '정지 확정': '■ 정지 확정', '미완': '? 미완'}


def consistency(rounds):
    """id → (라운드별 PASS/FAIL 문자열, 전부 같은가). S14 는 결과 문자열도 비교한다."""
    table = {}
    for r in rounds:
        for row in r['rows']:
            table.setdefault(row['id'], []).append(row)
    out = {}
    for rid, rows in table.items():
        marks = ['PASS' if x['ok'] else 'FAIL' for x in rows]
        same = len(set(marks)) == 1 and len({x.get('res', {}).get('outcome') for x in rows}) == 1
        out[rid] = (marks, same)
    return out


def write_rounds_csv(rounds, path):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['round', 'id', 'ok', 'value'])
        for r in rounds:
            for row in r['rows']:
                w.writerow([r['round'], row['id'], 'PASS' if row['ok'] else 'FAIL', row['value']])


def _setup_mpl():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font = 'DejaVu Sans'
    for p in ('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',):
        if os.path.exists(p):
            font_manager.fontManager.addfont(p)
            font = font_manager.FontProperties(fname=p).get_name()
    plt.rcParams.update({'font.family': font, 'font.size': 8, 'axes.unicode_minus': False, 'axes.linewidth': 0.7, 'axes.edgecolor': BASE,
                         'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.6, 'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE,
                         'legend.frameon': False, 'legend.fontsize': 7.5, 'savefig.dpi': 110, 'savefig.bbox': 'tight', 'lines.linewidth': 1.4})
    return plt


def _read(csv_dir, cid):
    p = os.path.join(csv_dir, f'{cid}.csv')
    if not os.path.exists(p):
        return None
    with open(p, newline='') as f:
        rows = list(csv.DictReader(f))
    return {k: [float(r[k]) if r[k] != '' else float('nan') for r in rows] for k in ('t_s', 'brake_pct', 'acc_pct', 'kph_0x201')}


def _two(plt, title, xlim=None):
    fig, ax = plt.subplots(2, 1, figsize=(6.2, 3.3), sharex=True, gridspec_kw={'height_ratios': [1.25, 1]})
    fig.suptitle(title, x=0.01, ha='left', fontsize=9, color=INK)
    if xlim:
        ax[0].set_xlim(*xlim)
    ax[1].set_xlabel('시나리오 시각 (s)')
    return fig, ax


def _cmd_panel(ax, d):
    ax.step(d['t_s'], d['acc_pct'], where='post', color=SERIES[0], label='가속 명령 %')
    ax.step(d['t_s'], d['brake_pct'], where='post', color=SERIES[1], label='브레이크 명령 %')
    ax.set_ylabel('0x210 %')
    ax.set_ylim(-3, 105)
    ax.legend(loc='center right', ncol=2)


def fig_s02(plt, csv_dir, out, res):
    d = _read(csv_dir, 'S02')
    if d is None:
        return None
    t = res.get('go_confirm_t', 2.0)
    fig, ax = plt.subplots(figsize=(6.2, 2.1))
    ax.set_title(f'S2 Go — 확인 뒤 첫 모델 명령까지 {(res.get("first_cmd_t", t) - t) * 1e3:.1f} ms (개발 PC 참고)', loc='left', fontsize=9, color=INK)
    _cmd_panel(ax, d)
    ax.axvline(t, color=MUTED, lw=0.8, ls='--')
    ax.text(t, 50, ' Go 2번째 프레임', color=INK2, fontsize=7.5)
    ax.set_xlim(t - 0.2, t + 0.2)
    ax.set_xlabel('시나리오 시각 (s)')
    path = os.path.join(out, 'S02_go.png')
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_s05(plt, csv_dir, out):
    series = [('S05', '가짜 차량 기본 표', 0), ('S05m', '실측 표 ★', 1), ('S05d', '실측 표 ★ + 구동 지연 0.7 s', 2)]
    fig, ax = plt.subplots(figsize=(6.2, 2.8))
    ax.set_title('S5 Slow On(12 s) — 0x201 차속. 실제 속도 추정 ★ = ÷0.958', loc='left', fontsize=9, color=INK)
    drawn = False
    for cid, label, i in series:
        d = _read(csv_dir, cid)
        if d is None:
            continue
        drawn = True
        ax.plot(d['t_s'], d['kph_0x201'], color=SERIES[i], label=label)
    if not drawn:
        plt.close(fig)
        return None
    for y, text, c, off, va in ((10.0, '10 규정', '#d03b3b', 0.08, 'bottom'), (9.58, '★ 실제 10 에 해당하는 0x201 = 9.58', MUTED, -0.08, 'top'),
                                (8.0, '목표 8.0', MUTED, 0.08, 'bottom')):
        ax.axhline(y, color=c, lw=0.8, ls='--')
        ax.text(11.1, y + off, text, color=INK2, fontsize=7, ha='left', va=va)
    ax.set_xlim(11, 30)
    ax.set_ylim(5, 14)
    ax.set_ylabel('km/h')
    ax.set_xlabel('시나리오 시각 (s). 12~14.5 s 진입 구간은 14 km/h 위라 잘렸음')
    ax.legend(loc='upper right', ncol=1)
    path = os.path.join(out, 'S05_slow_speed.png')
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_s07(plt, csv_dir, out, res):
    d = _read(csv_dir, 'S07')
    if d is None or 'ramp_t0' not in res:
        return None
    t0, start = float(res['ramp_t0']), float(res['ramp_start'])
    fig, ax = plt.subplots(figsize=(6.2, 2.2))
    ax.set_title('S7 Slow Off — 가속 명령이 램프 상한(잠정 50 %/s)을 따라감', loc='left', fontsize=9, color=INK)
    ax.step(d['t_s'], d['acc_pct'], where='post', color=SERIES[0], label='가속 명령 %')
    ax.plot([t0, t0 + (100 - start) / 50], [start, 100], color=SERIES[1], ls='--', label='상한 램프')
    ax.axvspan(t0, t0 + 1.0, color=GRID, alpha=0.5, lw=0)
    ax.text(t0 + 0.02, 92, '입력 유예 1 s', color=INK2, fontsize=7.5)
    ax.set_xlim(t0 - 0.5, t0 + 3)
    ax.set_ylim(-3, 105)
    ax.set_ylabel('%')
    ax.set_xlabel('시나리오 시각 (s)')
    ax.legend(loc='lower right')
    path = os.path.join(out, 'S07_ramp.png')
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_s09(plt, csv_dir, out, res):
    d = _read(csv_dir, 'S09')
    if d is None:
        return None
    fig, ax = _two(plt, 'S9 RES → 해제 — 풀린 뒤에도 브레이크 유지, 차속이 다시 오르지 않음')
    ax[0].plot(d['t_s'], d['kph_0x201'], color=SERIES[0])
    ax[0].set_ylabel('0x201 km/h')
    _cmd_panel(ax[1], d)
    for a in ax:
        for key, label in (('res_on', 'RES 켜짐'), ('res_off', 'RES 꺼짐')):
            if key in res:
                a.axvline(float(res[key]), color=MUTED, lw=0.8, ls='--')
    if 'res_on' in res:
        ax[0].text(float(res['res_on']), ax[0].get_ylim()[1] * 0.9, ' RES 켜짐', color=INK2, fontsize=7.5)
        ax[0].text(float(res['res_off']), ax[0].get_ylim()[1] * 0.9, ' RES 꺼짐', color=INK2, fontsize=7.5)
    path = os.path.join(out, 'S09_res.png')
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_s14(plt, cells, out):
    """끊김 시간 × 속도 × 결과. 색만으로 읽지 않게 칸마다 글자를 넣었다."""
    conds = [(c, r) for c, r in (('direct', None), ('direct', 20.0), ('ros', None), ('ros', 20.0), ('emu', None))
             if any(x['cond'] == c and x['rmin'] == r for x in cells)]
    if not conds:
        return None
    fig, axes = plt.subplots(len(conds), 1, figsize=(6.4, 0.95 * len(conds) + 0.3), squeeze=False)
    for ax, (c, r) in zip(axes[:, 0], conds):
        ax.set_xlim(0, len(GAPS))
        ax.set_ylim(0, 2)
        ax.grid(False)
        ax.set_xticks([i + 0.5 for i in range(len(GAPS))], [f'{g:g} s' for g in GAPS])
        ax.set_yticks([1.5, 0.5], [f'{s:g} km/h' for s in SPEEDS])
        ax.tick_params(length=0)
        ax.set_title(COND_TEXT[c].split('(')[0] + ', R_min ' + ('모름' if r is None else format(r, 'g') + ' m'), loc='left', fontsize=8, color=INK2)
        for x in cells:
            if x['cond'] != c or x['rmin'] != r:
                continue
            i, j = GAPS.index(x['gap']), SPEEDS.index(x['kph'])
            y = 1 - j
            ax.add_patch(plt.Rectangle((i + 0.02, y + 0.04), 0.96, 0.92, color=STATUS[x['outcome']], alpha=0.28, lw=0))
            ax.text(i + 0.5, y + 0.5, MARK[x['outcome']], ha='center', va='center', fontsize=7.5, color=INK)
        for s in ax.spines.values():
            s.set_visible(False)
    fig.tight_layout(h_pad=0.4)
    path = os.path.join(out, 'S14_gap_matrix.png')
    fig.savefig(path)
    plt.close(fig)
    return path


def _s14_cells(rounds, cons):
    cells = []
    for row in rounds[0]['rows']:
        if row['id'].startswith('S14-') and row.get('res', {}).get('outcome'):
            cells.append({**row['res'], 'id': row['id'], 'ok': row['ok'], 'same': cons[row['id']][1], 'marks': cons[row['id']][0]})
    return cells


def _s14_table(cells):
    lines = ['| 경로 | R_min | 속도 km/h | 끊김 s | 결과 | 예상(recovery.py) | 예산 s | 이탈(이벤트) | 끊김 뒤 STOPPED s | 최저 km/h | 판정 | 3회 일치 |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|']
    order = {'direct': 0, 'emu': 1, 'ros': 2}
    for c in sorted(cells, key=lambda x: (order[x['cond']], x['rmin'] is not None, -x['kph'], x['gap'])):
        t = c.get('t_stop_after_gap')
        rmin = '모름' if c['rmin'] is None else format(c['rmin'], 'g') + ' m'
        stop = '-' if t is None else format(t, '.2f')
        same = '예' if c['same'] else '아니오 ' + '/'.join(c['marks'])
        lines.append(f'| {COND_TEXT[c["cond"]].split("(")[0]} | {rmin} | {c["kph"]:g} | {c["gap"]:g} | **{c["outcome"]}** | '
                     f'{c["predicted"]}{" (경계)" if c["boundary"] else ""} | {c["budget"]:.2f} | {(c["commit"] or c["recover"] or "-")} | '
                     f'{stop} | {c["min_kph"]:.1f} | {"PASS" if c["ok"] else "FAIL"} | {same} |')
    return '\n'.join(lines)


def _s5_table(rows):
    lines = ['| 조건 | 진입 km/h→9.3 이하 도달 s | 도달 뒤 최고 0x201 km/h | 최고 실제 추정 ★ km/h | 10 초과(0x201) | 10 초과(★) | 정상 구간 평균 / 최저 km/h |', '|---|---|---|---|---|---|---|']
    for cid, label in (('S05', '가짜 차량 기본 표(75 % = 2.4 m/s²)'), ('S05m', '실측 표 ★(75 % ≈ 0 m/s²)'), ('S05d', '실측 표 ★ + 구동 지연 0.7 s')):
        r = next((x['res'] for x in rows if x['id'] == cid), None)
        if not r or 't_reach_s' not in r:
            continue
        lines.append(f'| {label} | {r["t_reach_s"]:.2f} | {r["peak_0x201"]:.2f} | {r["peak_true"]:.2f} | {r["n_over"]}회 | {r["n_over_true"]}회 | '
                     f'{r["tail_mean"]:.2f} / {r["tail_min"]:.2f} |')
    return '\n'.join(lines)


def build_report(data, out):
    meta, rounds = data['meta'], data['rounds']
    os.makedirs(out, exist_ok=True)
    figs = os.path.join(out, 'figs')
    csv_dir = os.path.join(out, 'csv')
    os.makedirs(figs, exist_ok=True)
    write_rounds_csv(rounds, os.path.join(out, 'rounds.csv'))
    cons = consistency(rounds)
    first = rounds[0]['rows']
    cells = _s14_cells(rounds, cons)
    res_of = {x['id']: x.get('res', {}) for x in first}

    plt = _setup_mpl()
    fig_paths = [fig_s02(plt, csv_dir, figs, res_of.get('S02', {})), fig_s05(plt, csv_dir, figs), fig_s07(plt, csv_dir, figs, res_of.get('S07', {})),
                 fig_s09(plt, csv_dir, figs, res_of.get('S09', {})), fig_s14(plt, cells, figs)]

    main = [x for x in first if not x['id'].startswith('S14-')]
    mode = MODE_TEXT[meta['interface']]
    groups = {}
    for c in cells:
        groups.setdefault(c['cond'], []).append(c)
    table = ['| ID | 시나리오 | 기준(계획서 WP7 표) | 측정값 | 판정 | 모드 | 커밋 | 비고 |', '|---|---|---|---|---|---|---|---|']
    for row in main:
        m = mode + ('+ROS' if row.get('ros') else '')
        table.append(f'| {row["id"]} | {row["title"]} | {row["criterion"]} | {row["value"]} | **{"PASS" if row["ok"] else "FAIL"}** | {m} | {meta["rev"]} | 개발 PC 참고 |')
    for cond, items in groups.items():
        n_ok = sum(c['ok'] for c in items)
        m = mode + ('+ROS' if cond == 'ros' else '')
        table.append(f'| S14 | 모델 0.1/0.2/0.3/0.6/1.0 s 끊김(고속·저속) — {COND_TEXT[cond]} | 예산 안 = 복구, 밖 = 정지 확정 | {n_ok}/{len(items)} 조합 예상과 일치(아래 표) | '
                     f'**{"PASS" if n_ok == len(items) else "FAIL"}** | {m} | {meta["rev"]} | 개발 PC 참고 |')
    all_rows = [x for x in first]
    n_ok = sum(x['ok'] for x in all_rows)
    fails = [x for x in all_rows if not x['ok']]
    flaky = sorted(k for k, v in cons.items() if not v[1])

    def s14_get(cond, rmin, kph, gap):
        return next((c for c in cells if c['cond'] == cond and c['rmin'] == rmin and c['kph'] == kph and c['gap'] == gap), None)

    default_lines = []
    for cond in ('ros', 'emu', 'direct'):
        for kph in SPEEDS:
            c = s14_get(cond, None, kph, 0.2)
            if c:
                t = c.get('t_stop_after_gap')
                stop = 'STOPPED 없음' if t is None else format(t, '.2f') + ' s 에 STOPPED'
                detail = c['commit'] or c['recover'] or '이벤트 없음'
                default_lines.append(f'- {COND_TEXT[cond]}, {kph:g} km/h: **{c["outcome"]}** ({detail}), 끊김 시작 뒤 {stop}, 시나리오 끝까지 최저 {c["min_kph"]:.1f} km/h, 다시 달리지 못함'
                                     if c['outcome'] == '정지 확정' else f'- {COND_TEXT[cond]}, {kph:g} km/h: {c["outcome"]}')
    rels = [os.path.relpath(p, out) for p in fig_paths if p]
    fig_md = '\n\n'.join(f'![{os.path.basename(p)}]({p})' for p in rels)
    elapsed = ', '.join(f'{r["elapsed_s"]:.0f}' for r in rounds)
    text = f"""# 경기 깃발 SIL — S1~S18 자동 판정 (2026-11-06)

**개발 PC 참고 결과다. 판정은 대회 PC(`--interface socketcan --channel vcan0`)로 다시 한다**(CLAUDE.md). 지연 값은 로직 확인용이다.

도구 `safety/can_guard/sil_tests/r_race.py`, 시나리오 `sil/vcan/race_scenarios/`, 기준 `docs/2026-11-06_race_flag_dev_plan.md` 11절 WP7 표(숫자 그대로). 판정 근거는 버스의 0x210·0x203·0x202 프레임과 can_guard 이벤트 로그다.
시험 코드 커밋 `{meta["rev"]}` · 모드 {mode}{" + 실제 cmd_adapter·race_state_publisher" if meta["with_ros"] else ""} · 라운드 {len(rounds)}회.

## 결과 요약

- 전체 {n_ok}/{len(all_rows)} 통과(1회차 기준){'. 실패: ' + ', '.join(x['id'] for x in fails) if fails else ''}.
- 3회 반복 일치: {'전부 같음' if not flaky else '다름: ' + ', '.join(flaky)}. 회차별 값은 [rounds.csv](rounds.csv).
- 실행 시간: 회차당 {elapsed} s, 전체 {meta["total_s"] / 60:.1f} 분(모델 직결은 동시 4개, ROS 는 하나씩).

## 전체 표

{chr(10).join(table)}

S5·S14 행과 ★ 표시는 아래 절에 따로 있다. `✗` 가 붙은 항목이 실패 항목이다.

## S5 서행 진입 (0x201 기준 / 실제 속도 추정 ★)

11-04 실측에서 0x201 은 위치 추정 속도보다 4.2 % 낮게 읽힌다(비율 0.958, `measurements/2026-11-04_drive_log/README.md` 2.4절). 그래서 "실제 속도 ★ = 0x201 ÷ 0.958" 줄을 함께 판정한다(0x201 9.58 = 실제 10).

{_s5_table(first)}

- 표 출처: 가짜 차량 기본 표는 실측보다 강하다(75 % 에서 2.4 m/s²). 실측 표 ★ = `long_accel_map.csv` 0~10 km/h 순가속 + 타행분, `long_brake_map.csv`, `long_coast.csv` 를 이어 만든 잠정값이고 기울기·슬립은 없다. 지연 0.7 s = 11-04 출발 지연 0.64~0.74 s. 실측 표로는 12 s 안에 30 km/h 를 못 만들어 11.5 s 에 차속을 30 으로 맞췄다(시나리오 조작).
- 진입 감속 `a_slow` 2.0 은 잠정이다. 도달 시간 기준은 대회 답변(질의서 3) 전이라 값만 기록한다.

## S14 끊김 표 (끊김 시간 × 속도 × 결과)

"예산"은 현재 동작(recovery.py) 그대로 쓴다: 끊긴 시간에 신선한 입력 3주기(0.06 s) 확인까지 더해 이탈 d = ½·a_err·(t + 0.25)² 가 0.30 m 를 넘으면 정지 확정. 어댑터 경유면 끊긴 뒤 0.10 s 동안은 어댑터가 계속 기록해 can_guard 가 못 본다(0.05 s 넘게 기록이 멈춰야 보임). 예산 근처 ±0.03 s 는 주기 위상에 따라 갈려 "경계"로 표시하고 어느 쪽이든 인정했다.

{_s14_table(cells)}

**기본값(R_min 모름, hold_after 0.10)에서 0.2 s 끊김의 실제 결과:**

{chr(10).join(default_lines)}

즉 기본값에서는 예산 약 0.20 s 인데 복구 확인에 0.06 s 가 더 걸리고 어댑터가 0.10 s 를 먼저 쓰므로, **can_guard 가 볼 수 있는 끊김은 전부 정지 확정이다**(`test_default_hold_after_makes_any_visible_gap_commit`). 계획서 8.1 표의 "약 0.20 s 복구"가 실제로는 성립하지 않는다. R-07(짧은 끊김으로 불필요하게 서지 않는다)을 살리는 길은 `--track-min-radius-m`(R_min 20 m·9 km/h 면 1.0 s 까지 복구, 표 참고)이다. 기준을 바꾸지 않았고 미결로 올렸다.

## 그래프

{fig_md}

원본(1회차): `csv/<ID>.csv`(0x210 + 차속, 값이 바뀌거나 0.1 s 마다), `csv/<ID>_events.csv`(can_guard 이벤트). S14 는 0.2 s 조합만 남겼다.

## 재현

```bash
python3 safety/can_guard/sil_tests/r_race.py --with-ros                            # 개발 PC, 약 {meta["total_s"] / 60:.0f} 분(colcon build 뒤)
python3 safety/can_guard/sil_tests/r_race.py --only S05,S05m,S05d --rounds 1 --out /tmp/r
python3 safety/can_guard/sil_tests/r_race.py --interface socketcan --channel vcan0 --with-ros   # 대회 PC, 프로세스 모드(vcan* 만 받음)
```

## 이번에 고친 것

- **제품 코드(버그):** `race_signal.py` — 확인 프레임 수를 못 채운 깃발이 흔적 없이 사라져 S3 기준 "이벤트 기록"을 못 맞췄다. 기록 `race_signal_ignored` 추가(1 초에 한 줄, 버린 수 포함). 시험 먼저 추가. `--race` off 는 이 모듈을 불러오지 않는다.
- **시험 도구:** `ros2 run` 은 SIGTERM 을 자식에게 넘기지 않아 노드가 남고, 남은 어댑터가 다음 시나리오에 섞였다(`r_race.py`, `e2e_race_nodes.py` 모두 프로세스 그룹으로 수정). can_guard·어댑터 동작 문제가 아니다.
- **시나리오·판정기 쪽 잘못(제품 문제 아님):** S16 은 0.1 s 보험 송신이 어긋남을 바로 고쳐 어긋남 경로가 돌지 않았다 → 차량이 0.6 s 동안 0x203 을 안 받는 조작을 넣었다. S6 어댑터 경로는 Slow On 전 RACING(유예 밖)에도 40 m/s² 를 내 래치가 났다(그 래치는 D7 대로 맞음) → 큰 가속을 Slow On 확인 뒤로 옮겼다.

## 미결

- 기본값(R_min 모름 + hold_after 0.10)에서 can_guard 가 볼 수 있는 끊김은 전부 영구 정지다(위 표). 0.2 s 끊김도 포함. `--track-min-radius-m` 값(모델 팀 질문 5-1)과 hold_after 를 정해야 R-07 이 의미가 있다. R_min 20 m 여도 30 km/h 는 횡가속 상한에 걸려 그대로다.
- 끊긴 시간 = 명령 나이 + hold_after 는 "어댑터가 입력 끊김으로 기록을 멈췄다"는 가정이다. 어댑터 프로세스 자체가 50 ms 넘게 밀려도(입력은 정상) 같은 계산을 타서 약 0.15 s 끊김으로 잡히고, 확인 3주기까지 더하면 정지 확정이 될 수 있다(계산상. 이 시험에서 직접 재현하지는 않음). 대회 PC 어댑터 주기 지터를 재야 한다.
- S5: 정상 구간 여유는 있으나 진입 끝 또는 지연 조건에서 0x201 9.20~9.30(실제 추정 ★ 9.60~9.71)까지 올라간다. 표는 잠정이고 오르막·슬립이 없다. 실차 서행 시험으로 확인.
- 프로세스 모드(대회 PC 첫 실행), E1~E7·P1·P2.

## 못 돌린 것

- **socketcan 프로세스 모드(`--interface socketcan`): 미실행.** vcan0 이 없다. 인자가 실제 파서를 통과하는지만 `--print-commands` 로 확인했다. 대회 PC 에서 처음 돈다.
- **S18 의 E1~E7·P1·P2 프로세스 실행: 대회 PC 로.** 여기서는 대체했다 — S18(--race off, 깃발·RES 흘림), S18b(11-05 보고서 `scenarios.py` 8개 시나리오 값 열을 그때 결과와 비교), S18c(기존 시험 전체). socketcan 모드에서는 r_race 가 E1~E7·P1·P2 를 직접 돌린다(코드만 작성, 미실행).
- 실차 없음. 차량 모델은 `fake_a1_vehicle.py --physics`(실차와 같다는 뜻이 아님).
"""
    with open(os.path.join(out, 'README.md'), 'w', encoding='utf-8') as f:
        f.write(text)
    with open(os.path.join(out, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
