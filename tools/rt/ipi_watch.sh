#!/usr/bin/env bash
# 격리 코어에 도착하는 "함수 호출 IPI"(CAL) 가 어떤 커널 함수인지 N초 동안 잡는다 — 전역 동시 스톨의 기전 후보 확인용.
#   sudo bash tools/rt/ipi_watch.sh [초=120] [-- 명령…]     # 명령을 주면 추적 중에 실행 (예: -- bash tools/rt/provoke_net.sh 2 60)
# 결과: 격리 코어별 CAL 증가량 + csd_function_entry 함수별 횟수 (logs/ipi_<ts>.txt, 원본 trace 는 .trace)
set -uo pipefail
BASE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
sec=${1:-120}; shift || true; [[ "${1:-}" == "--" ]] && shift
[[ $EUID -eq 0 ]] || { echo "sudo 로 실행"; exit 1; }
T=/sys/kernel/tracing; [[ -d $T/events ]] || mount -t tracefs nodev $T
ISO=$(tr -d '[:space:]' < /sys/devices/system/cpu/isolated); [[ -n "$ISO" ]] || { echo "격리 코어 없음"; exit 1; }
mkdir -p "$BASE/logs"; out="$BASE/logs/ipi_$(date +%Y%m%d_%H%M%S)"
ev=events/csd/csd_function_entry; [[ -d $T/$ev ]] || { echo "tracepoint csd_function_entry 없음 (커널 $(uname -r))"; exit 1; }
echo 0 > $T/tracing_on; echo > $T/trace; echo nop > $T/current_tracer
echo 65536 > $T/buffer_size_kb
echo 1 > $T/$ev/enable
echo 1 > $T/events/ipi/ipi_send_cpumask/enable 2>/dev/null || true
before=$(grep -E "^ *CAL:" /proc/interrupts)
echo 1 > $T/tracing_on
echo "$(date '+%T') 추적 시작 ${sec}s (격리 $ISO)" | tee "$out.txt"
if (($#)); then ( "$@" ) & cmd_pid=$!; fi
sleep "$sec"
echo 0 > $T/tracing_on
[[ -n "${cmd_pid:-}" ]] && { kill "$cmd_pid" 2>/dev/null; wait "$cmd_pid" 2>/dev/null; }
after=$(grep -E "^ *CAL:" /proc/interrupts)
cat $T/trace > "$out.trace"; echo 0 > $T/$ev/enable; echo 0 > $T/events/ipi/ipi_send_cpumask/enable 2>/dev/null
python3 - "$ISO" "$before" "$after" "$out.trace" "$sec" <<'PY' | tee -a "$out.txt"
import sys, re, collections
iso, before, after, trace, sec = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
def expand(s):
    r=[]
    for p in s.split(","):
        a,_,b=p.partition("-"); r+= range(int(a), int(b or a)+1)
    return r
cpus=expand(iso)
hdr=open("/proc/interrupts").readline().split()
col={int(c[3:]):i+1 for i,c in enumerate(hdr)}
b=before.split(); a=after.split()
print(f"격리 코어 CAL 증가 ({sec}s):", " ".join(f"cpu{c}={int(a[col[c]])-int(b[col[c]])}" for c in cpus if c in col))
fn=collections.Counter(); per=collections.Counter(); senders=collections.Counter(); sendts=[]; t0=None
for l in open(trace, errors="ignore"):
    m=re.search(r"\[(\d{3})\].*csd_function_entry:.*func=(\S+)", l)
    if m and int(m.group(1)) in cpus: fn[m.group(2)]+=1; per[int(m.group(1))]+=1
    m2=re.search(r"^\s*(.+?)\s+\[(\d{3})\]\s+\S+\s+([\d.]+): ipi_send_cpumask: cpumask=([0-9a-f,]+) callsite=(\S+)", l)
    if m2:
        mask=int(m2.group(4).replace(",",""),16)
        if any(mask>>c & 1 for c in cpus):   # 격리 코어가 수신 대상에 포함된 송신만
            senders[(m2.group(1).strip()[:24], m2.group(5))]+=1
            sendts.append((float(m2.group(3)), m2.group(1).strip()[:24], bin(mask).count("1")))
ts_all=[float(x) for x in re.findall(r"\]\s+\S+\s+([\d.]+): ", open(trace, errors="ignore").read())]
t0=min(ts_all) if ts_all else 0
print("격리 코어에서 실행된 IPI 함수 (횟수):")
for f,n in fn.most_common(12): print(f"  {n:7d}  {f}")
if senders:
    print("격리 코어로 IPI 를 보낸 쪽 (프로세스, 호출 지점):")
    for (p,c),n in senders.most_common(10): print(f"  {n:7d}  {p:<24} {c}")
    print("송신 시각 (추적 시작 후 초, 보낸 프로세스, 대상 CPU 수):")
    for t,p,n in sendts: print(f"  +{t-t0:7.3f}s  {p:<24} → {n} CPU")
else: print("격리 코어로 보낸 IPI 없음")
PY
echo "원본: $out.trace"
