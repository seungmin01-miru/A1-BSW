#!/usr/bin/env bash
# 네트워크 계열 사건을 일부러 일으켜 격리 코어 스파이크와의 인과를 보는 양성 대조 (30분 soak 와 병행).
#   sudo bash tools/rt/provoke_net.sh [분=30] [간격초=120]
# 간격마다: WiFi 라디오 off→on(재연결 유도) → PackageKit 저장소 갱신(pkcon refresh force).
# 각 동작의 시각을 logs/provoke_<ts>.log 에 남긴다 → soak 의 overflow 사이클(시작 시각 + N ms)과 대조.
set -uo pipefail
BASE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mins=${1:-30}; gap=${2:-120}
[[ $EUID -eq 0 ]] || { echo "sudo 로 실행"; exit 1; }
mkdir -p "$BASE/logs"; log="$BASE/logs/provoke_$(date +%Y%m%d_%H%M%S).log"
end=$(( $(date +%s) + mins*60 ))
echo "$(date '+%F %T') 시작 — ${mins}분, ${gap}초 간격 (로그 $log)" | tee "$log"
n=0
while (( $(date +%s) < end )); do
  sleep "$gap"
  n=$((n+1))
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n wifi off" >> "$log"; nmcli radio wifi off; sleep 8
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n wifi on"  >> "$log"; nmcli radio wifi on;  sleep 25
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n pkcon refresh" >> "$log"
  timeout 60 pkcon refresh force >/dev/null 2>&1; echo "$(date '+%F %T.%N' | cut -c1-23) #$n pkcon done rc=$?" >> "$log"
done
echo "$(date '+%F %T') 끝 — 사건 $n 회" | tee -a "$log"
