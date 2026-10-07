#!/usr/bin/env python3
"""실차 녹화 폴더 → 텍스트 요약. 집에 와서 로그를 처음 열어 볼 때 쓰는 도구.

  python3 tools/race_day/summarize_session.py ~/a1_race_capture/vehicle_20261007

입력(폴더 안에서 있는 것만 사용):
  kvaser_ch0*.log / candump-*.log   candump -L 형식 CAN 원본(여러 개면 합치고 중복 제거)
  notes.txt                         `m` 메모(시각 메모)
  lift_cmd_*.log, home_logs/        lift_cmd 입력 이력(우리가 보낸 명령의 근거)
출력 <폴더>/summary/:
  summary.md     한 장 요약 — 기간, ID별 개수·주기·공백, DBC 밖 ID, 카운터 연속성, 0x210 내용 변화 묶음, 바퀴 회전 구간
  events.md      시각순 사건 일지 — 메모 + lift_cmd 입력 + 0x210 내용 변화 + 0x200 auto/위치 변화 + 버스 공백 + 새 ID 등장
  timeline.csv   100 ms 간격 표 — 명령(0x210)·상태(0x200)·바퀴(0x201)·메모 (제어 모델·그래프용)

해석은 DBC/A1_dbc_fixed.dbc 와 같은 규칙(tools/race_day/a1_proto.py). 0x210 은 송신자를 구분할 수 없으므로(우리 can_guard 의
에코와 외부 송신자가 같은 ID) 내용 변화로만 보여 주고, lift_cmd 입력 이력과 나란히 놓아 사람이 대조하게 한다.

0x156/0x157 = 대회측 원격조종 메시지(EAIT_Control_01/02 형식, 2026-10-07 대회측 확인). 0x210 보다 낮은 ID 라 버스 중재에서
이기고, 차량도 0x156 의 EPS_En/ACC_En 이 1 이면 원격 명령을 우선한다(10-07 녹화: En=1 → 차량 auto 111 이 약 20 ms 안에,
0x157 ACC_Cmd 음수 → 브레이크 위치 상승). 신호 배치는 git 기록의 EAIT DBC 와 같다:
  0x156 bit0 EPS_En, bit8-15 EPS_Speed, bit16 ACC_En, bit22 AEB_En, bit56-63 Aliv_Cnt
  0x157 bit0-15 EPS_Cmd int16 ×0.1 deg, bit24-39 ACC_Cmd uint16 ×0.01 −10.23 m/s²
"""
import csv
import datetime
import glob
import os
import re
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from a1_proto import decode_0x200, decode_0x201, decode_0x210  # noqa: E402

LINE = re.compile(r'\((\d+\.\d+)\) \S+ ([0-9A-Fa-f]+)#([0-9A-Fa-f]*)')
A1_IDS = {0x200: 'USER_control_info', 0x201: 'USER_right_wheel_info', 0x210: 'USER_control_command'}
KNOWN_OTHER = {0x156: '원격조종 Control_01(EPS/ACC/AEB En)', 0x157: '원격조종 Control_02(조향·가감속 명령)',
               0x004: '미확인(9/17 로그에 있던 ID)', 0x204: '미확인(9/17 로그에 있던 ID)'}


def hms(t, ms=True):
    d = datetime.datetime.fromtimestamp(t)
    return d.strftime('%H:%M:%S.%f')[:-3] if ms else d.strftime('%H:%M:%S')


def load_frames(folder):
    """모든 candump -L 로그를 읽어 (t, id, data) 로 합친다. 같은 (t, id, data) 는 한 번만(2차 녹화는 1차의 복사본)."""
    # 1차 원본(미러 --log, Kvaser 하드웨어 시각)이 있으면 그것만 쓴다. 2차 candump 복사본은 같은 프레임이지만 수신 시각이
    # 미세하게 달라 (t, id, data) 로는 중복이 안 걸러진다(2026-10-07 첫 실행에서 2배 집계). 1차가 없을 때만 candump 사용.
    files = sorted(glob.glob(os.path.join(folder, 'kvaser_ch0*.log'))) or sorted(glob.glob(os.path.join(folder, 'candump-*.log')))
    seen = set()
    frames = []
    for p in files:
        with open(p, errors='replace') as f:
            for line in f:
                m = LINE.match(line)
                if not m:
                    continue
                key = (m.group(1), m.group(2).upper(), m.group(3).upper())
                if key in seen:
                    continue
                seen.add(key)
                frames.append((float(m.group(1)), int(m.group(2), 16), bytes.fromhex(m.group(3))))
    frames.sort(key=lambda x: x[0])
    return files, frames


