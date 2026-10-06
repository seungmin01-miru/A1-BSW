#!/usr/bin/env bash
# 전력·열 표본: RAPL(패키지/코어/uncore) 평균 전력, 패키지 온도, 격리·하우스키핑 실클럭(MSR), 유휴 상태 요약.
# 부팅 옵션별 비용 비교용 (generic 기본 / RT 튜닝 / rt-poll / generic-poll).
#   sudo bash power_sample.sh [초=30] [라벨]
# 출력 한 줄을 logs/power_<라벨>_<ts>.txt 에도 남김. 부하 없이 유휴 상태에서 재는 것이 기본.
set -u   # -e/pipefail 금지: 보조 값(온도·클럭) 하나가 실패해도 전력 줄은 반드시 출력
BASE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
sec=${1:-30}; label=${2:-$(uname -r)}
[[ $EUID -eq 0 ]] || { echo "sudo 로 실행"; exit 1; }
R=/sys/class/powercap/intel-rapl/intel-rapl:0
rd() { cat "$1" 2>/dev/null || echo 0; }
e0=$(rd $R/energy_uj); c0=$(rd $R/intel-rapl:0:0/energy_uj)

t0=$(date +%s.%N)
sleep "$sec"
t1=$(date +%s.%N)
e1=$(rd $R/energy_uj); c1=$(rd $R/intel-rapl:0:0/energy_uj)
dt=$(python3 -c "print($t1-$t0)")
pw() { python3 -c "d=$2-$1; d=d if d>=0 else d+2**32; print(f'{d/1e6/$dt:.1f}')"; }
pkg=$(pw "$e0" "$e1"); core=$(pw "$c0" "$c1")
temp=$(awk '{printf "%.0f",$1/1000}' /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1); temp=${temp:-?}   # x86_pkg_temp (sensors 패키지 없음)
idle=$(ls /sys/devices/system/cpu/cpu0/cpuidle 2>/dev/null | tr "\n" " " || true); [[ -n "$idle" ]] || idle="none(idle=poll)"
drv=$(rd /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver)/$(rd /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)
clk="iso:$(python3 "$BASE/core_mhz.py" --window 1 2>/dev/null | tr "\n" " " | cut -c1-120) hk:$(python3 "$BASE/core_mhz.py" --cpus 0-7,16-19 --window 1 2>/dev/null | tr "\n" " " | cut -c1-140)"
cmd=$(sed 's/.*vt.handoff=7//' /proc/cmdline)
line="$(date '+%F %T') [$label] ${sec}s pkg=${pkg}W core=${core}W pkgTemp=${temp}C cpuidle=[$idle] cpufreq=$drv | $clk | cmdline:$cmd"
echo "$line"; mkdir -p "$BASE/logs"; echo "$line" >> "$BASE/logs/power_${label//\//_}_$(date +%Y%m%d_%H%M%S).txt"
