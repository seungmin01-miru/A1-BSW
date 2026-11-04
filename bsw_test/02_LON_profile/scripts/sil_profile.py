#!/usr/bin/env python3
"""02 LON-PROF SIL — vcan0 에 can_guard 를 띄우고 profile_gen.py 를 같은 설정으로 2회 돌려 goal.md 기준을 본다.

  python3 bsw_test/02_LON_profile/scripts/sil_profile.py [--profile gen_check.yaml] [--var acc=6] [--channel vcan0]

| 기준(goal.md) | 여기서 보는 것 |
|---|---|
| 상한 초과 명령 0건       | profile_gen check 통과 + 버스 0x210 이 limits 밖으로 안 나감 |
| 명령 로그 = 0x210 (1주기) | 버스의 모든 0x210 이 "그 직전에 쓴 명령" 또는 "한 주기 전 명령"과 같음(인코딩 반올림 후) |
| 반복 재현성              | 1·2회 실행 단계 명령열이 완전히 같음 |
결과는 --out(기본 bsw_test/02_LON_profile/results/sil_<시각>/) 에 cmd.csv·0x210 기록·summary.txt.
"""
import argparse
import csv
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
from sil_tests._common import can_guard_cmd, wait_for_shm  # noqa: E402

GEN = os.path.join(REPO, 'bsw_test', 'common', 'scripts', 'profile_gen.py')


def enc(steer, brake, acc):
    """tx_encode 와 같은 반올림(조향 0.1°, % 정수)."""
    return (int(round(steer * 10)), int(round(brake)), int(round(acc)))


