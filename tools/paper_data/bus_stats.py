#!/usr/bin/env python3
"""실차 CAN 원본 → 논문용 수치표 2종 + 그림.

  02_signal_range_table.md : DBC 신호별 정의 범위 vs 실측 최소·최대·분위수(세션별), 정의 밖 비트·DLC 대조, live_counter
  03_bus_timing.md         : 송신(우리)·수신(차량)·제3자(원격) 분리 주기 통계, 실제 비트 스터핑 반영 버스 부하
  fig/fig2_period_*.png    : 주기 분포(히스토그램·ECDF·박스)·시계열

원본이 저장소에 있는 세션(10-07)은 직접 계산하고, 원본이 저장소 밖인 세션(11-03)은 judge.py 가 만든 results.md 표를 읽어
'요약값만' 같이 싣는다. 11-03 원본을 구하면 --session 으로 추가하면 같은 형식으로 모두 계산된다.

    python3 tools/paper_data/bus_stats.py \
        --session 10-07=measurements/2026-10-07_vehicle_lift/capture \
        --summary 11-03=measurements/2026-11-03_signal_audit/results.md \
        --exclude 10-07:0x210:11:03:30-11:09:05 --out reports/2026-11-03_paper_data
"""
import argparse
import datetime
import os
import re
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'tools', 'signal_audit'))
import dbc_lite  # noqa: E402
from audit_data import load_frames  # noqa: E402

BITRATE = 500_000
GAP_S = 0.5                 # 이보다 긴 간격은 버스 끊김(전원·분리) — 주기 통계에서 빼고 따로 센다
ROLE = {0x210: ('송신', '우리 제어 PC (can_guard)'),
        0x200: ('수신', '차량 제어기'), 0x201: ('수신', '차량 제어기'),
        0x156: ('제3자', '대회측 원격 유닛'), 0x157: ('제3자', '대회측 원격 유닛')}
NOMINAL_MS = {0x210: 20.0, 0x200: 21.0, 0x201: 21.0, 0x156: 10.0, 0x157: 10.0}


def hms(t):
    return datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S')


# ── CAN 2.0A 프레임 비트 수 (실제 데이터로 스터핑 계산) ─────────────────────────────
def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def frame_bits(fid, data):
    """표준 데이터 프레임 전체 비트 수(스터핑 포함, IFS 3비트 포함)."""
    bits = [0]                                              # SOF
    bits += [(fid >> (10 - i)) & 1 for i in range(11)]       # ID
    bits += [0, 0, 0]                                        # RTR, IDE, r0
    dlc = len(data)
    bits += [(dlc >> (3 - i)) & 1 for i in range(4)]
    for byte in data:
        bits += [(byte >> (7 - i)) & 1 for i in range(8)]
    c = crc15(bits)
    bits += [(c >> (14 - i)) & 1 for i in range(15)]
    stuff, run, last = 0, 0, None
    for b in bits:
        if b == last:
            run += 1
            if run == 5:
                stuff += 1
                last, run = 1 - b, 1            # 스터프 비트는 반대값, 다음 연속의 시작
                continue
        else:
            last, run = b, 1
    return len(bits) + stuff + 1 + 1 + 1 + 7 + 3, stuff   # + CRC delim, ACK, ACK delim, EOF, IFS


def parse_exclude(specs):
    out = defaultdict(list)
    for s in specs or []:
        sess, fid, rng = s.split(':', 2)
        a, b = rng.split('-')
        out[(sess, int(fid, 16))].append((a, b))
    return out


def in_windows(ts, windows):
    if not windows:
        return np.zeros(len(ts), bool)
    hm = np.array([datetime.datetime.fromtimestamp(t).strftime('%H:%M:%S') for t in ts])
    m = np.zeros(len(ts), bool)
    for a, b in windows:
        m |= (hm >= a) & (hm <= b)
    return m


