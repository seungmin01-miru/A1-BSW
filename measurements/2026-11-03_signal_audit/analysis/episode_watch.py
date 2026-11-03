#!/usr/bin/env python3
"""근거리 왕복(전진→정지→후진→정지) 시험용 에피소드 분석기.

명령이 섞일 수밖에 없는 조건(3 m 공간, 매번 후진 복귀)을 전제로,
'정지 → 움직임 → 정지' 한 번을 한 단위(에피소드)로 잡고 그 안을 가속·타력·브레이크 구간으로 나눈다.
방향은 버스에 신호가 없어 notes.txt 메모('전진'/'후진')를 쓰고, 없으면 교대(전진부터) 가정.

  follow : python3 episode_watch.py <session>              # 녹화 파일 끝에서부터 실시간
  replay : python3 episode_watch.py <session> --from 16:04:03 --to 16:07:00
"""
import argparse, datetime, os, sys, time
import numpy as np
import cantools

DBC = '/home/ailab/git/A1-BSW/DBC/A1_dbc.dbc'
MOVE = 0.10        # m/s, 움직임 판정 — 0x201 은 부호가 없어 전진↔후진 전환이 0 근처로만 찍힌다 → 0.1 미만이면 정지/전환
STILL_END = 0.10   # s, 0.1 m/s 미만이 이만큼 이어지면 에피소드 끝(전환점 포함)
PRE = 2.5          # s, 움직임 전 이 시간 안의 가속 명령을 같은 에피소드로
LAT_ACC = 0.6      # s, 가속 놓은 뒤 응답이 남는 시간(실측 지연 중앙값)

db = cantools.database.load_file(DBC)
hm = lambda t: datetime.datetime.fromtimestamp(t).strftime('%T')
hms = lambda t: datetime.datetime.fromtimestamp(t).strftime('%T.%f')[:-4]


class Buf:
    def __init__(self):
        self.C, self.S, self.W = [], [], []   # (t, steer, brake, acc) / (t, steerpos, brakepos) / (t, v m/s)

    def add(self, t, i, d):
        if i == 0x210:
            v = db.decode_message(i, d); self.C.append((t, v['steer_command'], v['break_command'], v['acc_command']))
        elif i == 0x200:
            v = db.decode_message(i, d); self.S.append((t, v['steer_postion'], v['break_postion']))
        elif i == 0x201:
            v = db.decode_message(i, d); self.W.append((t, (v['left_speed'] + v['right_speed']) / 2 / 3.6))

    def arr(self, a, b):
        f = lambda L: np.array([r for r in L if a <= r[0] <= b]) if L else np.zeros((0, 3))
        return f(self.C), f(self.S), f(self.W)

    def trim(self, keep_from):
        for L in (self.C, self.S, self.W):
            k = 0
            while k < len(L) and L[k][0] < keep_from: k += 1
            del L[:k]


def pulses(C, col, thr=0):
    out, s = [], None
    for k in range(len(C)):
        on = abs(C[k, col]) > thr
        if on and s is None: s = k
        if not on and s is not None: out.append((C[s, 0], C[k - 1, 0], np.max(np.abs(C[s:k, col])))); s = None
    if s is not None: out.append((C[s, 0], C[-1, 0], np.max(np.abs(C[s:, col]))))
    return [p for p in out if p[1] - p[0] >= 0.06]


def slope(W, a, b):
    m = (W[:, 0] >= a) & (W[:, 0] <= b) & (W[:, 1] > 0.08)
    if m.sum() < 8: return None
    return np.polyfit(W[m, 0] - a, W[m, 1], 1)[0]


def direction(notes, a, b, n):
    for t, txt in notes:
        if a - 15 <= t <= b + 3:
            if txt.startswith('후진'): return '후진', '메모'
            if txt.startswith('전진'): return '전진', '메모'
    return ('전진' if n % 2 == 1 else '후진'), '교대가정'


def read_notes(session):
    out = []
    try:
        for line in open(os.path.join(session, 'raw', 'notes.txt')):
            p = line.split(' ', 2)
            if len(p) == 3: out.append((float(p[0]), p[2].strip()))
    except OSError:
        pass
    return out