def one_run(a, idx, out):
    tag = uuid.uuid4().hex[:8]
    cmd_shm, hb_shm = f'p_cmd_{tag}', f'p_hb_{tag}'
    frames, stop = [], threading.Event()
    bus = can.Bus(channel=a.channel, interface='socketcan', receive_own_messages=False)

    def rx():
        while not stop.is_set():
            m = bus.recv(timeout=0.05)
            if m is not None and m.arbitration_id == 0x210:
                d = bytes(m.data)
                frames.append((time.monotonic(), int.from_bytes(d[0:2], 'little', signed=True),
                               int.from_bytes(d[2:4], 'little'), d[4], d[5] & 7))
    th = threading.Thread(target=rx, daemon=True)
    th.start()
    guard = subprocess.Popen(can_guard_cmd(cmd_shm, hb_shm, a.channel, 0.05, 0.02, ('--quiet',)),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not (wait_for_shm(cmd_shm) and wait_for_shm(hb_shm)):
            raise RuntimeError(f'can_guard 공유메모리 안 생김 — {a.channel} UP 인지 확인')
        run = f'sil_r{idx}'
        argv = [sys.executable, GEN, 'run', a.profile, '--sil', '--run', run, '--out', out,
                '--cmd-shm', cmd_shm, '--hb-shm', hb_shm] + sum((['--var', v] for v in a.var), [])
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        with open(os.path.join(out, f'{run}_stdout.txt'), 'w') as f:
            f.write(r.stdout + r.stderr)
        if r.returncode != 0:
            raise RuntimeError(f'profile_gen 실패 rc={r.returncode}\n{r.stderr[-2000:]}')
        time.sleep(0.3)
    finally:
        guard.send_signal(2)
        try:
            guard.wait(timeout=3)
        except subprocess.TimeoutExpired:
            guard.kill()
        stop.set()
        th.join(timeout=1)
        bus.shutdown()
    with open(os.path.join(out, f'{run}_cmd.csv')) as f:
        rows = list(csv.DictReader(f))
    with open(os.path.join(out, f'{run}_0x210.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['mono_s', 'steer_raw', 'brake_pct', 'acc_pct', 'auto_bits'])
        w.writerows(frames)
    return rows, frames


def check_match(rows, frames, period):
    """각 0x210 프레임 = 그 직전에 쓴 명령(row[j]) 또는 한 주기 전(row[j-1]) 인가."""
    import bisect
    ts = [float(r['mono_s']) for r in rows]
    exp = [enc(float(r['steer_cmd_deg']), float(r['brake_cmd_pct']), float(r['acc_cmd_pct'])) for r in rows]
    t_end = ts[-1] + 0.03   # 생성기 종료 뒤 HOLDING 프레임은 제외
    n = same = prev1 = 0
    bad = []
    for t, s, b, ac, au in frames:
        if t < ts[0] or t > t_end:
            continue
        j = bisect.bisect_right(ts, t) - 1
        if j < 0:
            continue
        n += 1
        got = (s, b, ac)
        if got == exp[j]:
            same += 1
        elif j >= 1 and got == exp[j - 1]:
            prev1 += 1
        else:
            bad.append((round(t - ts[0], 3), got, exp[j], exp[j - 1] if j else None))
    return n, same, prev1, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--profile', default=os.path.join(REPO, 'bsw_test', '02_LON_profile', 'profiles', 'gen_check.yaml'))
    ap.add_argument('--var', action='append', default=[])
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    if not os.path.isabs(a.profile) and not os.path.exists(a.profile):
        a.profile = os.path.join(REPO, 'bsw_test', '02_LON_profile', 'profiles', a.profile)
    out = a.out or os.path.join(REPO, 'bsw_test', '02_LON_profile', 'results',
                                f'sil_{time.strftime("%Y%m%d_%H%M%S")}_{os.uname().nodename.split(".")[0]}')
    os.makedirs(out, exist_ok=True)

    chk = subprocess.run([sys.executable, GEN, 'check', a.profile] + sum((['--var', v] for v in a.var), []),
                         capture_output=True, text=True)
    lines = [f'# 02 LON-PROF SIL — {time.strftime("%F %T")}  {os.path.basename(a.profile)} {a.var}',
             f'커밋 {subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()}',
             '', chk.stdout.strip()]
    results = []
    ok_check = chk.returncode == 0
    results.append(('설정 검사(상한·조향 속도·동시 입력)', ok_check, '' if ok_check else chk.stdout[-500:]))
    runs = []
    if ok_check:
        for i in (1, 2):
            runs.append(one_run(a, i, out))
        # 1주기 일치
        for i, (rows, frames) in enumerate(runs, 1):
            n, same, prev1, bad = check_match(rows, frames, 0.02)
            ok = n > 0 and not bad
            results.append((f'r{i} 0x210 = 명령 로그(1주기 안)', ok,
                            f'프레임 {n}: 직전 명령 {same}, 한 주기 전 {prev1}, 불일치 {len(bad)}'
                            + (f' 예 {bad[:3]}' if bad else '')))
        # 재현성: 실행 단계 명령열
        seqs = [[(r['seg'], r['steer_cmd_deg'], r['brake_cmd_pct'], r['acc_cmd_pct'])
                 for r in rows if r['phase'] == 'run'] for rows, _ in runs]
        same = seqs[0] == seqs[1] and len(seqs[0]) > 0
        results.append(('반복 재현성(실행 단계 명령열 r1 = r2)', same, f'r1 {len(seqs[0])} / r2 {len(seqs[1])} 주기'))
        # 상한: 버스 0x210 이 limits 밖으로 안 나감
        import yaml
        sys.path.insert(0, os.path.dirname(GEN))
        import profile_lib as pl
        with open(a.profile, encoding='utf-8') as f:
            prof = pl.build(yaml.safe_load(f), pl.parse_var_overrides(a.var))
        L = prof.limits
        over = [fr for _, frames in runs for fr in frames
                if abs(fr[1]) > L['steer_deg'] * 10 + 0.5 or fr[2] > L['brake_pct'] + 0.5 or fr[3] > L['acc_pct'] + 0.5]
        results.append(('버스 0x210 상한 초과 0건', not over, f'{len(over)} 건'))
    lines += ['', '| 항목 | 결과 | 내용 |', '|---|---|---|']
    lines += [f'| {n} | {"✅ PASS" if ok else "❌ FAIL"} | {d} |' for n, ok, d in results]
    fails = sum(not ok for _, ok, _ in results)
    lines += ['', f'전체: {"PASS" if fails == 0 else f"FAIL {fails}건"}  (결과 폴더 {out})']
    text = '\n'.join(lines)
    with open(os.path.join(out, 'summary.md'), 'w') as f:
        f.write(text + '\n')
    print(text)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