# ── 세션 계산 ────────────────────────────────────────────────────────────────
def analyse(name, raw, msgs, excl):
    files, frames = load_frames(raw)
    by = defaultdict(list)
    for t, fid, d in frames:
        by[fid].append((t, d))
    t0, t1 = frames[0][0], frames[-1][0]
    res = dict(name=name, raw=raw, files=[os.path.relpath(f, REPO) for f in files], n=len(frames),
               t0=t0, t1=t1, ids={}, sig={}, bits={}, counters={})
    # 주기
    for fid, rows in sorted(by.items()):
        ts = np.array([t for t, _ in rows])
        d = np.diff(ts) * 1000
        gaps = d > GAP_S * 1000
        ex = in_windows(ts[1:], excl.get((name, fid)))
        burst = d < 0.5 * NOMINAL_MS.get(fid, 20.0)      # 재전송·몰림·이중 송신자
        ok = ~gaps & ~ex
        dd = d[ok]
        res['ids'][fid] = dict(n=len(ts), dlc=sorted({len(x) for _, x in rows}), d=dd, ts=ts[1:][ok], gaps=int(gaps.sum()),
                               excluded=int(ex.sum()), burst=int((burst & ok).sum()))
    # 버스 부하: 초 단위
    sec = defaultdict(int)
    stuff_total, bits_total = 0, 0
    per_id_bits = defaultdict(list)
    for t, fid, d in frames:
        nb, st = frame_bits(fid, d)
        sec[int(t)] += nb
        stuff_total += st
        bits_total += nb
        per_id_bits[fid].append(nb)
    loads = np.array([v / BITRATE * 100 for k, v in sorted(sec.items())])
    res['load'] = dict(mean=loads[loads > 0].mean(), p99=np.percentile(loads[loads > 0], 99), max=loads.max(),
                       stuff_ratio=stuff_total / bits_total, per_id={k: (np.mean(v), max(v)) for k, v in per_id_bits.items()},
                       series=loads)
    # 신호 값·정의 밖 비트
    for fid, msg in msgs.items():
        rows = by.get(fid, [])
        used = 0
        for s in msg.signals:
            used |= ((1 << s.length) - 1) << s.start
        if rows:
            bad_bits = sum(1 for _, d in rows if int.from_bytes(bytes(d).ljust(8, b'\0'), 'little') & ~used & ((1 << (8 * len(d))) - 1))
            bad_dlc = sum(1 for _, d in rows if len(d) != msg.dlc)
            res['bits'][fid] = (len(rows), bad_bits, bad_dlc)
        for s in msg.signals:
            if not rows:
                continue
            v = np.array([s.phys(d) for _, d in rows])
            viol = int(((v < s.minimum - 1e-9) | (v > s.maximum + 1e-9)).sum())
            res['sig'][(fid, s.name)] = dict(n=len(v), min=v.min(), max=v.max(), mean=v.mean(),
                                             p1=np.percentile(v, 1), p99=np.percentile(v, 99),
                                             distinct=len(np.unique(v)), viol=viol)
            if 'counter' in s.name or s.name == 'Aliv_Cnt':
                ts = np.array([t for t, _ in rows])
                dv = (np.diff(v) % 256)
                okk = np.diff(ts) < GAP_S
                dv = dv[okk]
                res['counters'][(fid, s.name)] = dict(n=len(dv), inc=(dv == 1).mean() * 100, rep=(dv == 0).mean() * 100,
                                                      skip=((dv > 1)).mean() * 100)
    return res


def read_summary(path):
    """judge.py results.md → {'ids': {fid: (n, median, p99, maxgap)}, 'sig': {(fid, name): (n, min, max, viol)}, 'span'}."""
    txt = open(path).read()
    out = dict(ids={}, sig={}, path=os.path.relpath(path, REPO))
    m = re.search(r'프레임 ([\d,]+), (\d\d:\d\d:\d\d)\.\d+ ~ (\d\d:\d\d:\d\d)', txt)
    if m:
        out['n'], out['span'] = int(m.group(1).replace(',', '')), f'{m.group(2)}~{m.group(3)}'
    for m in re.finditer(r'^\| 0x([0-9A-F]+) \| [^|]+ \| (\d+) \| \d+ \| ([\d.]+) / ([\d.]+) \| ([\d.]+) \|$', txt, re.M):
        out['ids'][int(m.group(1), 16)] = (int(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5)))
    for m in re.finditer(r'^\| 0x([0-9A-F]+) \| `(\w+)` \| (\d+) \| ([-\d.]+) \| ([-\d.]+) \| (\d+) \| (\d+) \|', txt, re.M):
        out['sig'][(int(m.group(1), 16), m.group(2))] = (int(m.group(3)), float(m.group(4)), float(m.group(5)), int(m.group(7)))
    return out


