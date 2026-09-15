#!/usr/bin/env python3
"""cyclictest(-q -h) 로그의 히스토그램을 스레드(코어)별 버킷으로 나눠 표로 보인다 — 체크리스트 1-6b 판정용.

  python3 tools/rt/hist_threads.py <로그> [<로그> …] [--base 8]
  python3 tools/rt/hist_threads.py measurements/2026-09-12_rt/soak_20260912_174708.log \\
          measurements/2026-09-14_rt/soak_20260914_212042.log tools/rt/logs/soak_2026….log

스레드 t 는 CPU base+t (격리 8-15 에서 재면 base=8). 로그마다 버킷별 스레드 횟수와
"200 µs 초과 합"(200–400 + 히스토그램 범위 초과)을 찍고, 길이가 다른 측정을 비교할 수 있게
30분 환산값(샘플 수 = 분 × 60,000 기준)도 같이 낸다.
"""
import argparse, os, sys

BANDS = [("50–100", 50, 100), ("100–200", 100, 200), ("200–400", 200, 400)]


def parse(path):
    hist, in_hist, r = [], False, {}
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("# Histogram") and "Overflow" not in line:
            in_hist = True; continue
        if line.startswith("# Total:"):
            in_hist = False; r["total"] = [int(x) for x in line.split()[2:]]
        elif line.startswith("# Max Latencies:"):
            r["max"] = [int(x) for x in line.split()[3:]]
        elif line.startswith("# Histogram Overflows:"):
            r["over"] = [int(x) for x in line.split()[3:]]
        elif in_hist and line[:1].isdigit():
            hist.append([int(x) for x in line.split()[1:]])
    if "max" not in r or not hist:
        sys.exit(f"{path}: cyclictest -h 결과 형식이 아님")
    n = len(r["max"])
    r["band"] = {name: [sum(hist[b][t] for b in range(lo, min(hi, len(hist)))) for t in range(n)] for name, lo, hi in BANDS}
    r["over200"] = [r["band"]["200–400"][t] + r["over"][t] for t in range(n)]
    r["minutes"] = max(r["total"]) / 60000.0
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--base", type=int, default=8, help="스레드 0 이 올라간 CPU 번호 (기본 8 = 격리 코어)")
    a = ap.parse_args()
    summary = []
    for path in a.logs:
        r = parse(path); n = len(r["max"])
        print(f"== {path}  ({r['minutes']:.0f}분, 스레드 {n}개, 최악 {max(r['max'])} µs)")
        print(f"{'버킷':<14}" + "".join(f"{'CPU'+str(a.base+t):>8}" for t in range(n)) + f"{'합계':>9}")
        rows = [(name, r["band"][name]) for name, _, _ in BANDS] + [(">400 (범위초과)", r["over"]), ("**200 µs 초과 합**", r["over200"]), ("최악 µs", r["max"])]
        for name, vals in rows:
            tot = "" if name == "최악 µs" else f"{sum(vals):>9,}"
            print(f"{name:<14}" + "".join(f"{v:>8,}" for v in vals) + tot)
        per30 = [v * 30 / r["minutes"] for v in r["over200"]]
        print(f"{'200+ /30분 환산':<14}" + "".join(f"{v:>8.1f}" for v in per30) + f"{sum(per30):>9.1f}")
        print()
        summary.append((os.path.basename(path), r["minutes"], sum(r["over200"]), sum(per30), max(r["max"]), sum(r["over"])))
    if len(summary) > 1:
        print("== 비교 (200 µs 초과 합 / 30분 환산 / 최악 / >400)")
        for name, mins, o, p, mx, ov in summary:
            print(f"  {name:<32} {mins:>5.0f}분  200+ {o:>6,}  /30분 {p:>7.1f}  최악 {mx:>4} µs  >400 {ov:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