def analyze_episode(buf, t_m0, t_m1, n, session):
    """에피소드 = 정지→움직임→정지. 안을 구간으로 나눠 구간마다 (시작 속도, 기울기)를 낸다 — 명령이 섞여도 구간 값은 쓸 수 있다."""
    C, S, W = buf.arr(t_m0 - PRE, t_m1 + 0.5)
    t, v = W[:, 0], W[:, 1]
    mv = t >= t_m0
    k_pk = int(np.argmax(np.where(mv, v, -1))); v_pk, t_pk = v[k_pk], t[k_pk]
    m = (t >= t_m0) & (t <= t_m1); dist = float(np.trapz(v[m], t[m]))
    dirn, src = direction(read_notes(session), t_m0, t_m1, n)
    acc = [p for p in pulses(C, 3) if p[1] >= t_m0 - PRE]
    brk = [p for p in pulses(C, 2) if p[0] >= t_m0 - 0.5]
    # 구간 경계: 명령 변화 시각 + 응답 지연(가속 LAT_ACC, 브레이크는 0x200 위치로 실측)
    ev = []
    for a, b, mx in acc: ev.append((a + LAT_ACC, 'A+', mx)); ev.append((b + LAT_ACC, 'A-', 0))
    for a, b, mx in brk:
        pos = S[(S[:, 0] > a) & (S[:, 2] > 0.5)] if len(S) else []
        bl = (pos[0, 0] - a) if len(pos) else 0.15
        ev.append((a + bl, 'B+', mx)); ev.append((b + bl, 'B-', 0))
    ev.sort()
    cuts = [t_m0] + [e[0] for e in ev if t_m0 < e[0] < t_m1] + [t_m1]
    phases = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 0.3: continue
        mid = (a + b) / 2
        on_acc = any(x + LAT_ACC <= mid <= y + LAT_ACC for x, y, _ in acc)
        on_brk = any(x <= mid <= y + 0.3 for x, y, _ in brk)
        kind = '브레이크' if on_brk else ('가속' if on_acc else '타력')
        mm = (t >= a) & (t <= b) & (v > 0.08)
        if mm.sum() < 8: continue
        sl = np.polyfit(t[mm] - a, v[mm], 1)[0]
        stc = C[(C[:, 0] >= a - LAT_ACC) & (C[:, 0] <= b), 1] if len(C) else []
        st = float(np.max(np.abs(stc))) if len(stc) else 0
        phases.append((kind, a, b, float(np.interp(a, t, v)), float(np.interp(b, t, v)), sl, st))
    # 지연: 움직이기 직전 가속 시작 → 움직임
    pre = [p for p in acc if p[0] <= t_m0]
    lat = (t_m0 - pre[-1][0]) if pre else None
    head = (f'[E{n} {dirn}({src})] {hm(t_m0)}~{hm(t_m1)} {t_m1-t_m0:.1f}s 거리 {dist:.2f} m, 최고 {v_pk:.2f} m/s({v_pk*3.6:.1f} km/h)'
            + (f', 출발 지연 {lat:.2f}s' if lat is not None else ', 가속 명령 없이 움직임') + f', 가속 {len(acc)}회 브레이크 {len(brk)}회')
    rows = []
    for kind, a, b, v0, v1, sl, st in phases:
        ok = '✓' if st <= 20 else f'조향 {st:.0f}° 겹침'
        rows.append(f'   {kind:4s} +{a-t_m0:4.1f}~{b-t_m0:4.1f}s  {v0:.2f}→{v1:.2f} m/s  {sl:+.2f} m/s²  {ok}')
    warn = []
    if v_pk > 1.0: warn.append(f'⚠ 속도 {v_pk:.2f} m/s > 1.0')
    if dist > 2.0: warn.append(f'⚠ 거리 {dist:.2f} m > 2.0')
    out = '\n'.join([head] + rows + (['   ' + '; '.join(warn)] if warn else []))
    with open(os.path.join(session, 'analysis', 'episodes.md'), 'a') as f:
        f.write(out.replace('\n   ', '\n    - ').replace('[E', '- [E', 1) + '\n')
    return out


def analyze_steer(buf, t0, session):
    C, S, W = buf.arr(t0 - 0.2, t0 + 3.0)
    if not len(S): return None
    Cw = C[(C[:, 0] >= t0) & (C[:, 0] <= t0 + 2.5)]
    if not len(Cw): return None
    tgt = Cw[np.argmax(np.abs(Cw[:, 1])), 1]
    if abs(tgt) < 20: return None
    p0 = float(np.interp(t0, S[:, 0], S[:, 1]))
    mv = S[(S[:, 0] >= t0) & (np.sign(tgt - p0) * (S[:, 1] - p0) > 3)]
    lat = mv[0, 0] - t0 if len(mv) else None
    r = S[(S[:, 0] >= t0) & (np.sign(tgt - p0) * (S[:, 1] - (p0 + 0.9 * (tgt - p0))) >= 0)]
    t90 = r[0, 0] - t0 if len(r) else None
    Sw = S[S[:, 0] >= t0]; pk = Sw[np.argmax(np.sign(tgt) * Sw[:, 1]), 1]
    held = C[np.abs(C[:, 1] - tgt) < 3]
    ramp = (held[0, 0] - t0) if len(held) else 0
    hold = held[-1, 0] - held[0, 0] if len(held) else 0
    if hold < 0.3: return None
    v0 = float(np.interp(t0, W[:, 0], W[:, 1])) if len(W) else 0.0
    tag = '조향 정지' if v0 < MOVE else f'조향 주행 {v0:.2f} m/s'
    line = (f'[{tag}] {hm(t0)} {p0:+.0f}° → 명령 {tgt:+.0f}° (유지 {hold:.1f}s) → 지연 {lat:.2f}s' if lat is not None else f'[{tag}] {hm(t0)} 명령 {tgt:+.0f}° → 반응 없음') + \
           (f', 90% {t90:.2f}s' if t90 else '') + f', 최대 위치 {pk:+.1f}°'
    if hold >= 0.75 and abs(tgt) < 140: line += ' ✓ 중간각 유지됨(원격 비례)'
    if ramp > 0.5: line += f' (명령이 {ramp:.1f}s 에 걸쳐 천천히 증가 — 지연·90%는 명령 시작 기준)'
    if abs(p0) > 20: line += ' (반전 — 지연은 명령 0 통과 기준이라 참고값)'
    with open(os.path.join(session, 'analysis', 'episodes.md'), 'a') as f: f.write(f'- {line}\n')
    return line


