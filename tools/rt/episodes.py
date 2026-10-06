#!/usr/bin/env python3
"""soak 로그의 400 µs 초과 사이클을 시각으로 바꾸고, 에피소드(≤5 ms 묶음)별로 provoke 로그·저널 사건과 대조한다.
  python3 tools/rt/episodes.py logs/soak_<ts>.log [--provoke logs/provoke_<ts>.log] [--window 30]
  python3 tools/rt/episodes.py latest          # 가장 최근 soak + 짝이 되는 provoke 로그 자동 선택
시작 시각은 같은 이름의 .run.log 의 "측정 시작 HH:MM:SS" 줄에서 읽는다(날짜는 파일명)."""
import argparse, re, subprocess, datetime as dt, os
ap = argparse.ArgumentParser(); ap.add_argument("log"); ap.add_argument("--provoke"); ap.add_argument("--window", type=int, default=30)
a = ap.parse_args()
LOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
if a.log == "latest":   # 가장 최근 soak 로그 + 그 이후 시작된 가장 최근 provoke 로그
    a.log = max((os.path.join(LOGS, f) for f in os.listdir(LOGS) if re.match(r"soak_\d{8}_\d{6}\.log$", f)), key=os.path.getmtime)
    if a.provoke is None:
        cands = [os.path.join(LOGS, f) for f in os.listdir(LOGS) if re.match(r"provoke_\d{8}_\d{6}\.log$", f)]
        if cands:
            newest = max(cands, key=os.path.getmtime)
            if os.path.getmtime(newest) >= os.path.getmtime(a.log) - 900: a.provoke = newest
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
therm = []
tf = a.log[:-4] + ".thermal"
if os.path.exists(tf):
    for l in open(tf):
        f = l.split()
        if f and re.match(r"\d\d:\d\d:\d\d", f[0]):
            hh, mm_, ss = map(int, f[0].split(":")); tt = day.replace(hour=hh, minute=mm_, second=ss)
            if tt < start - dt.timedelta(minutes=1): tt += dt.timedelta(days=1)
            therm.append((tt, l.rstrip()))
for e in ep:
    t0 = start + dt.timedelta(milliseconds=e[0][0]); cores = sorted({t + 8 for _, t in e})
    print(f"\n  {t0:%T}  코어 {cores}  ({len(e)}건)")
    for tt, row in therm:
        d = (tt - t0).total_seconds()
        if 0 <= d <= 12: print(f"     thermal {row}  (+{d:.0f} s 표본: 그 10초 창의 W·클럭·제한사유)"); break
    for pt, what in prov:
        d = (t0 - pt).total_seconds()
        if -a.window <= d <= a.window: print(f"     provoke {pt:%T} {what}  ({d:+.0f} s 전)")
    since = (t0 - dt.timedelta(seconds=a.window)).strftime("%F %T"); until = (t0 + dt.timedelta(seconds=a.window)).strftime("%F %T")
    j = subprocess.run(["journalctl", "--since", since, "--until", until, "--no-pager", "-o", "short-iso"], capture_output=True, text=True).stdout
    for l in j.splitlines():
        if any(k in l for k in ("cyclictest", "stress-ng", "glmark")): continue
        print("     journal", l[11:19], l[34:140])
