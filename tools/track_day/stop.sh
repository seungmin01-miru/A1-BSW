#!/usr/bin/env bash
# bringup.sh 로 띄운 것 정리 — disarm(어댑터가 auto 000 을 씀 → can_guard 는 0x210 을 보내지 않음: auto 000 이 버스로 나가지는 않는다) → 녹화 종료 → 브리지·어댑터 → can_guard → 미러/가짜 차량.
#   bash tools/track_day/stop.sh [세션폴더] [--no-disarm]   # 기본: ~/a1_race_capture/track_latest
#   --no-disarm: disarm 을 부르지 않는다(tools/bsw/bsw_down.sh 가 먼저 ESTOP 으로 브레이크를 잡은 뒤 씀 —
#                disarm 은 어댑터가 auto 0 을 쓰게 해서 can_guard 가 송신을 멈춘다)
set -u
NO_DISARM=0; ARGS=()
for x in "$@"; do [ "$x" = --no-disarm ] && NO_DISARM=1 || ARGS+=("$x"); done
D=$(readlink -f "${ARGS[0]:-$HOME/a1_race_capture/track_latest}")
[ -f "$D/pids.env" ] || { echo "pids.env 없음: $D"; exit 1; }
WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/ros2_ws"
set +u; source /opt/ros/humble/setup.bash; source "$WS/install/setup.bash"; set -u
say() { echo "[stop] $*" | tee -a "$D/bringup.log"; }
pid() { grep "^$1=" "$D/pids.env" | tail -1 | cut -d= -f2; }
grp() { local p; p=$(pid "$1"); [ -n "$p" ] && kill -"$2" -- -"$p" 2>/dev/null; }

if [ $NO_DISARM = 0 ]; then
  r=$(timeout 10 ros2 service call /control/adapter/disarm std_srvs/srv/Trigger "{}" 2>&1 | grep -o "success=[A-Za-z]*")
  say "disarm: ${r:-응답 없음(어댑터가 이미 없음?)}"
  sleep 0.5
else
  say "disarm 생략(--no-disarm) — ESTOP 브레이크 유지 상태로 정리"
fi
grp record INT; sleep 1.5; grp record TERM
say "녹화 종료"
grp nodes INT; sleep 2; grp nodes TERM
# can_guard 는 sudo 로 띄웠을 수 있다 — 실제 파이썬 프로세스(이 계정)를 직접 SIGTERM(정상 종료 경로, shm 정리)
pkill -TERM -u "$USER" -f "^(/usr/bin/)?python3 .*safety/can_guard/can_guard.py" && say "can_guard 종료"
sleep 1
grp kvaser_mirror INT; grp fake_vehicle TERM
sleep 0.5
say "정리 끝. 세션 폴더: $D"
ls -la "$D" | sed 's/^/  /'
echo "⚠️ 0x210 이 끊긴 뒤 차량 동작은 미확정(TODO-2) — 0x200 auto 비트·브레이크 위치를 확인하고 바퀴 굄 유지."