# ── 표 ──────────────────────────────────────────────────────────────────────
def g(x, nd=2):
    if x is None:
        return '—'
    if abs(x - round(x)) < 1e-9:
        return f'{int(round(x))}'
    return f'{x:.{nd}f}'.rstrip('0').rstrip('.')


def write_ranges(out, msgs, sessions, summaries):
    L = []
    w = L.append
    w('# 표 2. DBC 신호 정의 범위 vs 실측 최소·최대\n')
    w('> 생성: `tools/paper_data/bus_stats.py`. DBC = `DBC/A1_dbc_fixed.dbc` + 원격 0x156/0x157(대회측 형식, `dbc_lite.remote_messages`).')
    for s in sessions:
        w(f'> 세션 **{s["name"]}** (원본 직접 계산): {", ".join(s["files"])} — {s["n"]:,} 프레임, '
          f'{hms(s["t0"])}~{hms(s["t1"])}.')
    for name, sm in summaries.items():
        w(f'> 세션 **{name}** (원본 저장소 밖 → `{sm["path"]}` 요약값 인용): {sm.get("n", 0):,} 프레임, {sm.get("span", "")}. '
          f'평균·분위수 없음.')
    w('> 활용률 = 실측 (최대−최소) / DBC (최대−최소). 범위 위반 = DBC [최소, 최대] 밖 프레임 수. "—" = 그 세션에 메시지가 안 보임.\n')
    names = [s['name'] for s in sessions] + list(summaries)
    w('## 1. 신호별 수치표\n')
    head = '| ID | 신호 | 단위 | 비트 | 배율 | DBC 최소 | DBC 최대 |'
    for n in names:
        head += f' {n} 최소 | {n} 최대 |'
    sname0 = sessions[0]['name'] if sessions else None
    head += f' {sname0} 평균 | {sname0} p1 / p99 | 활용률 (전 세션) | 범위 위반 |' if sessions else ''
    w(head)
    w('|' + '---|' * head.count('|')[:-1] if False else '|' + '|'.join(['---'] * (head.count('|') - 1)) + '|')
    for fid, msg in sorted(msgs.items()):
        for sig in msg.signals:
            k = (fid, sig.name)
            row = f'| 0x{fid:03X} | `{sig.name}` | {sig.unit or "—"} | {sig.start}\\|{sig.length}{"s" if sig.signed else ""} | ' \
                  f'{g(sig.factor, 3)} | {g(sig.minimum)} | {g(sig.maximum)} |'
            lo, hi, viol = [], [], 0
            for s in sessions:
                st = s['sig'].get(k)
                row += f' {g(st["min"])} | {g(st["max"])} |' if st else ' — | — |'
                if st:
                    lo.append(st['min']); hi.append(st['max']); viol += st['viol']
            for n, sm in summaries.items():
                st = sm['sig'].get(k)
                if st and st[0] > 0:
                    row += f' {g(st[1])} | {g(st[2])} |'
                    lo.append(st[1]); hi.append(st[2]); viol += st[3]
                else:
                    row += ' — | — |'
            if sessions:
                st = sessions[0]['sig'].get(k)
                row += f' {g(st["mean"])} | {g(st["p1"])} / {g(st["p99"])} |' if st else ' — | — |'
                span = sig.maximum - sig.minimum
                util = (max(hi) - min(lo)) / span * 100 if lo and span else None
                row += f' {g(util, 1) + " %" if util is not None else "—"} | {viol if lo else "—"} |'
            w(row)
    w('')
    w('- 10-07 의 0x210 값에는 11:03:30~11:09:05 미확인 송신자 값(조향 −58.9°, 가속 100 %)이 섞여 있다(송신자 구분 불가). '
      '11-03 의 0x210 은 원격 유닛이 송신한 값이다.')
    w('- 0x157 `ACC_Cmd` 범위 위반 112건 = 원격이 +1.17 m/s² 송신(대회측 문서 상한 +1). 0x201 바퀴 신호는 10-07 내내 0.')
    w('- 활용률이 낮은 신호(브레이크 위치 3.3 %, 바퀴 2.5 %)는 DBC 범위가 실제보다 크게 잡힌 것 — 제어 범위는 04 데이터시트.\n')
    w('## 2. 메시지 단위 형식 대조 (실측 프레임 vs DBC)\n')
    w('| 세션 | ID | 프레임 | DLC 불일치 | 정의 밖 비트가 1 인 프레임 |')
    w('|---|---|---|---|---|')
    for s in sessions:
        for fid, (n, bb, bd) in sorted(s['bits'].items()):
            w(f'| {s["name"]} | 0x{fid:03X} {msgs[fid].name} | {n:,} | {bd} | {bb} |')
    w('')
    w('## 3. live_counter 무결성 (끊김 0.5 s 미만 구간)\n')
    w('| 세션 | ID | 신호 | 간격 수 | +1 (정상) | 반복 | 건너뜀 |')
    w('|---|---|---|---|---|---|---|')
    for s in sessions:
        for (fid, n), c in sorted(s['counters'].items()):
            w(f'| {s["name"]} | 0x{fid:03X} | `{n}` | {c["n"]:,} | {c["inc"]:.1f} % | {c["rep"]:.1f} % | {c["skip"]:.2f} % |')
    w('\n> 11-03 카운터 표는 `measurements/2026-11-03_signal_audit/results.md` §3 (0x200 반복 24~26 % — 차량 전원 직후 재전송 구간 포함).\n')
    open(os.path.join(out, '02_signal_range_table.md'), 'w').write('\n'.join(L))


