#!/usr/bin/env bash
# 체크리스트 1-3: GPU 활동이 격리 코어(8-15)에 CPU 간 인터럽트(CAL/TLB)를 유입시키는지 직접 관찰. sudo 불필요.
#   bash tools/rt/ipi_check.sh [초, 기본 20]
# 유휴 → GPU 부하(glmark2 refract, 4K) → 유휴 순으로 격리 코어의 CAL/TLB 증가량(회/초)을 비교한다.
set -euo pipefail
SEC=${1:-20}
ISO=$(tr -d '[:space:]' < /sys/devices/system/cpu/isolated)
[[ -n "$ISO" ]] || { echo "격리 코어가 없는 부팅입니다 (튜닝 항목으로 부팅해야 의미 있음)"; exit 1; }
count() {  # $1 = CAL: 또는 TLB:  → 격리 코어 열 합계
  awk -v key="$1" -v iso="$ISO" 'NR==1{for(i=1;i<=NF;i++){c=$i; sub(/CPU/,"",c); col[i+1]=c}; n=NF
        split(iso,r,"-"); lo=r[1]; hi=(r[2]==""?r[1]:r[2]); next}
      $1==key{s=0; for(i=2;i<=n+1;i++) if(col[i]+0>=lo && col[i]+0<=hi) s+=$i; print s}' /proc/interrupts
}
phase() {  # $1 라벨
  local c0 t0 c1 t1; c0=$(count CAL:); t0=$(count TLB:); sleep "$SEC"; c1=$(count CAL:); t1=$(count TLB:)
  printf "  %-24s CAL %6d회 (%3d/초)   TLB %5d회 (%3d/초)\n" "$1" $((c1-c0)) $(((c1-c0)/SEC)) $((t1-t0)) $(((t1-t0)/SEC))
}
echo "격리 코어 $ISO, 구간당 ${SEC}초 — $(uname -r)"
phase "① 유휴 (데스크톱만)"
if command -v glmark2 >/dev/null && [[ -n "${DISPLAY:-}" ]]; then
  glmark2 --off-screen --run-forever -s 3840x2160 -b refract >/dev/null 2>&1 & GL=$!
  sleep 2; phase "② GPU 부하 (glmark2 4K)"; kill $GL 2>/dev/null; wait $GL 2>/dev/null || true
  sleep 2; phase "③ 다시 유휴"
else
  echo "  glmark2 또는 DISPLAY 없음 → GPU 부하 구간 생략"
fi
echo "판정: ②에서만 CAL 이 오르면 'GPU 활동 → 격리 코어 IPI 유입' 확인 (1-3). 기준선 ≈ 20/초 (4K 83 %)"