def load_notes(folder):
    out = []
    p = os.path.join(folder, 'notes.txt')
    if os.path.exists(p):
        for line in open(p, errors='replace'):
            m = re.match(r'(\d+\.\d+) \d\d:\d\d:\d\d (.*)', line.strip())
            if m:
                out.append((float(m.group(1)), 'memo', m.group(2)))
    return out


def load_lift_cmd(folder):
    out = []
    for p in glob.glob(os.path.join(folder, 'lift_cmd_*.log')) + glob.glob(os.path.join(folder, 'home_logs', 'lift_cmd_*.log')):
        for line in open(p, errors='replace'):
            m = re.match(r'(\d+\.\d+) \d\d:\d\d:\d\d (.*)', line.strip())
            if m:
                out.append((float(m.group(1)), 'lift_cmd', m.group(2)))
    # 같은 파일이 home_logs 에도 복사돼 있을 수 있음 → 중복 제거
    return sorted(set(out))


def runs(frames, fid):
    """fid 프레임들의 '내용 변화 묶음': 같은 데이터가 이어지는 구간 [(t0, t1, n, data)]."""
    out = []
    for t, x, d in frames:
        if x != fid:
            continue
        if out and out[-1][3] == d and t - out[-1][1] < 1.0:
            out[-1][1] = t
            out[-1][2] += 1
        else:
            out.append([t, t, 1, d])
    return out


def decode_remote_156(d):
    d = bytes(d)
    return {'eps_en': d[0] & 1, 'acc_en': d[2] & 1, 'aeb_en': (d[2] >> 6) & 1, 'eps_speed': d[1], 'cnt': d[7]}


def decode_remote_157(d):
    d = bytes(d)
    return {'eps_cmd_deg': round(int.from_bytes(d[0:2], 'little', signed=True) * 0.1, 1),
            'acc_cmd_mps2': round(int.from_bytes(d[3:5], 'little') * 0.01 - 10.23, 2)}