def write_timing(out, sessions, summaries, msgs, figs):
    L = []
    w = L.append
    w('# 표 3. CAN 메시지 주기 — 송신 / 수신 분리\n')
    w('> 생성: `tools/paper_data/bus_stats.py`. 500 kbit/s classic CAN, Kvaser Leaf v3 장치 타임스탬프. '
      f'간격 > {GAP_S*1000:.0f} ms 는 버스 끊김으로 보고 통계에서 제외(끊김 수 따로). '
      '지터 = 실측 주기 − 공칭 주기. 공칭 = 송신 측 설정값(0x210 can_guard 20 ms, 원격 10 ms) 또는 실측 중앙값에 가까운 정수(차량 21 ms).\n')
    w('**역할 구분**: DBC 에 송수신 노드가 없어(모두 `Vector__XXX`) 실측 송신자로 구분 — 송신 = 우리 제어 PC(0x210), '
      '수신 = 차량 제어기(0x200·0x201), 제3자 = 대회측 원격 유닛(0x156·0x157).\n')
    for s in sessions:
        w(f'## 1. 세션 {s["name"]} — 원본 직접 계산\n')
        w('| 구분 | ID | 이름 | 송신 주체 | 간격 수 | 공칭 ms | 평균 ms | 표준편차 ms | 최소 | p1 | 중앙값 | p99 | p99.9 | 최대 | '
          '공칭 ±5 % 안 | 끊김 | 제외 |')
        w('|' + '|'.join(['---'] * 17) + '|')
        for fid in sorted(s['ids'], key=lambda f: (ROLE.get(f, ('z',))[0], f)):
            st = s['ids'][fid]
            d = st['d']
            if len(d) < 10:
                continue
            role, who = ROLE.get(fid, ('기타', 'DBC 밖 / 미상'))
            nom = NOMINAL_MS.get(fid)
            within = f'{(np.abs(d - nom) <= 0.05 * nom).mean() * 100:.2f} %' if nom else '—'
            name = msgs[fid].name if fid in msgs else 'DBC 밖'
            pc = np.percentile(d, [1, 50, 99, 99.9])
            w(f'| **{role}** | 0x{fid:03X} | {name} | {who} | {len(d):,} | {g(nom) if nom else "—"} | {d.mean():.3f} | {d.std():.3f} | '
              f'{d.min():.2f} | {pc[0]:.2f} | {pc[1]:.2f} | {pc[2]:.2f} | {pc[3]:.2f} | {d.max():.2f} | {within} | {st["gaps"]} | {st["excluded"]} |')
        w('')
        ex = [k for k in s['ids'] if s['ids'][k]['excluded']]
        if ex:
            w(f'- 제외: ' + ', '.join(f'0x{k:03X} {s["ids"][k]["excluded"]:,}간격' for k in ex)
              + ' — 미확인 0x210 송신자가 같이 송신한 구간(11:03:30~11:09:05, `measurements/2026-10-07_vehicle_lift/analysis_report.md`).')
        bursts = [(k, s['ids'][k]['burst']) for k in s['ids'] if s['ids'][k]['burst']]
        if bursts:
            w('- 공칭의 절반 미만 간격(몰림·재전송): ' + ', '.join(f'0x{k:03X} {b}건' for k, b in bursts)
              + ' — 최소값이 0 에 가까운 원인. 10-07 재연결 때 0x210 715개 몰림(analysis_report §2-1).')
        w('')
        ld = s['load']
        w(f'### 버스 부하 (세션 {s["name"]}, 실제 데이터로 비트 스터핑·CRC 계산)\n')
        w('| 항목 | 값 |')
        w('|---|---|')
        w(f'| 1 s 창 평균 부하 | {ld["mean"]:.2f} % |')
        w(f'| 1 s 창 p99 / 최대 | {ld["p99"]:.2f} % / {ld["max"]:.2f} % |')
        w(f'| 스터프 비트 비율 | {ld["stuff_ratio"] * 100:.2f} % |')
        for fid, (mb, xb) in sorted(ld['per_id'].items()):
            if fid in ROLE:
                rate = 1000 / np.median(s['ids'][fid]['d'])
                w(f'| 0x{fid:03X} 프레임 길이 평균 / 최대 · 정상 부하 기여 | {mb:.1f} / {xb} bit · {rate * mb / BITRATE * 100:.2f} % |')
        w('')
    for name, sm in summaries.items():
        w(f'## 2. 세션 {name} — 요약값 (원본 저장소 밖, `{sm["path"]}`)\n')
        w('| 구분 | ID | 개수 | 주기 중앙값 ms | p99 ms | 최대 공백 s |')
        w('|---|---|---|---|---|---|')
        for fid, (n, med, p99, gap) in sorted(sm['ids'].items()):
            role = ROLE.get(fid, ('기타 (DBC 밖)',))[0]
            if fid == 0x210:
                role = '송신* (원격 유닛)'
            w(f'| {role} | 0x{fid:03X} | {n:,} | {med} | {p99} | {gap} |')
        w('\n\\* 11-03 녹화 구간(15:56~16:33)의 0x210 은 **원격 유닛이 직접 송신**(0x156/0x157 아님, `measurements/2026-11-03_signal_audit/README.md`). '
          '우리 can_guard 0x210 은 2단계(21:41~, `raw/l4g`·`l4h`) 원본이 필요 — HANDOFF 참조.\n')
    w('## 3. 그림\n')
    for f, cap in figs:
        w(f'![{cap}](fig/{f})\n\n*{cap}*\n')
    w('## 4. 해석 메모 (논문 본문용 초안)\n')
    w('- 차량 상태(0x200·0x201)의 실제 주기는 **21 ms(≈47.6 Hz)** 로 문서상 20 ms 와 다르다. 제어기가 보는 상태 나이는 최대 약 22 ms.')
    w('- 우리 송신 0x210 은 비격리·비RT 조건(10-07)에서도 공칭 20 ms 에 대해 p99.9 가 1 ms 안. 대회 운용 설정(격리 코어·FIFO) 값은 미측정.')
    w('- 버스 부하는 10 % 안팎으로 여유가 크다 → 병목은 버스가 아니라 액추에이터 무반응 시간(조향 ≈ 0.2 s, 04 데이터시트).')
    open(os.path.join(out, '03_bus_timing.md'), 'w').write('\n'.join(L))


