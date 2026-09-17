#!/usr/bin/env bash
# 네트워크 없이 "패키지 갱신 뒤 후처리"와 비슷한 무거운 사용자 공간 작업만 반복 — USB WiFi 와 사용자 공간 작업을 가르는 대조.
#   sudo bash tools/rt/provoke_local.sh [분=3] [간격초=60]
# 간격마다: apt 캐시 재생성(CPU+디스크) → 2 GB 파일 쓰기+fsync → 페이지 캐시 드롭. 시각을 logs/provoke_<ts>.log 에 남김(episodes.py 호환).
set -uo pipefail
BASE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mins=${1:-3}; gap=${2:-60}
[[ $EUID -eq 0 ]] || { echo "sudo 로 실행"; exit 1; }
mkdir -p "$BASE/logs"; log="$BASE/logs/provoke_$(date +%Y%m%d_%H%M%S).log"
end=$(( $(date +%s) + mins*60 )); n=0
echo "$(date '+%F %T') 시작(local) — ${mins}분, ${gap}초 간격 (로그 $log)" | tee "$log"
while (( $(date +%s) < end )); do
  sleep "$gap"; n=$((n+1))
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n apt-cache gencaches" >> "$log"; apt-cache gencaches >/dev/null 2>&1
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n dd 2GB fsync" >> "$log"; dd if=/dev/zero of=/var/tmp/a1_provoke.bin bs=1M count=2000 conv=fsync status=none 2>/dev/null
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n drop_caches" >> "$log"; sync; echo 3 > /proc/sys/vm/drop_caches; rm -f /var/tmp/a1_provoke.bin
  echo "$(date '+%F %T.%N' | cut -c1-23) #$n done" >> "$log"
done
echo "$(date '+%F %T') 끝 — $n 회" | tee -a "$log"
