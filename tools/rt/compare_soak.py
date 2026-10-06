#!/usr/bin/env python3
"""cyclictest(-q -h) 로그 두 개를 비교해 체크리스트 1-1(대조군) 판정을 낸다.

  python3 tools/rt/compare_soak.py <RT 로그> <generic 로그>
  python3 tools/rt/compare_soak.py measurements/2026-09-12_rt/soak_20260912_174708.log tools/rt/logs/soak_2026….log

판정 규칙 (2026-09-14_can_verification_checklist.md 1-1):
  RT 유지 = generic 최악 > 2 × RT 최악  또는  generic 의 100 µs 초과 횟수 > 3 × RT 의 횟수
  그 외   = RT 가 비지원 NVIDIA 빌드를 감수할 만큼 낫지 않음 → generic + preempt=full 로 복귀
"""
import sys


def parse(path):
    hist, in_hist = {}, False
    maxes, overflow, avgs, total = [], 0, [], 0
    for line in open(path, encoding="utf-8", errors="replace"):
        if line.startswith("# Histogram") and "Overflow" not in line:
            in_hist = True; continue
        if line.startswith("# Total:"):
            in_hist = False; total = sum(int(x) for x in line.split()[2:])
        elif line.startswith("# Max Latencies:"):
            maxes = [int(x) for x in line.split()[3:]]
        elif line.startswith("# Avg Latencies:"):
            avgs = [int(x) for x in line.split()[3:]]
        elif line.startswith("# Histogram Overflows:"):
            overflow = sum(int(x) for x in line.split()[3:])
        elif in_hist and line[:1].isdigit():
            parts = line.split(); hist[int(parts[0])] = sum(int(x) for x in parts[1:])
    if not maxes:
        sys.exit(f"{path}: cyclictest 결과 형식이 아님")
    band = lambda lo, hi: sum(v for b, v in hist.items() if lo <= b < hi)
    return {
        "max": max(maxes), "maxes": maxes, "avg": sum(avgs) / len(avgs) if avgs else 0.0, "total": total,
        "50_100": band(50, 100), "100_200": band(100, 200), "200_400": band(200, 400), "over400": overflow,
        "over100": band(100, 400) + overflow,
    }


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    rt, gen = parse(sys.argv[1]), parse(sys.argv[2])
    rows = [("최악 (µs)", "max"), ("평균 (µs)", "avg"), ("샘플 수", "total"), ("50–100 µs", "50_100"),
            ("100–200 µs", "100_200"), ("200–400 µs", "200_400"), ("400 µs 초과", "over400"), ("100 µs 초과 합계", "over100")]
    print(f"{'항목':<16}{'RT':>14}{'generic+full':>16}")
    for label, k in rows:
        f = (lambda v: f"{v:.1f}") if k == "avg" else (lambda v: f"{v:,}")
        print(f"{label:<16}{f(rt[k]):>14}{f(gen[k]):>16}")
    print(f"{'스레드별 최악':<16}{str(rt['maxes']):>14}\n{'':<16}{str(gen['maxes']):>16}")
    rule_a = gen["max"] > 2 * rt["max"]
    rule_b = gen["over100"] > 3 * rt["over100"]
    print()
    print(f"규칙 A  generic 최악 {gen['max']} > 2×RT {2*rt['max']} ?  → {'예' if rule_a else '아니오'}")
    print(f"규칙 B  generic 100µs 초과 {gen['over100']} > 3×RT {3*rt['over100']} ?  → {'예' if rule_b else '아니오'}")
    if rule_a or rule_b:
        print("\n판정: **RT 유지** — generic 이 뚜렷이 나쁨 (D1)")
    else:
        print("\n판정: **generic + preempt=full 복귀 권고** — RT 의 이득이 비지원 NVIDIA 빌드를 감수할 만큼 크지 않음 (D1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
