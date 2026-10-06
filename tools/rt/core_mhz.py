#!/usr/bin/env python3
"""격리 코어의 '실제' 클럭과 SMI 횟수를 MSR 로 읽는다 (root + msr 모듈 필요).

  sudo python3 tools/rt/core_mhz.py                # 격리 코어(없으면 0-7), 1초 창
  sudo python3 tools/rt/core_mhz.py --cpus 8-15 --window 1

왜 필요한가: intel_pstate + HWP 에서는 sysfs `scaling_cur_freq` / `/proc/cpuinfo` 의 MHz 가
nohz_full 격리 코어에서 갱신되지 않는 자리표시자(policy->cur = 최소 800 MHz)를 돌려준다
(8시간 soak .thermal 의 2,866 샘플이 전부 800/800 이었던 이유). APERF/MPERF(MSR 0xE8/0xE7)
차분으로 계산한 값이 실제 평균 클럭이다. MSR 읽기는 해당 CPU 로 IPI 한 번(수 µs) — 격리 코어에
태스크를 올리지 않는다.

출력 한 줄: "<min MHz> <max MHz> <SMI 누적> cpu8=… cpu9=…"   (읽기 실패 시 "- - -")
"""
import argparse, os, struct, sys, time

MSR_PLATFORM_INFO, MSR_SMI_COUNT, MSR_MPERF, MSR_APERF = 0xCE, 0x34, 0xE7, 0xE8


def rdmsr(cpu, reg):
    fd = os.open(f"/dev/cpu/{cpu}/msr", os.O_RDONLY)
    try:
        return struct.unpack("<Q", os.pread(fd, 8, reg))[0]
    finally:
        os.close(fd)


def cpulist(s):
    out = []
    for p in s.split(","):
        if not p:
            continue
        a, _, b = p.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cpus", default=None, help="CPU 목록 (기본: /sys/devices/system/cpu/isolated, 없으면 0-7)")
    ap.add_argument("--window", type=float, default=1.0, help="측정 창(초)")
    a = ap.parse_args()
    cpus = a.cpus
    if cpus is None:
        try:
            cpus = open("/sys/devices/system/cpu/isolated").read().strip() or "0-7"
        except OSError:
            cpus = "0-7"
    cpus = [c for c in cpulist(cpus)
            if not os.path.exists(f"/sys/devices/system/cpu/cpu{c}/online")
            or open(f"/sys/devices/system/cpu/cpu{c}/online").read().strip() == "1"]   # offline 코어 제외
    try:
        tsc_mhz = ((rdmsr(0, MSR_PLATFORM_INFO) >> 8) & 0xFF) * 100   # 최대 비터보 배수 × 100 MHz 버스 = TSC
        before = {c: (rdmsr(c, MSR_APERF), rdmsr(c, MSR_MPERF)) for c in cpus}
        time.sleep(a.window)
        after = {c: (rdmsr(c, MSR_APERF), rdmsr(c, MSR_MPERF)) for c in cpus}
        smi = rdmsr(0, MSR_SMI_COUNT)
    except OSError as e:
        print("- - -")
        print(f"MSR 읽기 실패: {e} (sudo + modprobe msr 필요)", file=sys.stderr)
        return 1
    mhz = {}
    for c in cpus:
        da, dm = after[c][0] - before[c][0], after[c][1] - before[c][1]
        mhz[c] = int(tsc_mhz * da / dm) if dm > 0 else 0
    print(f"{min(mhz.values())} {max(mhz.values())} {smi} " + " ".join(f"cpu{c}={v}" for c, v in mhz.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
