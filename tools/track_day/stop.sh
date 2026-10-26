#!/usr/bin/env bash
# bringup.sh 로 띄운 것 정리 — disarm(차량에 auto 0) → 녹화 종료 → 브리지·어댑터 → can_guard → 미러/가짜 차량.
#   bash tools/track_day/stop.sh [세션폴더]      # 기본: ~/a1_race_capture/track_latest
set -u
D=$(readlink -f "${1:-$HOME/a1_race_capture/track_latest}")
[ -f "$D/pids.env" ] || { echo "pids.env 없음: $D"; exit 1; }
WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/ros2_ws"
set +u; source /opt/ros/humble/setup.bash; source "$WS/install/setup.bash"; set -u
say() { echo "[stop] $*" | tee -a "$D/bringup.log"; }
pid() { grep "^$1=" "$D/pids.env" | tail -1 | cut -d= -f2; }
grp() { local p; p=$(pid "$1"); [ -n "$p" ] && kill -"$2" -- -"$p" 2>/dev/null; }

r=$(timeout 10 ros2 service call /control/adapter/disarm std_srvs/srv/Trigger "{}" 2>&1 | grep -o "success=[A-Za-z]*")
say "disarm: ${r:-응답 없음(어댑터가 이미 없음?)}"
sleep 0.5                                       # can_guard 가 auto 0 을 몇 프레임 보내게
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
echo "⚠️ can_guard 가 STOPPED(hold) 였다면 차량은 auto 가 남아 있을 수 있다(0x210 타임아웃 없음) — 0x200 auto 비트를 확인하고,"
echo "   남아 있으면 bringup 을 다시 띄워(disarm 상태 = auto 0 송신) 몇 초 뒤 stop.sh."