def analyze_brake_still(buf, b, session):
    C, S, W = buf.arr(b[0] - 0.2, b[1] + 1.0)
    pos = S[S[:, 2] > 0.5]
    lat = pos[0, 0] - b[0] if len(pos) else None
    mx = S[:, 2].max() if len(S) else 0
    r90 = S[S[:, 2] >= 0.9 * mx] if mx > 0 else []
    t90 = r90[0, 0] - b[0] if len(r90) else None
    rel = S[(S[:, 0] > b[1]) & (S[:, 2] < 0.5)]
    trel = rel[0, 0] - b[1] if len(rel) else None
    line = f'[브레이크 정지] {hm(b[0])} {b[2]:.0f}% {b[1]-b[0]:.1f}s → 위치 최대 {mx:.1f}' + \
           (f', 지연 {lat:.2f}s' if lat is not None else '') + (f', 90% {t90:.2f}s' if t90 else '') + (f', 해제 {trel:.2f}s' if trel else '')
    with open(os.path.join(session, 'analysis', 'episodes.md'), 'a') as f: f.write(f'- {line}\n')
    return line


def run(session, t_from=None, t_to=None):
    path = os.path.join(session, 'raw', 'kvaser_ch0_listen.log')
    os.makedirs(os.path.join(session, 'analysis'), exist_ok=True)
    buf = Buf(); n = 0
    moving = False; t_m0 = t_last_move = None
    pend_steer = []; pend_brake = []; last_steer = 0.0; last_brake = 0; b_start = None
    last_frame_wall = time.time(); silent = False; t_now = 0
    f = open(path)
    if t_from is None: f.seek(0, 2)
    print(f'분석 시작 {hm(time.time()) if t_from is None else "replay"}', flush=True)
    while True:
        line = f.readline()
        if not line:
            if t_from is not None: break
            if not silent and time.time() - last_frame_wall > 3:
                print(f'{hm(time.time())} 버스 프레임 없음 (차량/CAN 꺼짐?)', flush=True); silent = True
            time.sleep(0.05); continue
        p = line.split()
        try:
            t = float(p[0].strip('()')); i, d = p[2].split('#'); i = int(i, 16); d = bytes.fromhex(d)
        except (IndexError, ValueError):
            continue
        if t_from is not None and t < t_from: continue
        if t_to is not None and t > t_to: break
        last_frame_wall = time.time(); t_now = t
        if silent: print(f'{hm(t)} 버스 다시 들어옴', flush=True); silent = False
        buf.add(t, i, d)
        if i == 0x201:
            v = buf.W[-1][1]
            if v > MOVE:
                if not moving: moving, t_m0 = True, t
                t_last_move = t
            elif moving and t - t_last_move >= STILL_END:
                n += 1; print(analyze_episode(buf, t_m0, t_last_move, n, session), flush=True); moving = False
        elif i == 0x210:
            _, st, br, ac = buf.C[-1]
            idle = not moving and (not buf.W or buf.W[-1][1] < MOVE)
            if abs(last_steer) < 5 <= abs(st) and not any(abs(q - t) < 1.0 for q in pend_steer): pend_steer.append(t)
            if last_brake == 0 and br > 0: b_start = (t, idle)
            if last_brake > 0 and br == 0 and b_start:
                if b_start[1] and not moving and t - b_start[0] >= 0.3: pend_brake.append((b_start[0], t, 100))
                b_start = None
            last_steer, last_brake = st, br
        for q in [q for q in pend_steer if t_now - q > 3.0]:
            r = analyze_steer(buf, q, session)
            if r: print(r, flush=True)
            pend_steer.remove(q)
        for b in [b for b in pend_brake if t_now - b[1] > 1.0]:
            print(analyze_brake_still(buf, b, session), flush=True); pend_brake.remove(b)
        if len(buf.W) > 20000: buf.trim(t_now - 60)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('session'); ap.add_argument('--from', dest='fr'); ap.add_argument('--to')
    a = ap.parse_args()
    conv = lambda s: datetime.datetime.strptime(datetime.date.today().isoformat() + ' ' + s, '%Y-%m-%d %H:%M:%S').timestamp() if s else None
    run(a.session, conv(a.fr), conv(a.to))
