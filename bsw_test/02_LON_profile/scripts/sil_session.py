#!/usr/bin/env python3
"""02 세션 SIL — vcan0 에 가짜 차량(--physics) + can_guard 를 띄우고 profile_gen 을 "한 세션 여러 회차 + 자율 U턴"으로 돌린다.

  python3 bsw_test/02_LON_profile/scripts/sil_session.py [--channel vcan0] [--only A|B]

| 시나리오 | 내용 |
|---|---|
| A 폐루프 | speed_step.yaml (0→10 km/h, 8 s) × 2회, 사이 U턴 폐루프 5 km/h, 조향 오프셋 +1.0°, --ctrl speed_ctrl.yaml |
| B 개루프 | align_straight.yaml (acc 10 %, 코스 50 m) × 2회, 사이 U턴 개루프 acc 10 %, 조향 오프셋 −1.0° |

| 확인 | 기준 |
|---|---|
| 세션 완주 | rc 0, 시험 회차마다 "프로파일 완료"/"코스 끝 예측", U턴마다 "방위 ≥ 180", "세션 중단" 없음 |
| can_guard 계속 ACTIVE | 세션 동안 0x210 간격 ≤ 60 ms, auto 비트 111 유지(재시작·STOPPED 없음) |
| 가속·브레이크 동시 0건 | 버스 0x210 |
| 명령 로그 = 0x210 (1주기) | sil_profile.py 와 같은 판정 |
| 조향 오프셋 적용 | 시험 회차 실행 중 0x210 조향 = 오프셋 |
| 회차 사이 브레이크 유지 | 대기(pre) 중 명령 브레이크 ≥ 30 %, 가속 0 |
| 코스 | B: 시험 회차 이동거리(후륜 적분, 제동 포함) ≤ 50 m |
| 출발 안 함 감지 | C: 불감대 아래 acc 3 % → 5 s 안에 그 회차 끝내기, 세션은 다음 회차로 |
| 속도 추종(로직 확인) | A: 계단 마지막 3 s 평균 |오차| ≤ 1 km/h, 최고 ≤ 목표 × 1.1 + 0.5 |
| U턴 방위 | 가짜 차량 자세 로그로 방위 변화 180 ± 15° |

**가짜 차량 물리는 실차가 아니다** — 로직(닫힘·멈춤·이어짐)만 본다. 실차 판정에 쓰지 않는다.
"""
import argparse
import csv
import glob
import os
import subprocess
import sys
import threading
import time
import uuid

import can

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'safety', 'can_guard'))
sys.path.insert(0, HERE)
from sil_tests._common import can_guard_cmd, wait_for_shm  # noqa: E402
from sil_profile import check_match  # noqa: E402

GEN = os.path.join(REPO, 'bsw_test', 'common', 'scripts', 'profile_gen.py')
FAKE = os.path.join(REPO, 'sil', 'vcan', 'fake_a1_vehicle.py')
P = os.path.join(REPO, 'bsw_test', '02_LON_profile', 'profiles')
CTRL = os.path.join(REPO, 'bsw_test', '02_LON_profile', 'ctrl', 'speed_ctrl.yaml')

SCENARIOS = {
    'A': dict(desc='폐루프 계단 0→10 km/h ×2 + U턴 폐루프 5 km/h, 오프셋 +1.0°',
              profile='speed_step.yaml', var=['v=10', 't=8'], reps=2, offset=1.0,
              turn_var=['acc=0', 'kph=5'], ctrl=True, v_ref=10.0),
    'B': dict(desc='개루프 직진 acc 10 % 코스 50 m ×2 + U턴 개루프 acc 10 %, 오프셋 −1.0°',
              profile='align_straight.yaml', var=['acc=10'], reps=2, offset=-1.0,
              turn_var=['acc=10'], ctrl=False, course=50.0),
    'C': dict(desc='출발 안 함 감지: 직진 acc 3 %(가짜 차량 불감대 5 % 아래) ×2, U턴 없음',
              profile='align_straight.yaml', var=['acc=3'], reps=2, offset=0.0, turn=False,
              turn_var=[], ctrl=False, expect_stall=True),
}


def read_csv(path):
    with open(path) as f:
        return list(csv.DictReader(f))


