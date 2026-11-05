"""2026-11-04 인터페이스(어댑터·can_guard) 세부 수치 — Kvaser 원본(candump) 시각 + GNSS(INS) bag CSV.

입력: candump 로그들(원격·자율·오후 빠른 시험), extract.py 로 뽑은 out/<세션>/gsof_client__gsof__ins_solution_49.csv 등.
출력: OUT 폴더에 표 CSV(텍스트) — 문서 measurements/2026-11-04_drive_log/README.md 5장의 근거.
"""
import sys, os, csv, glob, json, numpy as np
sys.path.insert(0, '/home/ailab/git/A1-BSW/tools/signal_audit'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbc_lite, minidf

DBC = dbc_lite.load('/home/ailab/git/A1-BSW/DBC/A1_dbc_fixed.dbc'); DBC.update(dbc_lite.remote_messages())
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
ANA = os.path.dirname(os.path.abspath(__file__))
L = 2.36; WHEEL_SCALE = 0.958
RUN = '/home/ailab/a1_race_capture/a1drive_20261104_164006_run'
SRC = {'remote': '/home/ailab/git/A1-BSW/measurements/2026-11-04_signal_audit/raw/kvaser_ch0_listen.log'}
for s in ['bsw', 'bsw2', 'bsw3', 'bsw4', 'bsw5']:
    SRC['auto_' + s] = f'{RUN}/{s}/candump_kv0.log'
for f in sorted(glob.glob('/home/ailab/a1_race_capture/20261104_*/candump_kv0.log')):
    SRC['quick_' + f.split('/')[-2][9:]] = f


def readcan(path):
    rows = {}
    for line in open(path):
        p = line.split()
        if len(p) < 3 or '#' not in p[2]:
            continue
        cid, d = p[2].split('#'); cid = int(cid, 16)
        rows.setdefault(cid, []).append((float(p[0][1:-1]), d))
    out = {}
    for cid, r in rows.items():
        t = np.array([x[0] for x in r])
        sig = {}
        if cid in DBC:
            dec = [DBC[cid].decode(bytes.fromhex(x[1])) for x in r]
            sig = {k: np.array([m[k] for m in dec]) for k in dec[0]}
        out[cid] = (t, sig)
    return out


def sm(x, n):
    if n <= 1: return x
    k = np.ones(n) / n; y = np.convolve(x, k, 'same')
    y[:n] = x[:n]; y[-n:] = x[-n:]
    return y


def held(t, x, win, tol):
    """각 시점에서 직전 win 초 동안 x 의 최대-최소 <= tol 이면 True."""
    ok = np.zeros(len(t), bool); j = 0
    from collections import deque
    mx, mn = deque(), deque()
    for i in range(len(t)):
        while mx and x[mx[-1]] <= x[i]: mx.pop()
        mx.append(i)
        while mn and x[mn[-1]] >= x[i]: mn.pop()
        mn.append(i)
        while t[i] - t[j] > win:
            j += 1
            if mx[0] < j: mx.popleft()
            if mn[0] < j: mn.popleft()
        ok[i] = (t[i] - t[j] >= win * 0.9) and (x[mx[0]] - x[mn[0]] <= tol)
    return ok


def wcsv(name, header, rows, note=''):
    with open(os.path.join(OUT, name), 'w', newline='') as f:
        if note:
            for ln in note.strip().splitlines(): f.write('# ' + ln + '\n')
        w = csv.writer(f); w.writerow(header)
        for r in rows:
            w.writerow([(('%.3f' % v if abs(v) > 1e6 else '%.4g' % v) if isinstance(v, (float, np.floating)) else v) for v in r])
    print('wrote', name, len(rows))


def q(x, p): return float(np.percentile(x, p)) if len(x) else float('nan')


print('reading CAN ...')
CAN = {k: readcan(v) for k, v in SRC.items()}
OVR = {}

# ---------- GNSS(INS) per auto session ----------
GN = {}
for s in ['bsw', 'bsw2', 'bsw3', 'bsw4', 'bsw5']:
    g = minidf.read_csv(f'{ANA}/out/{s}/gsof_client__gsof__ins_solution_49.csv')
    GN['auto_' + s] = (np.asarray(g.t), np.asarray(g.spd), np.asarray(g.yawrate), np.asarray(g.ax), np.asarray(g.gnss), np.asarray(g.imu))


def override_windows(src):
    """앱 원격이 켜진 시간(0x156 EPS_En 또는 ACC_En = 1) ± 2 s — 이때 차량은 0x210 이 아니라 원격을 따른다."""
    c = CAN[src]
    if 0x156 not in c: return []
    t, sg = c[0x156]; on = (sg['EPS_En'] > 0) | (sg['ACC_En'] > 0)
    if not on.any(): return []
    w = []; i = 0; idx = np.where(on)[0]
    st = idx[0]; prev = idx[0]
    for k in idx[1:]:
        if t[k] - t[prev] > 1.0: w.append((t[st] - 2, t[prev] + 2)); st = k
        prev = k
    w.append((t[st] - 2, t[prev] + 2)); return w


def ok_time(src, tt):
    m = np.ones(len(tt), bool)
    for a, b in OVR.get(src, []): m &= ~((tt >= a) & (tt <= b))
    return m


def frames(src):
    c = CAN[src]
    t0, s0 = c.get(0x200, (np.array([]), {})); t1, s1 = c.get(0x201, (np.array([]), {})); t2, s2 = c.get(0x210, (np.array([]), {}))
    return t0, s0, t1, s1, t2, s2


OVR = {k: override_windows(k) for k in SRC}
print('override windows', {k: [(round(a), round(b)) for a, b in v] for k, v in OVR.items() if v})


def speed_src(src, t1, s1):
    """속도[m/s]: 자율 = GNSS(INS total_speed, RTK fix 만), 그 외 = 0x201 평균 / 0.958."""
    vw = (s1['left_speed'] + s1['right_speed']) / 2 / 3.6 / WHEEL_SCALE
    if src in GN:
        tg, spd, yr, ax, gq, imu = GN[src]
        good = (gq == 4) & (imu >= 3)
        return tg[good], spd[good], 'GNSS'
    return t1, vw, '0x201/0.958'

# =====================================================================
# 1. 주기·타이밍
rows = []
for src in ['remote', 'auto_bsw3', 'auto_bsw5']:
    for cid in sorted(CAN[src]):
        t = CAN[src][cid][0]
        if len(t) < 100: continue
        d = np.diff(t) * 1000
        rows.append([src, '0x%03X' % cid, len(t), round(t[-1] - t[0], 1), np.median(d), q(d, 1), q(d, 99), d.max(), np.mean(d > 2 * np.median(d))])
wcsv('timing_can_ids.csv', ['source', 'id', 'count', 'span_s', 'period_med_ms', 'period_p1_ms', 'period_p99_ms', 'period_max_ms', 'frac_gap_over_2x'], rows,
     '1. CAN ID 별 주기(Kvaser 수신 시각). remote = 오전 원격(0x210 송신자 = 원격 유닛), auto = 오후 자율(0x210 송신자 = can_guard)')

# can_guard 0x210 송신 주기 전 세션
rows = []
for src in [k for k in SRC if k.startswith('auto_') or k.startswith('quick_')]:
    t = CAN[src].get(0x210, (np.array([]),))[0]
    if len(t) < 50: continue
    d = np.diff(t) * 1000
    rows.append([src, len(t), np.median(d), q(d, 99), q(d, 99.9), d.max(), int(np.sum(d > 40)), int(np.sum(d > 100))])
wcsv('timing_canguard_tx.csv', ['source', 'frames', 'med_ms', 'p99_ms', 'p999_ms', 'max_ms', 'gaps_over_40ms', 'gaps_over_100ms'], rows,
     'can_guard 0x210 송신 간격(설정 20 ms). gap>40 ms = 한 주기 이상 빠짐')

# 어댑터·제어기 ROS 쪽 주기(bag 시각 — 이 토픽들은 지연 없음 확인)
rows = []
for s in ['bsw3', 'bsw4', 'bsw5']:
    ad = minidf.read_csv(f'{ANA}/out/{s}/control__adapter__status.csv'); tg = minidf.read_csv(f'{ANA}/out/{s}/control__target.csv')
    od = minidf.read_csv(f'{ANA}/out/{s}/localization__odom.csv')
    arm = (np.asarray(ad.sarm) > 0) | (np.asarray(ad.larm) > 0)
    age = np.asarray(ad.age)[arm] * 1000
    for name, t in [('/control/adapter/status', np.asarray(ad.t)), ('/control/target', np.asarray(tg.t)), ('/localization/odom', np.asarray(od.t))]:
        d = np.diff(t) * 1000
        rows.append([s, name, len(t), np.median(d), q(d, 99), d.max(), int(np.sum(d > 200)), '', ''])
    rows.append([s, 'adapter input_age (armed)', int(arm.sum()), np.median(age), q(age, 99), age.max(), int(np.sum(age > 150)), int(np.max(np.asarray(ad.rej))), int(np.sum(np.asarray(ad.writing)[arm] == 0))])
wcsv('timing_ros.csv', ['session', 'topic', 'n', 'med_ms', 'p99_ms', 'max_ms', 'count_over_200ms(age:over_stale_150ms)', 'rejected_max', 'writing_false_while_armed'], rows,
     'ROS 쪽 주기·어댑터 입력 나이. stale_timeout_s = 0.15, odom_timeout_s(follower) = 0.2')

# =====================================================================
# 2. 조향 — 정적 특성(명령 1 s 유지 뒤 위치)
st_rows = []; small_rows = []
allc, allp, allsrc = [], [], []
for src in SRC:
    t0, s0, t1, s1, t2, s2 = frames(src)
    if len(t2) < 100 or len(t0) < 100: continue
    cmd = s2['steer_command']; h = held(t2, cmd, 1.0, 0.3) & ok_time(src, t2)
    tt = t2[h]; pos = np.interp(tt, t0, s0['steer_postion'])
    allc.append(cmd[h]); allp.append(pos); allsrc += [src] * int(h.sum())
allc = np.concatenate(allc); allp = np.concatenate(allp); allsrc = np.array(allsrc)
edges = [-150.5, -140, -120, -100, -80, -60, -40, -20, -10, -5, -2, -0.5, 0.5, 2, 5, 10, 20, 40, 60, 80, 100, 120, 140, 150.5]
for a, b in zip(edges[:-1], edges[1:]):
    m = (allc >= a) & (allc < b)
    if m.sum() < 20: continue
    e = allp[m] - allc[m]
    st_rows.append([a, b, int(m.sum()), np.mean(allc[m]), np.mean(allp[m]), np.mean(e), q(e, 5), q(e, 95), len(set(allsrc[m]))])
wcsv('steer_static.csv', ['cmd_from_deg', 'cmd_to_deg', 'n', 'cmd_mean_wheel_deg', 'pos_mean_wheel_deg', 'err_mean_deg', 'err_p5', 'err_p95', 'n_sources'], st_rows,
     '2-1. 조향 정적: 0x210 steer_command 를 1 s 이상 ±0.3° 안에서 유지한 뒤의 0x200 steer_postion (핸들 °). 원격+자율+빠른시험 전부')

# 2-2 조향 계단 응답: |Δcmd| >= 20° 인 프레임
step_rows = []
for src in SRC:
    t0, s0, t1, s1, t2, s2 = frames(src)
    if len(t2) < 100 or len(t0) < 100: continue
    cmd = s2['steer_command']; pos = s0['steer_postion']
    dc = np.diff(cmd)
    for i in np.where(np.abs(dc) >= 20)[0]:
        ts = t2[i + 1]; c0, c1 = cmd[i], cmd[i + 1]
        if not ok_time(src, np.array([ts]))[0]: continue
        # 다음 1.5 s 동안 명령이 그대로여야(계단)
        nxt = (t2 > ts) & (t2 < ts + 1.5)
        if not nxt.any() or np.max(np.abs(cmd[nxt] - c1)) > 1.0: continue
        m = (t0 >= ts - 0.05) & (t0 <= ts + 1.5)
        if m.sum() < 30: continue
        tp, pp = t0[m], pos[m]; p0 = np.interp(ts, t0, pos); sgn = np.sign(c1 - c0)
        mv = np.where(sgn * (pp - p0) > 2.0)[0]
        dead = (tp[mv[0]] - ts) * 1000 if len(mv) else np.nan
        span = c1 - p0
        if abs(span) < 15: continue
        a10 = np.where(sgn * (pp - p0) >= 0.1 * abs(span))[0]; a90 = np.where(sgn * (pp - p0) >= 0.9 * abs(span))[0]
        rise = (tp[a90[0]] - tp[a10[0]]) if len(a10) and len(a90) else np.nan
        rate = 0.8 * abs(span) / rise if rise and rise > 0 else np.nan
        t90 = (tp[a90[0]] - ts) * 1000 if len(a90) else np.nan
        over = sgn * (np.max(sgn * pp) * sgn - c1) if True else 0
        over = max(0.0, sgn * (pp[np.argmax(sgn * pp)] - c1))
        final = np.mean(pp[tp > ts + 1.2]) if (tp > ts + 1.2).any() else np.nan
        tsp = np.interp(ts, t1, (s1['left_speed'] + s1['right_speed']) / 2) if len(t1) else np.nan
        step_rows.append([src, round(ts, 3), c0, c1, round(p0, 1), tsp, dead, t90, rise * 1000 if rise == rise else np.nan, rate, over, final - c1])
wcsv('steer_steps.csv', ['source', 't_unix', 'cmd_from', 'cmd_to', 'pos_at_step', 'speed_kph', 'dead_ms(>2deg)', 't90_ms', 'rise10_90_ms', 'rate_deg_s(10-90)', 'overshoot_deg', 'final_err_deg(1.2-1.5s)'], step_rows,
     '2-2. 조향 계단 응답: 0x210 이 한 프레임에 20° 이상 바뀌고 1.5 s 유지된 경우만. 핸들 °')

# 2-3 조향 추종 지연(연속 신호, 자율 — 어댑터가 120°/s 로 제한한 램프)
rows = []
for src in ['auto_bsw3', 'auto_bsw4', 'auto_bsw5', 'remote']:
    t0, s0, t1, s1, t2, s2 = frames(src)
    v = np.interp(t2, t1, (s1['left_speed'] + s1['right_speed']) / 2)
    for lo, hi in [(0, 0.5), (0.5, 10), (10, 40)]:
        m = (v >= lo) & (v < hi)
        if m.sum() < 500: continue
        best = None
        for lag in range(0, 60):
            pos = np.interp(t2[m] + lag / 100, t0, s0['steer_postion']); e = pos - s2['steer_command'][m]
            r = np.sqrt(np.mean(e ** 2))
            if best is None or r < best[1]: best = (lag * 10, r, q(np.abs(e), 95))
        cr = np.abs(np.gradient(sm(s2['steer_command'][m], 3), t2[m]))
        rows.append([src, f'{lo}-{hi}', int(m.sum()), best[0], best[1], best[2], q(cr, 99)])
wcsv('steer_tracking.csv', ['source', 'speed_kph', 'n', 'best_lag_ms', 'rms_at_lag_deg', 'p95_abs_at_lag_deg', 'cmd_rate_p99_deg_s'], rows,
     '2-3. 조향 연속 추종: 0x200 위치를 lag 만큼 당겨 0x210 명령과 rms 최소가 되는 지연. 자율은 어댑터 레이트 제한 120°/s 램프 입력')

# 2-4 조향 위치 변화 속도(액추에이터 능력)
rows = []
for src in ['remote', 'auto_bsw5', 'quick_QUICK-STEER-LON_s3']:
    t0, s0, *_ = frames(src)
    r = np.abs(np.gradient(sm(s0['steer_postion'], 3), t0))
    rows.append([src, q(r, 90), q(r, 99), q(r, 99.9), r.max()])
wcsv('steer_rate.csv', ['source', 'rate_p90_deg_s', 'p99', 'p99.9', 'max'], rows, '2-4. 0x200 조향 위치 변화 속도(3점 평활 미분, 핸들 °/s)')

# 2-4b 주행 중 위치 계단(불감대): 위치가 0.1 s 이상 멈춘 뒤 다시 움직이기 직전의 |명령 − 위치|
rows = []
for src, ta, tb in [('auto_bsw3', 1793779030, 1793779090), ('auto_bsw5', 1793779377, 1793779786), ('remote', 0, 1e10)]:
    t0, s0, t1, s1, t2, s2 = frames(src)
    m = (t0 >= ta) & (t0 <= tb); T0 = t0[m]; Pp = s0['steer_postion'][m]
    v = np.interp(T0, t1, (s1['left_speed'] + s1['right_speed']) / 2); dp = np.diff(Pp)
    mv = np.where(np.abs(dp) >= 0.5)[0]; errs = []; hold = []; last = -1
    for i in mv:
        if last >= 0 and T0[i] - T0[last + 1] >= 0.1 and v[i] > 3:
            errs.append(abs(np.interp(T0[i], t2, s2['steer_command']) - Pp[i])); hold.append(T0[i] - T0[last + 1])
        last = i
    e = np.abs(np.interp(T0, t2, s2['steer_command']) - Pp); still = np.r_[False, np.abs(dp) < 0.05] & (v > 3)
    rows.append([src, len(errs), q(errs, 25), q(errs, 50), q(errs, 75), q(hold, 50), q(hold, 90), q(e[still], 50), q(e[still], 90), still.sum() / max(1, (v > 3).sum())])
wcsv('steer_deadband_driving.csv', ['source', 'n_plateaus', 'err_at_move_p25_deg', 'p50', 'p75', 'hold_p50_s', 'hold_p90_s', 'err_while_still_p50', 'err_while_still_p90', 'frac_time_still'], rows,
     '2-4b. 주행 중(> 3 km/h) 0x200 위치는 계단식: 0.1 s 이상 멈춰 있다가 |명령 − 위치| 가 err_at_move 만큼 벌어지면 움직인다(핸들 °)')

# 2-5 조향비(GNSS 요레이트): 자율 세션
ratio_rows = []; pts = []
for src in ['auto_bsw', 'auto_bsw2', 'auto_bsw3', 'auto_bsw4', 'auto_bsw5']:
    t0, s0, *_ = frames(src)
    tg, spd, yr, ax, gq, imu = GN[src]
    good = (gq == 4) & (imu >= 3) & (spd > 1.5)
    tg, spd, yr = tg[good], spd[good], yr[good]
    if len(tg) < 100: continue
    ys = sm(yr, 20); vs = sm(spd, 20)
    pos = np.interp(tg - 0.15, t0, s0['steer_postion'])            # 조향 위치는 0.15 s 앞 값(액추에이터→차체 반응)
    posh = held(tg, pos, 1.0, 6.0)                                  # 1 s 동안 핸들 변화 6° 이하(준정상)
    yrd = np.abs(np.gradient(ys, tg)) < 5                            # 요 가속 작음
    m = posh & yrd
    tire = np.degrees(np.arctan(L * np.radians(-ys[m]) / vs[m]))    # ang_rate_down + = 시계(우) → 좌 + 로 부호 반전
    pts.append(np.column_stack([pos[m], tire, vs[m] * 3.6, np.radians(-ys[m]) * vs[m]]))
P = np.concatenate(pts)
corr = np.corrcoef(P[:, 0], P[:, 1])[0, 1]
mm = np.abs(P[:, 0]) < 40; k0 = np.polyfit(P[mm, 1], P[mm, 0], 1); B0 = k0[1]
np.savetxt(os.path.join(OUT, 'steer_ratio_points.csv'), P[::5], delimiter=',', fmt='%.3f', header='wheel_deg,tire_eff_deg,speed_kph,a_lat_mps2 (5개 중 1개 표본, 2-5 근거 점)', comments='# ')
edges = [-150, -120, -90, -60, -40, -25, -15, -8, -3, 3, 8, 15, 25, 40, 60, 90, 120, 150.1]
for a, b in zip(edges[:-1], edges[1:]):
    m = (P[:, 0] >= a) & (P[:, 0] < b) & (np.abs(P[:, 3]) < 2.0)
    if m.sum() < 30: continue
    pw, ti = P[m, 0], P[m, 1]
    ratio_rows.append([a, b, int(m.sum()), np.mean(pw), np.mean(ti), q(ti, 10), q(ti, 90), (np.mean(pw) - B0) / np.mean(ti - (-B0 / k0[0])) if abs(np.mean(ti) + B0 / k0[0]) > 1.0 else np.nan, np.mean(P[m, 2]), np.mean(np.abs(P[m, 3]))])
wcsv('steer_ratio_gnss.csv', ['wheel_from_deg', 'wheel_to_deg', 'n', 'wheel_mean_deg', 'tire_mean_deg', 'tire_p10', 'tire_p90', 'ratio_offset_corrected', 'speed_mean_kph', 'a_lat_mean_mps2'], ratio_rows,
     f'''2-5. 핸들각(0x200) → 실제 타이어각(운동학 δ = atan(L·r/v), L = 2.36 m, r = INS 요레이트, v = INS 속도). 자율 5세션, RTK fix, v > 5.4 km/h,
준정상(1 s 핸들 변화 ≤ 6°, 요가속 < 5 °/s²), 횡가속 < 2 m/s². 타이어각 + = 좌. 미끄럼각·언더스티어가 포함된 "유효" 타이어각이다.
상관계수 핸들 vs 타이어 = {corr:.3f}. ratio_offset_corrected = (핸들 − 직진 핸들각) ÷ (타이어 − 0), 직진 핸들각 = |핸들|<40° 회귀 절편''')
# 직선 근처 오프셋: 타이어 0 이 되는 핸들각(선형 회귀 |wheel|<40)
m = np.abs(P[:, 0]) < 40
k = np.polyfit(P[m, 1], P[m, 0], 1)
mL = (P[:, 0] > 3) & (np.abs(P[:, 3]) < 2); mR = (P[:, 0] < -3) & (np.abs(P[:, 3]) < 2)
kL = np.polyfit(P[mL, 1], P[mL, 0], 1); kR = np.polyfit(P[mR, 1], P[mR, 0], 1)
summ = [['fit |wheel|<40: wheel = a*tire + b', k[0], k[1], int(m.sum())],
        ['fit left (wheel>3)', kL[0], kL[1], int(mL.sum())],
        ['fit right (wheel<-3)', kR[0], kR[1], int(mR.sum())],
        ['tire at wheel=0 (from |wheel|<40 fit)', -k[1] / k[0], '', ''],
        ['corr wheel vs tire', corr, '', len(P)]]
wcsv('steer_ratio_fit.csv', ['item', 'slope_wheel_per_tire', 'intercept_wheel_deg', 'n'], summ,
     '2-5b. 핸들각 = 기울기 × 유효 타이어각 + 절편. 절편 = 차가 직진(요레이트 0)할 때의 핸들각 = 조향 오프셋(핸들 °)')

# =====================================================================
# 3. 종방향
def long_samples(src):
    t0, s0, t1, s1, t2, s2 = frames(src)
    if len(t2) < 100 or len(t1) < 100: return None
    ts, vs_, vsrc = speed_src(src, t1, s1)
    tt = np.arange(max(ts[0], t2[0]) + 2, min(ts[-1], t2[-1]) - 2, 0.05)
    if len(tt) < 50: return None
    v = np.interp(tt, ts, sm(vs_, 25 if vsrc == 'GNSS' else 12))
    a = np.gradient(v, tt); a = sm(a, 6)
    acc = np.interp(tt, t2, s2['acc_command']); brk = np.interp(tt, t2, s2['break_command'])
    # 응답 지연 반영: 가속 0.5 s, 브레이크 0.3 s 전 명령, 그리고 그 명령이 1.0 s(가속)/0.5 s(브레이크) 유지
    accd = np.interp(tt - 0.5, t2, s2['acc_command']); brkd = np.interp(tt - 0.3, t2, s2['break_command'])
    acch = held(tt, np.interp(tt - 0.5, t2, s2['acc_command']), 1.0, 6.0)
    brkh = held(tt, np.interp(tt - 0.3, t2, s2['break_command']), 0.5, 6.0) & ok_time(src, tt)
    acch &= ok_time(src, tt)
    bpos = np.interp(tt, t0, s0['break_postion'])
    return dict(t=tt, v=v, a=a, acc=acc, brk=brk, accd=accd, brkd=brkd, acch=acch, brkh=brkh, bpos=bpos, vsrc=vsrc)

LS = {src: long_samples(src) for src in SRC}
LS = {k: v for k, v in LS.items() if v is not None}

# 3-1 가속 표: acc % × 속도
rows = []
for grp, keys in [('remote', ['remote']), ('auto', [k for k in LS if k.startswith('auto_')]), ('quick', [k for k in LS if k.startswith('quick_')])]:
    D = {f: np.concatenate([LS[k][f] for k in keys if k in LS]) for f in ['v', 'a', 'accd', 'brkd', 'acch', 'brk']}
    for a_lo, a_hi in [(0, 1), (1, 50), (50, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 95), (95, 101)]:
        for v_lo, v_hi in [(0, 1), (1, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 35)]:
            m = (D['accd'] >= a_lo) & (D['accd'] < a_hi) & D['acch'] & (D['brkd'] < 0.5) & (D['brk'] < 0.5) & (D['v'] * 3.6 >= v_lo) & (D['v'] * 3.6 < v_hi)
            if m.sum() < 15: continue
            rows.append([grp, f'{a_lo}-{a_hi}', f'{v_lo}-{v_hi}', int(m.sum()), np.median(D['a'][m]), q(D['a'][m], 25), q(D['a'][m], 75)])
wcsv('long_accel_map.csv', ['group', 'acc_pct', 'speed_kph(true)', 'n(0.05s)', 'a_p50_mps2', 'a_p25', 'a_p75'], rows,
     '''3-1. 가속 명령 % → 종가속(m/s²). 명령은 0.5 s 전 값이고 그 전 1 s 동안 ±3 % 안에서 유지, 브레이크 0.
속도·가속 원천: auto = INS(GNSS RTK) 속도 미분, remote/quick = 0x201 평균 ÷ 0.958 미분. 경사 미보정(용인 트랙, 구간 섞임) → 잠정.
"0-1" 행 = 타행(가속 0·브레이크 0).''')

# 3-2 타행 감속 vs 속도 + 2차 적합
rows = []; V = []; A = []
for grp, keys in [('remote', ['remote']), ('auto', [k for k in LS if k.startswith('auto_')])]:
    D = {f: np.concatenate([LS[k][f] for k in keys]) for f in ['v', 'a', 'accd', 'brkd', 'acch', 'brkh', 'acc', 'brk']}
    m0 = (D['accd'] < 0.5) & (D['acc'] < 0.5) & D['acch'] & (D['brkd'] < 0.5) & (D['brk'] < 0.5) & D['brkh']
    for v_lo in range(2, 34, 4):
        m = m0 & (D['v'] * 3.6 >= v_lo) & (D['v'] * 3.6 < v_lo + 4)
        if m.sum() < 15: continue
        rows.append([grp, f'{v_lo}-{v_lo + 4}', int(m.sum()), np.median(D['a'][m]), q(D['a'][m], 25), q(D['a'][m], 75)])
        V.append(D['v'][m]); A.append(D['a'][m])
V = np.concatenate(V); A = np.concatenate(A)
c = np.polyfit(V ** 2, A, 1)
rows.append(['fit all', 'a = c0 + c2*v^2 (v m/s)', len(V), c[1], c[0], ''])
wcsv('long_coast.csv', ['group', 'speed_kph', 'n', 'a_p50_mps2', 'a_p25', 'a_p75'], rows,
     '3-2. 타행(가속 0·브레이크 0, 1 s 유지) 감속. 마지막 행: 최소제곱 a = c0 + c2·v² (c0 = a_p50 열, c2 = a_p25 열)')

# 3-3 브레이크 표
rows = []
for grp, keys in [('remote', ['remote']), ('auto', [k for k in LS if k.startswith('auto_')]), ('quick', [k for k in LS if k.startswith('quick_')]), ('all', list(LS))]:
    D = {f: np.concatenate([LS[k][f] for k in keys]) for f in ['v', 'a', 'brkd', 'brkh', 'acc', 'accd']}
    for b_lo, b_hi in [(1, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 82), (82, 95), (95, 101)]:
        for v_lo, v_hi in [(2, 10), (10, 20), (20, 35)]:
            m = (D['brkd'] >= b_lo) & (D['brkd'] < b_hi) & D['brkh'] & (D['acc'] < 0.5) & (D['accd'] < 0.5) & (D['v'] * 3.6 >= v_lo) & (D['v'] * 3.6 < v_hi)
            if m.sum() < 8: continue
            rows.append([grp, f'{b_lo}-{b_hi}', f'{v_lo}-{v_hi}', int(m.sum()), np.median(D['a'][m]), q(D['a'][m], 25), q(D['a'][m], 75)])
wcsv('long_brake_map.csv', ['group', 'brake_pct', 'speed_kph(true)', 'n(0.05s)', 'a_p50_mps2', 'a_p25', 'a_p75'], rows,
     '3-3. 브레이크 명령 % → 종가속(m/s², 음수 = 감속). 명령은 0.3 s 전 값이고 0.5 s 유지, 가속 0. 타행 감속이 포함된 전체 감속이다.')

# 3-4 브레이크 위치(0x200) 정적 vs 명령
rows = []
for grp, keys in [('all', list(SRC))]:
    C, Pp, Vv = [], [], []
    for k in keys:
        t0, s0, t1, s1, t2, s2 = frames(k)
        if len(t2) < 100 or len(t0) < 100: continue
        h = held(t2, s2['break_command'], 1.5, 0.5) & ok_time(k, t2)
        C.append(s2['break_command'][h]); Pp.append(np.interp(t2[h], t0, s0['break_postion']))
        Vv.append(np.interp(t2[h], t1, (s1['left_speed'] + s1['right_speed']) / 2) if len(t1) else np.zeros(h.sum()))
    C, Pp, Vv = map(np.concatenate, (C, Pp, Vv))
    for lo, hi in [(0, 0.5), (0.5, 15), (15, 25), (25, 35), (35, 45), (45, 55), (55, 65), (65, 75), (75, 81.5), (81.5, 90), (90, 99.5), (99.5, 101)]:
        for vname, vm in [('stopped', Vv < 0.5), ('moving', Vv >= 0.5)]:
            m = (C >= lo) & (C < hi) & vm
            if m.sum() < 20: continue
            rows.append([f'{lo}-{hi}', vname, int(m.sum()), np.mean(C[m]), np.mean(Pp[m]), q(Pp[m], 5), q(Pp[m], 95), np.mean(Pp[m]) * 10 - np.mean(C[m])])
rows2 = []
for k in SRC:
    t0, s0, t1, s1, t2, s2 = frames(k)
    if len(t2) < 100 or len(t0) < 100: continue
    h = held(t2, s2['break_command'], 1.5, 0.5) & ok_time(k, t2)
    C = s2['break_command'][h]; Pp = np.interp(t2[h], t0, s0['break_postion'])
    Vv = np.interp(t2[h], t1, (s1['left_speed'] + s1['right_speed']) / 2)
    for lo, hi in [(81.5, 95), (95, 101)]:
        for vname, vm in [('stopped', Vv < 0.5), ('moving', Vv >= 0.5)]:
            m = (C >= lo) & (C < hi) & vm
            if m.sum() >= 20: rows2.append([k, f'{lo}-{hi}', vname, int(m.sum()), np.mean(C[m]), np.mean(Pp[m]), q(Pp[m], 5), q(Pp[m], 95)])
wcsv('brake_position_by_session.csv', ['source', 'cmd_pct', 'state', 'n', 'cmd_mean', 'pos_mean', 'pos_p5', 'pos_p95'], rows2,
     '3-4b. 높은 브레이크 명령(≥ 82 %)을 1.5 s 유지했을 때 세션별 0x200 break_postion — 세션마다 포화값이 다르다')
wcsv('brake_position_static.csv', ['cmd_pct', 'state', 'n', 'cmd_mean', 'pos_mean(0-10)', 'pos_p5', 'pos_p95', 'pos*10 - cmd'], rows,
     '3-4. 브레이크 명령 % 를 1.5 s 유지한 뒤 0x200 break_postion. 기대값 = 명령 ÷ 10, 82 % 이상 8.2 포화(10-07)')

# 3-5 브레이크 계단 응답(위치)
rows = []
for src in SRC:
    t0, s0, t1, s1, t2, s2 = frames(src)
    if len(t2) < 100 or len(t0) < 100: continue
    b = s2['break_command']; db = np.diff(b)
    for i in np.where(np.abs(db) >= 20)[0]:
        ts = t2[i + 1]; b0, b1 = b[i], b[i + 1]
        if not ok_time(src, np.array([ts]))[0]: continue
        nxt = (t2 > ts) & (t2 < ts + 2.0)
        if not nxt.any() or np.max(np.abs(b[nxt] - b1)) > 2: continue
        m = (t0 >= ts) & (t0 <= ts + 2.0); tp, pp = t0[m], s0['break_postion'][m]
        if len(tp) < 20: continue
        p0 = np.interp(ts, t0, s0['break_postion']); sgn = np.sign(b1 - b0)
        mv = np.where(sgn * (pp - p0) > 0.2)[0]
        dead = (tp[mv[0]] - ts) * 1000 if len(mv) else np.nan
        target = min(b1, 82) / 10 if b1 > 0 else 0.0
        span = target - p0
        a90 = np.where(sgn * (pp - p0) >= 0.9 * abs(span))[0] if abs(span) > 0.5 else []
        t90 = (tp[a90[0]] - ts) * 1000 if len(a90) else np.nan
        v = np.interp(ts, t1, (s1['left_speed'] + s1['right_speed']) / 2) if len(t1) else np.nan
        rows.append([src, round(ts, 3), b0, b1, round(p0, 2), v, dead, t90, np.mean(pp[-10:])])
wcsv('brake_steps.csv', ['source', 't_unix', 'cmd_from', 'cmd_to', 'pos_at_step', 'speed_kph', 'dead_ms(>0.2)', 't90_ms', 'pos_final(2s)'], rows,
     '3-5. 브레이크 계단(한 프레임 20 %p 이상, 2 s 유지) → 0x200 break_postion 응답')

# 3-6 출발 지연(정지 → 가속 명령 ≥ 75 %, 브레이크 0)
rows = []
for src in SRC:
    t0, s0, t1, s1, t2, s2 = frames(src)
    if len(t2) < 100 or len(t1) < 100: continue
    acc = s2['acc_command']; brk = s2['break_command']; v = (s1['left_speed'] + s1['right_speed']) / 2
    on = np.where((acc[1:] >= 75) & (acc[:-1] < 75))[0] + 1
    for i in on:
        ts = t2[i]
        if np.interp(ts, t1, v) > 0.05 or brk[i] > 0 or not ok_time(src, np.array([ts]))[0]: continue
        if rows and rows[-1][0] == src and ts - rows[-1][1] < 3: continue
        m = (t1 > ts) & (t1 < ts + 6); vv = v[m]; tv = t1[m]
        mv = np.where(vv > 0.05)[0]
        dly = (tv[mv[0]] - ts) * 1000 if len(mv) else np.nan
        w = (t2 >= ts) & (t2 <= ts + (dly / 1000 if dly == dly else 6))
        r3 = np.where(vv >= 3.0)[0]
        if not (dly == dly) or np.median(acc[w]) < 75: continue
        rows.append([src, round(ts, 3), np.median(acc[w]), int(np.max(brk[w])), dly, (tv[r3[0]] - ts) * 1000 if len(r3) else np.nan])
wcsv('long_launch.csv', ['source', 't_unix', 'acc_pct_med_until_move', 'brake_max_until_move', 'first_move_ms(0x201>0)', 't_to_3kph_ms'], rows,
     '3-6. 정지 출발: 가속 명령이 75 % 이상으로 올라간 순간 → 0x201 첫 0 아닌 값. 0x201 분해능 0.1 km/h')

# 3-7 바퀴 속도 vs GNSS
rows = []
for src in ['auto_bsw', 'auto_bsw2', 'auto_bsw3', 'auto_bsw4', 'auto_bsw5']:
    t0, s0, t1, s1, *_ = frames(src)
    tg, spd, yr, ax, gq, imu = GN[src]
    good = (gq == 4)
    vw = (s1['left_speed'] + s1['right_speed']) / 2
    vg = np.interp(t1, tg[good], spd[good]) * 3.6
    for lo in [2, 5, 10, 15, 20]:
        m = (vg >= lo) & (vg < lo + (3 if lo == 2 else 5))
        if m.sum() < 50: continue
        rows.append([src, f'{lo}-{lo + (3 if lo == 2 else 5)}', int(m.sum()), np.median(vw[m] / vg[m]), np.median(s1['left_speed'][m] / vg[m]), np.median(s1['right_speed'][m] / vg[m])])
vall = np.concatenate([(CAN[s][0x201][1]['left_speed']) for s in SRC if 0x201 in CAN[s]])
rows.append(['all', 'min nonzero 0x201 left_speed (km/h)', len(vall), float(np.min(vall[vall > 0])), '', ''])
wcsv('wheel_speed_vs_gnss.csv', ['source', 'gnss_speed_kph', 'n', 'ratio_avg', 'ratio_left', 'ratio_right'], rows,
     '3-7. 0x201 바퀴 속도 ÷ GNSS 속도. 시각은 Kvaser(0x201)와 bag 수신(GNSS, 지연 2 ms)')

# =====================================================================
# 4. 비상정지 이벤트
rows = []
for s in ['bsw', 'bsw2', 'bsw3', 'bsw4', 'bsw5']:
    for r in csv.DictReader(open(f'{RUN}/{s}/can_guard_events.csv')):
        rows.append([s, r['wall_s'], r['event'], r['detail']])
wcsv('canguard_events.csv', ['session', 'wall_s', 'event', 'detail'], rows, '4. can_guard 이벤트(원본 그대로)')
