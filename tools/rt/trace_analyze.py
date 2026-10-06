#!/usr/bin/env python3
"""a1_rt.sh trace 가 남긴 .ftrace 를 읽어 스파이크 직전 격리 코어에서 무슨 일이 있었는지 정리한다.

  python3 tools/rt/trace_analyze.py tools/rt/logs/trace_<시각>.ftrace [--iso 8-15] [--window 2.0]

- cyclictest 는 임계 초과를 만난 스레드가 tracing_on 을 0 으로 내리므로, 기록의 '끝' 이 스파이크 순간이다.
- 끝에서 --window ms 이내의 이벤트를 격리 코어 / 그 외로 나눠 세고, 격리 코어에서 일어난 것을 시간순으로 보여준다.
"""
import argparse, re, sys
from collections import Counter

LINE = re.compile(r"^\s*(?P<comm>.+?)-(?P<pid>\d+)\s+\[(?P<cpu>\d{3})\]\s+(?P<flags>\S+)\s+(?P<ts>\d+\.\d+):\s+(?P<ev>\w+):\s*(?P<rest>.*)$")


def cpuset(txt):
    out = set()
    for part in txt.split(","):
        lo, _, hi = part.partition("-"); out.update(range(int(lo), int(hi or lo) + 1))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ftrace"); ap.add_argument("--iso", default="8-15"); ap.add_argument("--window", type=float, default=2.0, help="끝에서 몇 ms 를 볼지")
    a = ap.parse_args()
    iso = cpuset(a.iso)
    evs = []
    for line in open(a.ftrace, errors="replace"):
        m = LINE.match(line)
        if m:
            evs.append((float(m["ts"]), int(m["cpu"]), m["comm"].strip(), m["ev"], m["rest"].strip()))
    if not evs:
        sys.exit("이벤트를 못 읽음 — 추적이 발동하지 않았거나 형식이 다름")
    t_end = evs[-1][0]; t0 = t_end - a.window / 1000.0
    win = [e for e in evs if e[0] >= t0]
    print(f"기록 {len(evs)}건, 마지막 {a.window} ms 창: {len(win)}건 (끝 = 스파이크 순간 ≈ {t_end:.6f}s)")

    on_iso = [e for e in win if e[1] in iso]
    print(f"\n### 창 안에서 격리 코어({a.iso})에 들어온 이벤트 {len(on_iso)}건 — 종류별")
    c = Counter()
    for ts, cpu, comm, ev, rest in on_iso:
        key = ev
        if ev == "irq_handler_entry":
            key = "irq:" + (re.search(r"name=(\S+)", rest) or [None, "?"])[1]
        elif ev == "ipi_entry":
            key = "ipi:" + rest.strip("()")
        elif ev == "sched_switch":
            nxt = re.search(r"next_comm=(\S+)", rest); key = "switch→" + (nxt[1] if nxt else "?")
        elif ev == "softirq_entry":
            key = "softirq:" + (re.search(r"action=(\w+)", rest) or [None, "?"])[1]
        c[key] += 1
    for k, v in c.most_common(12):
        print(f"  {v:5d}  {k}")

    print(f"\n### 격리 코어 이벤트 시간순 (마지막 30건, 시각은 스파이크 기준 -µs)")
    for ts, cpu, comm, ev, rest in on_iso[-30:]:
        print(f"  -{(t_end - ts)*1e6:8.0f} µs  CPU{cpu:<3d} {comm:<16.16s} {ev:<18s} {rest[:70]}")

    senders = Counter()
    for ts, cpu, comm, ev, rest in win:
        if ev == "ipi_send_cpu" and cpu not in iso:
            tgt = re.search(r"cpu=(\d+)", rest)
            if tgt and int(tgt[1]) in iso:
                senders[(cpu, comm)] += 1
    if senders:
        print(f"\n### 창 안에서 격리 코어로 IPI 를 보낸 쪽 (CPU, 프로세스)")
        for (cpu, comm), v in senders.most_common(8):
            print(f"  {v:5d}  CPU{cpu} {comm}")
    else:
        print("\n(창 안에 격리 코어를 향한 ipi_send_cpu 기록 없음)")


if __name__ == "__main__":
    main()