def run_scenario(key, sc, a, out):
    tag = uuid.uuid4().hex[:8]
    cmd_shm, hb_shm = f's_cmd_{tag}', f's_hb_{tag}'
    d = os.path.join(out, key)
    os.makedirs(d, exist_ok=True)
    frames, stop = [], threading.Event()
    bus = can.Bus(channel=a.channel, interface='socketcan', receive_own_messages=False)

    def rx():
        while not stop.is_set():
            m = bus.recv(timeout=0.05)
            if m is not None and m.arbitration_id == 0x210:
                b = bytes(m.data)
                frames.append((time.monotonic(), int.from_bytes(b[0:2], 'little', signed=True),
                               int.from_bytes(b[2:4], 'little'), b[4], b[5] & 7))
    th = threading.Thread(target=rx, daemon=True)
    th.start()
    fake = subprocess.Popen([sys.executable, FAKE, '--channel', a.channel, '--physics',
                             '--pose-log', os.path.join(d, 'pose.csv')],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    guard = subprocess.Popen(can_guard_cmd(cmd_shm, hb_shm, a.channel, 0.05, 0.02, ('--quiet',)),
                             stdout=subprocess.DEVNULL, stderr=open(os.path.join(d, 'can_guard_stderr.txt'), 'w'))
    rc = None
    try:
        if not (wait_for_shm(cmd_shm) and wait_for_shm(hb_shm)):
            raise RuntimeError(f'can_guard 공유메모리 안 생김 — {a.channel} UP 인지 확인')
        time.sleep(0.5)
        argv = [sys.executable, GEN, 'run', os.path.join(P, sc['profile']), '--sil', '--run', f'sil{key}',
                '--out', d, '--cmd-shm', cmd_shm, '--hb-shm', hb_shm, '--interface', 'socketcan',
                '--channel', a.channel, '--reps', str(sc['reps']), '--steer-offset', str(sc['offset'])]
        if sc.get('turn', True):
            argv += ['--turn', os.path.join(P, 'uturn.yaml')]
        argv += sum((['--var', v] for v in sc['var']), [])
        argv += sum((['--turn-var', v] for v in sc['turn_var']), [])
        if sc['ctrl']:
            argv += ['--ctrl', CTRL]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        rc = r.returncode
        with open(os.path.join(d, 'profile_gen_stdout.txt'), 'w') as f:
            f.write(r.stdout + r.stderr)
        time.sleep(0.3)
    finally:
        for p in (guard, fake):
            p.send_signal(2)
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        stop.set()
        th.join(timeout=1)
        bus.shutdown()
    with open(os.path.join(d, '0x210.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['mono_s', 'steer_raw', 'brake_pct', 'acc_pct', 'auto_bits'])
        w.writerows(frames)
    return d, rc, frames


def judge(key, sc, d, rc, frames):
    res = []
    glog = open(glob.glob(os.path.join(d, '*_gen.log'))[0], encoding='utf-8').read()
    names = [f'sil{key}_r{i + 1}' for i in range(sc['reps'])]
    turns = [f'{n}_turn' for n in names[:-1]] if sc.get('turn', True) else []
    if sc.get('expect_stall'):
        n_stall = glog.count('출발 안 함')
        rows = [r for n in names for r in read_csv(os.path.join(d, f'{n}_cmd.csv'))]
        t_run = [float(r['t_prof_s']) for r in rows if r['phase'] == 'run' and r['t_prof_s']]
        ok = rc == 0 and n_stall == len(names) and '세션 중단' not in glog and max(t_run) <= 6.0
        return [('출발 안 함 → 5 s 안에 회차 끝, 다음 회차 진행', ok,
                 f'rc {rc}, 감지 {n_stall}/{len(names)}, 실행 단계 최대 {max(t_run):.1f} s')]
    done = [n for n in names if f'{n} 끝내기 단계: 프로파일 완료' in glog or f'{n} 끝내기 단계: 코스 끝 예측' in glog]
    turned = glog.count('U턴: 방위')
    ok = rc == 0 and len(done) == len(names) and turned == len(turns) and '세션 중단' not in glog
    res.append(('세션 완주', ok, f'rc {rc}, 시험 끝 {len(done)}/{len(names)}, U턴 {turned}/{len(turns)}'))

    rows_all = []
    for n in names + turns:
        rows_all += read_csv(os.path.join(d, f'{n}_cmd.csv'))
    rows_all.sort(key=lambda r: float(r['mono_s']))
    t0, t1 = float(rows_all[0]['mono_s']), float(rows_all[-1]['mono_s'])
    fr = [f for f in frames if t0 + 0.1 <= f[0] <= t1]
    gaps = [b[0] - a_[0] for a_, b in zip(fr, fr[1:])]
    gmax = max(gaps) if gaps else 9
    autos = {f[4] for f in fr}
    res.append(('can_guard 계속 ACTIVE(0x210 ≤ 60 ms, auto 111)', gmax <= 0.06 and autos == {7},
                f'프레임 {len(fr)}, 최대 간격 {gmax * 1e3:.0f} ms, auto {sorted(autos)}'))
    both = [f for f in fr if f[2] > 0 and f[3] > 0]
    res.append(('가속·브레이크 동시 0건', not both, f'{len(both)} 건'))
    n, same, prev1, bad = check_match(rows_all, frames, 0.02)
    res.append(('0x210 = 명령 로그(1주기 안)', n > 0 and not bad,
                f'프레임 {n}: 직전 {same}, 한 주기 전 {prev1}, 불일치 {len(bad)}' + (f' 예 {bad[:2]}' if bad else '')))
    off_raw = int(round(sc['offset'] * 10))
    test_rows = [r for nm in names for r in read_csv(os.path.join(d, f'{nm}_cmd.csv')) if r['phase'] == 'run']
    ts = [float(r['mono_s']) for r in test_rows]
    off_frames = [f for f in fr if any(abs(f[0] - t) < 0.01 for t in ts[::25])]
    off_bad = [f for f in off_frames if f[1] != off_raw]
    res.append(('조향 오프셋 적용(시험 중 0x210 조향)', bool(off_frames) and not off_bad,
                f'{sc["offset"]:+g}° → raw {off_raw}, 표본 {len(off_frames)}, 다름 {len(off_bad)}'))
    pre = [r for r in rows_all if r['phase'] == 'pre' and int(r['item']) > 0]
    pre_bad = [r for r in pre if float(r['brake_cmd_pct']) < 30 or float(r['acc_cmd_pct']) > 0]
    res.append(('회차 사이 브레이크 유지(≥ 30 %, 가속 0)', bool(pre) and not pre_bad,
                f'대기 {len(pre)} 주기, 위반 {len(pre_bad)}'))
    if sc.get('course'):
        dmax = [max(float(r['dist_m']) for r in read_csv(os.path.join(d, f'{nm}_cmd.csv'))) for nm in names]
        res.append((f'코스 {sc["course"]:g} m 안에서 정지(후륜 적분)', all(x <= sc['course'] for x in dmax),
                    ', '.join(f'{x:.1f} m' for x in dmax)))
    if sc.get('v_ref'):
        msg, ok = [], True
        for nm in names:
            rr = [r for r in read_csv(os.path.join(d, f'{nm}_cmd.csv')) if r['label'] == '계단' and r['phase'] == 'run']
            tl = float(rr[-1]['t_prof_s'])
            last = [r for r in rr if float(r['t_prof_s']) >= tl - 3]
            mae = sum(abs(float(r['speed_kph']) - sc['v_ref']) for r in last) / len(last)
            vmax = max(float(r['speed_kph']) for r in rr)
            ok &= mae <= 1.0 and vmax <= sc['v_ref'] * 1.1 + 0.5
            msg.append(f'{nm}: 오차 {mae:.2f} km/h, 최고 {vmax:.1f}')
        res.append(('속도 추종(가짜 차량, 로직 확인)', ok, '; '.join(msg)))
    pose = read_csv(os.path.join(d, 'pose.csv'))
    pt = [float(p['mono_s']) for p in pose]
    msg, ok = [], bool(turns)
    import bisect
    for nm in turns:
        rr = [r for r in read_csv(os.path.join(d, f'{nm}_cmd.csv')) if r['phase'] == 'run']
        ta, tb = float(rr[0]['mono_s']), float(rr[-1]['mono_s'])
        ya = float(pose[max(bisect.bisect_left(pt, ta) - 1, 0)]['yaw_deg'])
        yb = float(pose[min(bisect.bisect_left(pt, tb), len(pose) - 1)]['yaw_deg'])
        dy = abs(yb - ya)
        ok &= abs(dy - 180) <= 15
        msg.append(f'{nm}: {dy:.0f}°')
    res.append(('U턴 방위 180 ± 15°(가짜 차량 자세)', ok, ', '.join(msg)))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--only', default='', help='A 또는 B 만')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    out = a.out or os.path.join(REPO, 'bsw_test', '02_LON_profile', 'results',
                                f'sil_session_{time.strftime("%Y%m%d_%H%M%S")}_{os.uname().nodename.split(".")[0]}')
    os.makedirs(out, exist_ok=True)
    commit = subprocess.run(['git', '-C', REPO, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', REPO, 'diff', '--quiet', '--', 'bsw_test', 'sil']).returncode != 0
    lines = [f'# 02 세션 SIL — {time.strftime("%F %T")} {os.uname().nodename}',
             f'커밋 {commit}{" (+수정 있음)" if dirty else ""}  python {sys.version.split()[0]}  python-can {can.__version__}',
             '가짜 차량 물리는 실차가 아님 — 로직 확인용']
    fails = 0
    for key, sc in SCENARIOS.items():
        if a.only and key != a.only:
            continue
        print(f'▶ {key}: {sc["desc"]}', flush=True)
        d, rc, frames = run_scenario(key, sc, a, out)
        res = judge(key, sc, d, rc, frames)
        lines += ['', f'## {key}. {sc["desc"]}', '', '| 확인 | 결과 | 내용 |', '|---|---|---|']
        lines += [f'| {n} | {"✅ PASS" if ok else "❌ FAIL"} | {m} |' for n, ok, m in res]
        fails += sum(not ok for _, ok, _ in res)
    lines += ['', f'전체: {"PASS" if fails == 0 else f"FAIL {fails}건"}  (결과 폴더 {out})']
    text = '\n'.join(lines)
    with open(os.path.join(out, 'summary.md'), 'w') as f:
        f.write(text + '\n')
    print(text)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
