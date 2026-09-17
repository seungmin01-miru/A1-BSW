#!/usr/bin/env python3
"""하우스키핑 코어의 클럭-시간 곡선(기울기·반응 시간)을 실측 — governor 튜닝 검증용 (root).
  sudo python3 tools/rt/ramp_probe.py [--cpu 0] [--idle 2] [--burst 3] [--period 0.02]
idle 초 유휴 → burst 초 동안 그 코어에 100 % 부하(파이썬 스핀, 해당 CPU 에 고정) → 다시 유휴. period 마다 MSR APERF/MPERF 로 실클럭을 찍고
  · 부하 시작 → 2,000 MHz 도달 시간, 최대 도달 시간, 최대 기울기(MHz/ms), 부하 종료 → 1,000 MHz 이하 복귀 시간 을 출력한다."""
import argparse, os, time, multiprocessing
MSR_APERF, MSR_MPERF, MSR_PLATFORM_INFO = 0xE8, 0xE7, 0xCE
def rd(cpu, reg):
    fd = os.open(f"/dev/cpu/{cpu}/msr", os.O_RDONLY)
    try: return int.from_bytes(os.pread(fd, 8, reg), "little")
    finally: os.close(fd)
def spin(cpu, sec):
    os.sched_setaffinity(0, {cpu}); t = time.time() + sec; x = 0
    while time.time() < t: x += 1
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--cpu", type=int, default=0); ap.add_argument("--idle", type=float, default=2)
    ap.add_argument("--burst", type=float, default=3); ap.add_argument("--period", type=float, default=0.02); a = ap.parse_args()
    os.sched_setaffinity(0, {c for c in range(os.cpu_count()) if c != a.cpu and c not in range(8, 16)} or {1})
    tsc = ((rd(0, MSR_PLATFORM_INFO) >> 8) & 0xFF) * 100
    gov = open(f"/sys/devices/system/cpu/cpu{a.cpu}/cpufreq/scaling_governor").read().strip()
    samples = []; t0 = time.time(); pa, pm = rd(a.cpu, MSR_APERF), rd(a.cpu, MSR_MPERF)
    p = multiprocessing.Process(target=spin, args=(a.cpu, a.burst)); started = False
    end = a.idle + a.burst + a.idle
    while True:
        time.sleep(a.period); now = time.time() - t0
        ca, cm = rd(a.cpu, MSR_APERF), rd(a.cpu, MSR_MPERF)
        mhz = tsc * (ca - pa) / (cm - pm) if cm != pm else 0; pa, pm = ca, cm
        samples.append((now, mhz))
        if not started and now >= a.idle: p.start(); started = True; tb = now
        if now >= end: break
    p.join()
    b = [(t - tb, m) for t, m in samples if t >= tb]
    after = [(t - (tb + a.burst), m) for t, m in samples if t >= tb + a.burst]
    t2000 = next((t for t, m in b if m >= 2000), None); mx = max(m for _, m in b); tmax = next(t for t, m in b if m >= mx * 0.97)
    slope = max(((b[i][1] - b[i-1][1]) / ((b[i][0] - b[i-1][0]) * 1000)) for i in range(1, len(b)))
    tdown = next((t for t, m in after if m <= 1000), None)
    print(f"cpu{a.cpu} governor={gov}  부하 {a.burst}s")
    print(f"  유휴 클럭 {samples[max(0,len(samples)//10)][1]:.0f} MHz → 최대 {mx:.0f} MHz")
    print(f"  2,000 MHz 도달 {t2000*1000:.0f} ms" if t2000 else "  2,000 MHz 미도달", f"| 최대(97 %) 도달 {tmax*1000:.0f} ms | 최대 상승 기울기 {slope:.0f} MHz/ms")
    print(f"  부하 종료 → 1,000 MHz 이하 복귀 {tdown*1000:.0f} ms" if tdown else "  부하 종료 후 1,000 MHz 이하로 안 내려옴")
    print("  곡선(ms:MHz):", " ".join(f"{t*1000:.0f}:{m:.0f}" for t, m in b[:: max(1, len(b)//25)]))
if __name__ == "__main__": main()
