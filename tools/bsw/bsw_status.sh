#!/usr/bin/env bash
# BSW 상태 한 화면 — 프로세스, can_guard 상태·차속, 어댑터 arm, CAN 로그 크기, 비상 정지 이벤트.
#   bash tools/bsw/bsw_status.sh [세션폴더]        # 기본 ~/a1_race_capture/bsw_latest
#   watch -n 1 bash tools/bsw/bsw_status.sh         # 계속 보기
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
source "$HERE/bsw.env"
D=$(readlink -f "${1:-$BSW_CAPTURE/bsw_latest}" 2>/dev/null)
[ -d "${D:-}" ] || { echo "세션 폴더 없음 — bsw_up.sh 먼저"; exit 1; }
ok() { echo "  ✅ $*"; }; bad() { echo "  ❌ $*"; }; warn() { echo "  ⚠️ $*"; }

echo "── BSW 상태  $(date +%T)  $D"
gpid=$(pgrep -n -u "$USER" -f "^(/usr/bin/)?python3 .*safety/can_guard/can_guard.py")
if [ -n "$gpid" ]; then
  pol=$(chrt -p "$gpid" 2>/dev/null | grep -o "SCHED_[A-Z]*" | head -1)
  ok "can_guard pid $gpid ($pol, CPU $(taskset -cp "$gpid" 2>/dev/null | awk '{print $NF}'))"
  line=$(grep "^\[can_guard\] [A-Z]" "$D/can_guard.log" 2>/dev/null | grep "차속" | tail -1)
  echo "     ${line#\[can_guard\] }"
else
  bad "can_guard 없음"
fi
pgrep -f "a1_cmd_adapter|cmd_adapter" >/dev/null && ok "어댑터 실행 중" || bad "어댑터 없음"
pgrep -f "can_raw_bridge" >/dev/null && ok "상태 브리지 실행 중" || bad "상태 브리지 없음"
pgrep -u "$USER" -f "tools/bsw/bsw_monitor.py" >/dev/null && ok "상태판 창 실행 중" || echo "     상태판 창 꺼짐(주행과 무관)"
set +u; source /opt/ros/humble/setup.bash 2>/dev/null; source "$REPO/ros2_ws/install/setup.bash" 2>/dev/null; set -u
if command -v ros2 >/dev/null 2>&1; then
  st=$(timeout 3 ros2 topic echo --once /control/adapter/status 2>/dev/null | grep -E "^(steer_armed|long_armed|writing|input_age_s|note)" | tr '\n' ' ')
  [ -n "$st" ] && echo "     어댑터: $st" || warn "어댑터 상태 토픽 응답 없음"
  python3 "$HERE/race_up_check.py" "$REPO" --timeout 2 | sed 's/^/   /'
fi
for f in "$D"/kvaser_ch*.log "$D"/candump_*.log; do
  [ -f "$f" ] || continue
  s1=$(stat -c %s "$f"); sleep 0.5; s2=$(stat -c %s "$f")
  [ "$s2" -gt "$s1" ] && ok "CAN 로그 $(basename "$f") $(numfmt --to=iec "$s2") (늘어나는 중)" || bad "CAN 로그 $(basename "$f") 멈춤"
done
ev="$D/can_guard_events.csv"
if [ -f "$ev" ]; then
  n_es=$(grep -c ",estop_request," "$ev"); n_fx=$(grep -c ",foreign_0x210," "$ev")
  [ "$n_es" -gt 0 ] && warn "비상 정지 $n_es 회 — 마지막: $(grep ',estop_request,' "$ev" | tail -1 | cut -d, -f4-)"
  [ "$n_fx" -gt 0 ] && bad "다른 0x210 송신자 감지 $n_fx 줄 — 원격 유닛 끌 것"
  grep ",state," "$ev" | tail -1 | awk -F, '{print "     마지막 상태 전이: " $4}'
fi