# ── 그림 ─────────────────────────────────────────────────────────────────────
def figures(out, sessions, summaries, msgs):
    from _style import plt, C
    figs = []
    s = sessions[0]
    order = [f for f in (0x210, 0x200, 0x201, 0x156, 0x157) if f in s['ids'] and len(s['ids'][f]['d'])]
    col = {0x210: C['tx'], 0x200: C['rx'], 0x201: C['rx2'], 0x156: C['remote'], 0x157: '#7FC97F'}
    lab = lambda f: f'0x{f:03X} {ROLE[f][0]} ({ROLE[f][1]})'

    # (a) 송신/수신 히스토그램 — 공칭 근처 확대
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    for a, ids, title, rng in ((ax[0], [0x210], '송신 (우리 → 차량)', (18.5, 22.5)),
                               (ax[1], [0x200, 0x201], '수신 (차량 → 우리)', (19.5, 22.5)),
                               (ax[2], [0x156, 0x157], '제3자 (원격 유닛)', (8.5, 11.5))):
        for f in ids:
            if f not in s['ids']:
                continue
            d = s['ids'][f]['d']
            a.hist(d[(d > rng[0]) & (d < rng[1])], bins=120, range=rng, histtype='stepfilled', alpha=0.55, color=col[f],
                   label=f'0x{f:03X} (n={len(d):,})', log=True)
            a.axvline(np.median(d), color=col[f], ls='--', lw=1)
        a.set_title(title, fontsize=10)
        a.set_xlabel('메시지 간격 (ms)')
        a.legend(fontsize=7)
    ax[0].set_ylabel('빈도 (log)')
    fig.suptitle(f'그림 2a. 메시지 주기 분포 — 세션 {s["name"]} (점선 = 중앙값)', fontsize=10, x=0.01, ha='left')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'fig', 'fig2a_period_hist.png'), dpi=200)
    figs.append(('fig2a_period_hist.png', f'그림 2a. 송신·수신·제3자 메시지 주기 분포 (세션 {s["name"]})'))
    plt.close(fig)

    # (b) 지터 ECDF (주기 − 공칭), 꼬리 강조
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    for f in order:
        d = s['ids'][f]['d'] - NOMINAL_MS[f]
        x = np.maximum(np.sort(np.abs(d)), 0.01)
        y = 1 - np.arange(len(x)) / len(x)
        ax.step(x, y, where='post', color=col[f], label=lab(f), lw=1.3)
    ax.set_yscale('log')
    ax.set_xscale('log')
    ax.set_xlim(0.01, 30)
    ax.annotate('0x210 몰림 송신(재연결)\n707건 → |지터| ≈ 20 ms', xy=(18, 6e-3), xytext=(1.2, 0.25), fontsize=7,
                arrowprops=dict(arrowstyle='->', lw=0.6))
    ax.set_xlabel('|주기 − 공칭| (ms)')
    ax.set_ylabel('초과 확률 P(|지터| > x)')
    ax.legend(fontsize=7, loc='lower left')
    ax.set_title(f'그림 2b. 주기 지터 꼬리 분포 (CCDF) — 세션 {s["name"]}', fontsize=10, loc='left')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'fig', 'fig2b_jitter_ccdf.png'), dpi=200)
    figs.append(('fig2b_jitter_ccdf.png', '그림 2b. 공칭 주기 대비 지터의 초과 확률 (송신 = 주황, 수신 = 파랑, 원격 = 초록)'))
    plt.close(fig)

    # (c) 시계열 — 주기 vs 시각
    fig, ax = plt.subplots(len(order), 1, figsize=(11, 1.5 * len(order) + 0.6), sharex=True)
    for a, f in zip(ax, order):
        st = s['ids'][f]
        tt = [datetime.datetime.fromtimestamp(t) for t in st['ts']]
        a.plot(tt, st['d'], ',', color=col[f], alpha=0.6, rasterized=True)
        a.set_ylim(NOMINAL_MS[f] * 0.75, NOMINAL_MS[f] * 1.25)
        a.axhline(NOMINAL_MS[f], color=C['ref'], lw=0.6, ls=':')
        a.set_ylabel(f'0x{f:03X}\n{ROLE[f][0]} ms', fontsize=8)
    import matplotlib.dates as mdates
    ax[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax[0].set_title(f'그림 2c. 메시지 주기 시계열 — 세션 {s["name"]} (±25 % 창, 끊김·제외 구간 빈칸)', fontsize=10, loc='left')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'fig', 'fig2c_period_timeseries.png'), dpi=160)
    figs.append(('fig2c_period_timeseries.png', '그림 2c. 시간에 따른 메시지 주기 (공칭 점선)'))
    plt.close(fig)

    # (d) 세션 비교 — 중앙값·p99 막대
    fig, ax = plt.subplots(figsize=(8, 3.4))
    allids = sorted({f for x in sessions for f in x['ids'] if len(x['ids'][f]['d']) > 10} |
                    {f for sm in summaries.values() for f in sm['ids']})
    names = [x['name'] for x in sessions] + list(summaries)
    wdt = 0.8 / len(names)
    for k, n in enumerate(names):
        med, p99 = [], []
        for f in allids:
            if k < len(sessions):
                st = sessions[k]['ids'].get(f)
                if st and len(st['d']) > 10:
                    med.append(np.median(st['d'])); p99.append(np.percentile(st['d'], 99))
                else:
                    med.append(np.nan); p99.append(np.nan)
            else:
                v = summaries[n]['ids'].get(f)
                med.append(v[1] if v else np.nan); p99.append(v[2] if v else np.nan)
        x = np.arange(len(allids)) + k * wdt
        bars = ax.bar(x, med, wdt * 0.9, label=f'{n} 중앙값', alpha=0.85,
                      color=[col.get(f, C['other']) for f in allids], hatch=None if k == 0 else '//', edgecolor='white')
        ax.errorbar(x, med, yerr=[np.zeros(len(med)), np.array(p99) - np.array(med)], fmt='none', ecolor='k', capsize=2, lw=0.8)
    ax.set_xticks(np.arange(len(allids)) + wdt * (len(names) - 1) / 2)
    ax.set_xticklabels([f'0x{f:03X}\n{ROLE.get(f, ("DBC 밖",))[0]}' for f in allids], fontsize=8)
    ax.set_ylabel('주기 (ms) — 막대 중앙값, 오차선 p99')
    ax.set_title('그림 2d. 세션별 주기 (민무늬 = ' + names[0] + (', 빗금 = ' + ', '.join(names[1:]) if len(names) > 1 else '') + ')',
                 fontsize=10, loc='left')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'fig', 'fig2d_period_sessions.png'), dpi=200)
    figs.append(('fig2d_period_sessions.png', '그림 2d. 세션별 ID 주기 중앙값·p99 (11-03 의 0x210 송신자는 원격 유닛)'))
    plt.close(fig)

    # (e) 버스 부하 시계열
    fig, ax = plt.subplots(figsize=(8, 2.6))
    ld = s['load']['series']
    ax.plot(ld, color=C['rx'], lw=0.6)
    ax.set_xlabel(f'경과 시간 (s, 세션 {s["name"]})')
    ax.set_ylabel('버스 부하 (%)')
    ax.set_title(f'그림 2e. 1 s 창 버스 부하 (평균 {s["load"]["mean"]:.1f} %, 최대 {s["load"]["max"]:.1f} %)', fontsize=10, loc='left')
    fig.tight_layout()
    fig.savefig(os.path.join(out, 'fig', 'fig2e_bus_load.png'), dpi=200)
    figs.append(('fig2e_bus_load.png', '그림 2e. 실제 프레임(스터핑 포함)으로 계산한 버스 부하'))
    plt.close(fig)
    return figs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--session', action='append', default=[], help='이름=원본 폴더')
    ap.add_argument('--summary', action='append', default=[], help='이름=results.md')
    ap.add_argument('--exclude', action='append', default=[], help='세션:0xID:HH:MM:SS-HH:MM:SS')
    ap.add_argument('--dbc', default=os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc'))
    ap.add_argument('--out', default=os.path.join(REPO, 'docs', 'paper_data'))
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, 'fig'), exist_ok=True)
    msgs = dbc_lite.load(a.dbc)
    msgs.update(dbc_lite.remote_messages())
    excl = parse_exclude(a.exclude)
    sessions = [analyse(n, os.path.join(REPO, p), msgs, excl) for n, p in (x.split('=', 1) for x in a.session)]
    summaries = {n: read_summary(os.path.join(REPO, p)) for n, p in (x.split('=', 1) for x in a.summary)}
    write_ranges(a.out, msgs, sessions, summaries)
    figs = figures(a.out, sessions, summaries, msgs) if sessions else []
    write_timing(a.out, sessions, summaries, msgs, figs)
    for s in sessions:
        print(s['name'], s['n'], 'load', round(s['load']['mean'], 2), {f'0x{k:03X}': (len(v['d']), round(float(np.median(v['d'])), 2) if len(v['d']) else None) for k, v in s['ids'].items()})


if __name__ == '__main__':
    main()