def fmt_210(d):
    c = decode_0x210(d)
    return f"조향 {c['steer_deg']:+.1f}° 브레이크 {c['brake_pct']} 가속 {c['acc_pct']} auto {c['steer_auto']}{c['brake_auto']}{c['acc_auto']}"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    folder = os.path.abspath(sys.argv[1])
    out_dir = os.path.join(folder, 'summary')
    os.makedirs(out_dir, exist_ok=True)
    files, fr = load_frames(folder)
    if not fr:
        sys.exit('CAN 로그를 못 읽음')
    notes = load_notes(folder)
    lcmd = load_lift_cmd(folder)
    t0, t1 = fr[0][0], fr[-1][0]
    by_id = defaultdict(list)
    for t, fid, d in fr:
        by_id[fid].append((t, d))

    S = []
    S.append(f"# 실차 녹화 요약 — {os.path.basename(folder)}\n")
    S.append(f"- 기간: **{hms(t0)} ~ {hms(t1)}** ({(t1 - t0) / 60:.1f}분), 프레임 {len(fr):,}개 (중복 제거 후)")
    S.append(f"- 원본 파일: {', '.join(os.path.basename(p) for p in files)}")
    S.append(f"- 메모 {len(notes)}건, lift_cmd 입력 {len(lcmd)}건\n")

    S.append("## ID 별 (주기는 중앙값, 공백은 1초 이상 끊긴 횟수)\n")
    S.append("| ID | 이름 | 개수 | 주기 | p99 | 공백 | 처음 | 마지막 |")
    S.append("|---|---|---|---|---|---|---|---|")
    for fid in sorted(by_id):
        ts = [t for t, _ in by_id[fid]]
        d = [b - a for a, b in zip(ts, ts[1:])]
        gaps = sum(1 for x in d if x > 1.0)
        dd = [x for x in d if x <= 1.0]
        med = statistics.median(dd) * 1000 if dd else 0
        p99 = sorted(dd)[int(len(dd) * .99)] * 1000 if dd else 0
        name = A1_IDS.get(fid) or KNOWN_OTHER.get(fid) or '**DBC 에 없음**'
        S.append(f"| 0x{fid:03X} | {name} | {len(ts):,} | {med:.1f} ms | {p99:.1f} ms | {gaps} | {hms(ts[0], False)} | {hms(ts[-1], False)} |")
    S.append("")

    # 버스 공백(모든 ID 기준)
    all_ts = [t for t, _, _ in fr]
    bus_gaps = [(a, b) for a, b in zip(all_ts, all_ts[1:]) if b - a > 1.0]
    S.append("## 버스 전체 공백 (1초 이상 아무 프레임 없음)\n")
    S.extend([f"- {hms(a)} → {hms(b)} ({b - a:.1f}초)" for a, b in bus_gaps] or ["- 없음"])
    S.append("")

    # 카운터 연속성
    S.append("## 카운터 연속성 (0x200 축별 3개, 0x201 2개)\n")
    S.append("| 카운터 | 값 종류 | 같은 값 반복 | 하나 건너뜀 | 기타 불연속 |")
    S.append("|---|---|---|---|---|")
    for fid, idx, name in ((0x200, 5, 'steer_live_counter'), (0x200, 6, 'break_live_counter'), (0x200, 7, 'acc_live_counter'),
                           (0x201, 6, 'right_live_counter'), (0x201, 7, 'left_live_counter')):
        seq = [d[idx] for _, d in by_id.get(fid, [])]
        if not seq:
            continue
        diffs = Counter((b - (a + 1)) % 256 for a, b in zip(seq, seq[1:]) if b != (a + 1) % 256)
        rep, skip = diffs.pop(255, 0), diffs.pop(1, 0)
        other = sum(diffs.values())
        flag = '  ← **고정(증가 안 함)**' if len(set(seq)) == 1 else ''
        S.append(f"| 0x{fid:03X} {name} | {len(set(seq))} | {rep} | {skip} | {other}{flag} |")
    S.append("")

    # 0x200 상태 범위
    if 0x200 in by_id:
        dec = [decode_0x200(d) for _, d in by_id[0x200]]
        S.append("## 차량 상태(0x200) 범위\n")
        S.append(f"- 조향 위치: {min(x['steer_pos_deg'] for x in dec):+.1f} ~ {max(x['steer_pos_deg'] for x in dec):+.1f} °")
        S.append(f"- 브레이크 위치: {min(x['brake_pos'] for x in dec):.1f} ~ {max(x['brake_pos'] for x in dec):.1f}")
        autos = Counter(f"{x['steer_auto']}{x['brake_auto']}{x['acc_auto']}" for x in dec)
        S.append("- auto 비트(S/B/A) 분포: " + ', '.join(f"{k}: {v:,}" for k, v in autos.most_common()))
        S.append("")
    if 0x201 in by_id:
        dec = [(t, decode_0x201(d)) for t, d in by_id[0x201]]
        mx = max(max(x['right_kph'], x['left_kph']) for _, x in dec)
        S.append("## 바퀴(0x201)\n")
        S.append(f"- 최대 속도 {mx:.1f} km/h, 최대 rpm {max(max(x['right_rpm'], x['left_rpm']) for _, x in dec)}")
        spans = []
        for t, x in dec:
            moving = x['right_kph'] > 0 or x['left_kph'] > 0
            if moving:
                if spans and t - spans[-1][1] < 1.0:
                    spans[-1][1] = t
                else:
                    spans.append([t, t])
        S.append(f"- 회전 구간(속도>0): {len(spans)}개" + (''.join(f"\n  - {hms(a)} ~ {hms(b)}" for a, b in spans[:20]) if spans else ' — **한 번도 회전 안 함**'))
        S.append("")

    # 0x210 내용 변화 묶음
    S.append("## 명령(0x210) 내용 변화 묶음 — 송신자 구분 없음(우리 에코 + 외부). lift_cmd 입력과 대조할 것\n")
    r210 = runs(fr, 0x210)
    S.append(f"- 총 {len(by_id.get(0x210, [])):,}프레임, 내용 묶음 {len(r210)}개. 1초 이상 이어진 묶음만 아래에 (짧은 변화는 events.md)\n")
    S.append("| 시작 | 끝 | 프레임 | 내용 |")
    S.append("|---|---|---|---|")
    for a, b, n, d in r210:
        if b - a >= 1.0:
            S.append(f"| {hms(a, False)} | {hms(b, False)} | {n} | {fmt_210(d)} |")
    S.append("")

    # 원격조종(0x156/0x157)
    if 0x156 in by_id:
        S.append("## 원격조종(0x156/0x157) — 대회측 원격 메시지, 0x210 보다 우선\n")
        en_runs = []
        for t, d in by_id[0x156]:
            x = decode_remote_156(d)
            on = (x['eps_en'], x['acc_en'], x['aeb_en']) != (0, 0, 0)
            if on:
                if en_runs and t - en_runs[-1][1] < 0.5:
                    en_runs[-1][1] = t
                else:
                    en_runs.append([t, t, x])
        S.append(f"- 활성(En=1) 구간 {len(en_runs)}개" + (' — 활성 없음' if not en_runs else ''))
        for a, b, x in en_runs:
            acc = [decode_remote_157(d)['acc_cmd_mps2'] for t, d in by_id.get(0x157, []) if a <= t <= b + 0.1]
            S.append(f"  - {hms(a)} ~ {hms(b)} ({b - a:.1f}s) EPS_En {x['eps_en']} ACC_En {x['acc_en']} AEB_En {x['aeb_en']}"
                     + (f", ACC_Cmd {min(acc):+.2f} ~ {max(acc):+.2f} m/s²" if acc else ''))
        S.append("")

    # 알 수 없는/기타 ID 의 데이터 다양성
    others = [fid for fid in by_id if fid not in A1_IDS]
    if others:
        S.append("## A1 DBC 밖 ID\n")
        for fid in sorted(others):
            vals = Counter(d for _, d in by_id[fid])
            S.append(f"- 0x{fid:03X} ({KNOWN_OTHER.get(fid, '미확인')}): {len(by_id[fid]):,}개, 데이터 종류 {len(vals)}, 최빈 `{vals.most_common(1)[0][0].hex()}`")
        S.append("")
    open(os.path.join(out_dir, 'summary.md'), 'w').write('\n'.join(S))

    # ---- events.md ----
    ev = list(notes) + list(lcmd)
    for a, b, n, d in r210:
        ev.append((a, '0x210', f"내용 변화 → {fmt_210(d)} ({n}프레임, {b - a:.1f}s)"))
    prev = None
    for t, d in by_id.get(0x200, []):
        x = decode_0x200(d)
        key = (x['steer_auto'], x['brake_auto'], x['acc_auto'])
        if prev is not None and key != prev:
            ev.append((t, '0x200', f"차량 auto 비트 {prev[0]}{prev[1]}{prev[2]} → {key[0]}{key[1]}{key[2]}"))
        prev = key
    # 브레이크/조향 위치 큰 변화(1.0 이상)
    last = None
    for t, d in by_id.get(0x200, []):
        x = decode_0x200(d)
        if last and abs(x['brake_pos'] - last['brake_pos']) >= 1.0:
            ev.append((t, '0x200', f"브레이크 위치 {last['brake_pos']:.1f} → {x['brake_pos']:.1f}"))
        if last and abs(x['steer_pos_deg'] - last['steer_pos_deg']) >= 1.0:
            ev.append((t, '0x200', f"조향 위치 {last['steer_pos_deg']:+.1f} → {x['steer_pos_deg']:+.1f} °"))
        last = x
    prev = None
    for t, d in by_id.get(0x156, []):
        x = decode_remote_156(d)
        key = (x['eps_en'], x['acc_en'], x['aeb_en'])
        if prev is not None and key != prev:
            ev.append((t, '원격', f"0x156 En(EPS/ACC/AEB) {prev[0]}{prev[1]}{prev[2]} → {key[0]}{key[1]}{key[2]}"))
        prev = key
    prev = None
    for t, d in by_id.get(0x157, []):
        x = decode_remote_157(d)
        key = (x['eps_cmd_deg'], x['acc_cmd_mps2'])
        if prev is not None and key != prev:
            ev.append((t, '원격', f"0x157 조향 {x['eps_cmd_deg']:+.1f}° 가감속 {x['acc_cmd_mps2']:+.2f} m/s²"))
        prev = key
    for a, b in bus_gaps:
        ev.append((a, 'bus', f"버스 공백 시작 ({b - a:.1f}초, {hms(b)} 까지)"))
    for fid, lst in by_id.items():
        ev.append((lst[0][0], 'bus', f"0x{fid:03X} 첫 등장"))
    ev.sort(key=lambda e: e[0])
    E = [f"# 사건 일지 — {os.path.basename(folder)}\n", "| 시각 | 출처 | 내용 |", "|---|---|---|"]
    E += [f"| {hms(t)} | {src} | {txt.replace('|', '/')} |" for t, src, txt in ev]
    open(os.path.join(out_dir, 'events.md'), 'w').write('\n'.join(E))

    # ---- timeline.csv (100 ms) ----
    step = 0.1
    note_at = defaultdict(list)
    for t, src, txt in list(notes) + list(lcmd):
        note_at[int((t - t0) / step)].append(f"[{src}] {txt}")
    cols = ['time', 'clock', 'cmd_steer_deg', 'cmd_brake_pct', 'cmd_acc_pct', 'cmd_auto_sba',
            'remote_en_eps_acc_aeb', 'remote_steer_deg', 'remote_acc_mps2',
            'veh_steer_pos_deg', 'veh_brake_pos', 'veh_auto_sba', 'veh_brake_cnt_moving',
            'wheel_r_kph', 'wheel_r_rpm', 'wheel_l_kph', 'wheel_l_rpm', 'notes']
    with open(os.path.join(out_dir, 'timeline.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(cols)
        cur = {}
        i = 0
        bcnt_prev = None
        bcnt_moving = ''
        bucket = int((fr[0][0] - t0) / step)
        for t, fid, d in fr + [(t1 + step, -1, b'')]:
            bk = int((t - t0) / step)
            while bucket < bk:
                c210 = cur.get(0x210, {})
                c200 = cur.get(0x200, {})
                c201 = cur.get(0x201, {})
                c156 = cur.get(0x156, {})
                c157 = cur.get(0x157, {})
                w.writerow([f"{t0 + bucket * step:.1f}", hms(t0 + bucket * step),
                            c210.get('steer_deg', ''), c210.get('brake_pct', ''), c210.get('acc_pct', ''),
                            f"{c210['steer_auto']}{c210['brake_auto']}{c210['acc_auto']}" if c210 else '',
                            f"{c156['eps_en']}{c156['acc_en']}{c156['aeb_en']}" if c156 else '',
                            c157.get('eps_cmd_deg', ''), c157.get('acc_cmd_mps2', ''),
                            c200.get('steer_pos_deg', ''), c200.get('brake_pos', ''),
                            f"{c200['steer_auto']}{c200['brake_auto']}{c200['acc_auto']}" if c200 else '', bcnt_moving,
                            c201.get('right_kph', ''), c201.get('right_rpm', ''), c201.get('left_kph', ''), c201.get('left_rpm', ''),
                            ' ; '.join(note_at.get(bucket, []))])
                bucket += 1
                i += 1
            if fid == 0x210:
                cur[0x210] = decode_0x210(d)
            elif fid == 0x200:
                cur[0x200] = decode_0x200(d)
                bcnt_moving = '1' if (bcnt_prev is not None and d[6] != bcnt_prev) else '0'
                bcnt_prev = d[6]
            elif fid == 0x201:
                cur[0x201] = decode_0x201(d)
            elif fid == 0x156:
                cur[0x156] = decode_remote_156(d)
            elif fid == 0x157:
                cur[0x157] = decode_remote_157(d)
    print(f"완료: {out_dir}/summary.md, events.md ({len(ev)}건), timeline.csv ({i}행)")


if __name__ == '__main__':
    main()
