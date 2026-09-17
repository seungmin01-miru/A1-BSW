#!/usr/bin/env python3
"""soak 로그의 400 µs 초과 사이클을 시각으로 바꾸고, 에피소드(≤5 ms 묶음)별로 provoke 로그·저널 사건과 대조한다.
  python3 tools/rt/episodes.py logs/soak_<ts>.log [--provoke logs/provoke_<ts>.log] [--window 30]
시작 시각은 같은 이름의 .run.log 의 "측정 시작 HH:MM:SS" 줄에서 읽는다(날짜는 파일명)."""
import argparse, re, subprocess, datetime as dt, os
ap = argparse.ArgumentParser(); ap.add_argument("log"); ap.add_argument("--provoke"); ap.add_argument("--window", type=int, default=30)
a = ap.parse_args()
run = a.log[:-4] + ".run.log"
m = re.search(r"soak_(\d{8})_(\d{6})", a.log); day = dt.datetime.strptime(m.group(1), "%Y%m%d")
start = None
for l in open(run, errors="ignore"):
    mm = re.search(r"측정 시작 (\d\d):(\d\d):(\d\d)", l)
    if mm: start = day.replace(hour=int(mm.group(1)), minute=int(mm.group(2)), second=int(mm.group(3))); break
if start is None: raise SystemExit("run.log 에서 측정 시작 시각을 못 찾음")
cyc = {}
for l in open(a.log):
    mm = re.match(r"# Thread (\d+): (.*)", l)
    if mm: cyc[int(mm.group(1))] = [int(x) for x in mm.group(2).split()]
allc = sorted((c, t) for t, cs in cyc.items() for c in cs)
ep = []
for c, t in allc:
    if ep and c - ep[-1][-1][0] <= 5: ep[-1].append((c, t))
    else: ep.append([(c, t)])
print(f"{os.path.basename(a.log)}: 400 µs 초과 {len(allc)}건 → 에피소드 {len(ep)}개 (시작 {start:%F %T})")
prov = []
if a.provoke:
    for l in open(a.provoke):
        mm = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(\.\d+)? (#\d+ .*)", l)
        if mm: prov.append((dt.datetime.strptime(mm.group(1), "%Y-%m-%d %H:%M:%S"), mm.group(3)))
for e in ep:
    t0 = start + dt.timedelta(milliseconds=e[0][0]); cores = sorted({t + 8 for _, t in e})
    print(f"\n  {t0:%T}  코어 {cores}  ({len(e)}건)")
    for pt, what in prov:
        d = (t0 - pt).total_seconds()
        if -a.window <= d <= a.window: print(f"     provoke {pt:%T} {what}  ({d:+.0f} s 전)")
    since = (t0 - dt.timedelta(seconds=a.window)).strftime("%F %T"); until = (t0 + dt.timedelta(seconds=a.window)).strftime("%F %T")
    j = subprocess.run(["journalctl", "--since", since, "--until", until, "--no-pager", "-o", "short-iso"], capture_output=True, text=True).stdout
    for l in j.splitlines():
        if any(k in l for k in ("cyclictest", "stress-ng", "glmark")): continue
        print("     journal", l[11:19], l[34:140])
