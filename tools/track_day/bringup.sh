#!/usr/bin/env bash
# 트랙 시험 한 번에 띄우기 — CAN 장치 → can_guard → 상태 브리지 + 제어기 어댑터(disarm) → 녹화.
#
#   bash tools/track_day/bringup.sh --kvaser 0        # 실차, Kvaser Leaf v3 CANlib 채널 0 (10-07 에 쓴 장치)
#   bash tools/track_day/bringup.sh --peak can0       # 실차, PEAK (1단계에서 ip link 로 500k·normal 설정을 먼저)
#   bash tools/track_day/bringup.sh --sil             # 이 PC 만으로: vcan0 + 가짜 차량 (리허설·연습)
#   옵션: --params <yaml>  어댑터 표 (기본: 패키지 config/track.yaml — 현장 수정본은 세션 폴더에 복사해 쓰는 걸 권장)
#         --no-rt          can_guard 를 격리 코어·SCHED_FIFO 없이 (sudo 불필요, 지연 값은 '비격리'로 표기할 것)
#         --acc-limit <%>  can_guard 가속 운용 한계 (기본 30 — 어댑터 acc_max_pct 와 같게)
#         --out <폴더>     세션 폴더 (기본 ~/a1_race_capture/track_YYYYmmdd_HHMMSS)
#         --no-adapter     브리지만 — 표 측정(T1~T4)을 lift_cmd 로 직접 할 때(어댑터와 lift_cmd 는 동시에 못 씀)
#
# 끝낼 때: bash tools/track_day/stop.sh   (disarm → 녹화 종료 → 노드 → can_guard → 미러 순서)
# 띄운 뒤 할 일은 TRACK_MANUAL.md. 어댑터는 disarm 으로 시작하므로 이 스크립트만으로는 차량이 움직이지 않는다.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WS="$REPO/ros2_ws"
MODE=""; KV_CH=0; CAN=""; RT=1; ACC_LIMIT=30; OUT=""; ADAPTER=true
PARAMS="$WS/src/a1_control_adapter/config/track.yaml"
GUARD_CPU=8; NODE_CPU=10       # 격리 코어 8-15 (tools/rt). 물리 코어당 한 스레드 원칙으로 8, 10
while [ $# -gt 0 ]; do case "$1" in
  --sil) MODE=sil;; --kvaser) MODE=kvaser; [ "${2:-}" ] && [[ "$2" != --* ]] && { KV_CH=$2; shift; };;
  --peak) MODE=peak; CAN=${2:?--peak can0}; shift;;
  --params) PARAMS=$(readlink -f "$2"); shift;; --no-rt) RT=0;; --no-adapter) ADAPTER=false;; --acc-limit) ACC_LIMIT=$2; shift;;
  --out) OUT=$2; shift;; -h|--help) sed -n 2,15p "$0"; exit 0;;
  *) echo "모르는 옵션 $1"; exit 2;; esac; shift; done
[ -n "$MODE" ] || { sed -n 2,15p "$0"; exit 2; }
D=${OUT:-$HOME/a1_race_capture/track_$(date +%Y%m%d_%H%M%S)}; mkdir -p "$D"
mkdir -p "$HOME/a1_race_capture"; ln -sfn "$D" "$HOME/a1_race_capture/track_latest"
say() { echo "[bringup] $*" | tee -a "$D/bringup.log"; }
die() { say "❌ $*"; say "이미 띄운 것은 bash $HERE/stop.sh 로 정리"; exit 1; }
PIDS="$D/pids.env"; : > "$PIDS"
bg() { local name=$1; shift; setsid "$@" > "$D/$name.log" 2>&1 < /dev/null & echo "${name}=$!" >> "$PIDS"; }

set +u; source /opt/ros/humble/setup.bash; source "$WS/install/setup.bash" || die "ros2_ws 빌드 필요"; set -u
pgrep -f "^(/usr/bin/)?python3 .*safety/can_guard/can_guard.py" >/dev/null && die "can_guard 가 이미 실행 중 — stop.sh 먼저"
pgrep -f "^(/usr/bin/)?python3 .*lift_(cmd|tx)\.py" >/dev/null && die "lift_tx/lift_cmd 실행 중 — 0x210 송신자가 둘이 된다"
cp "$PARAMS" "$D/track_params.yaml"
say "세션 폴더 $D, 모드 $MODE, 어댑터 표 $PARAMS (사본 저장)"

# ── 1. CAN 장치 ─────────────────────────────────────────
case $MODE in
  sil)    CH=vcan0; ip link show vcan0 >/dev/null 2>&1 || die "vcan0 없음 — sudo bash $REPO/sil/vcan/vcan_up.sh"
          bg fake_vehicle python3 "$REPO/sil/vcan/fake_a1_vehicle.py" --channel vcan0
          GUARD_IF=(--channel vcan0); say "가짜 차량 시작(vcan0)";;
  kvaser) CH=kv0; ip link show kv0 >/dev/null 2>&1 || die "kv0 없음 — sudo ip link add dev kv0 type vcan; sudo ip link set kv0 up"
          # can_guard 가 같은 채널로 송신하므로 미러는 listen-only 없이(normal) — VEHICLE_MANUAL C단계와 같음
          bg kvaser_mirror python3 "$REPO/tools/kvaser/kvaser_mirror.py" --channel "$KV_CH" --bitrate 500000 \
             --log "$D/kvaser_ch${KV_CH}.log"
          GUARD_IF=(--interface kvaser --channel "$KV_CH" --bitrate 500000); say "Kvaser 미러 ch$KV_CH → kv0, 원본 기록";;
  peak)   CH=$CAN; ip -details link show "$CAN" 2>/dev/null | grep -q "bitrate 500000" || die "$CAN 비트레이트 500k 아님/없음"
          ip -details link show "$CAN" | grep -q "LISTEN-ONLY" && die "$CAN 이 listen-only — 송신 불가(매뉴얼 1단계로 normal 재설정)"
          GUARD_IF=(--channel "$CAN");;
esac
sleep 1.5
if [ "$MODE" != sil ]; then
  n=$(timeout --foreground 2 candump -n 20 "$CH" 2>/dev/null | grep -cE " (200|201) ")
  [ "$n" -gt 0 ] && say "차량 0x200/0x201 수신 확인($n)" || die "$CH 에서 차량 0x200/0x201 이 안 보임 — 케이블·전원·비트레이트"
fi

# ── 2. can_guard ────────────────────────────────────────
GUARD=(python3 "$REPO/safety/can_guard/can_guard.py" "${GUARD_IF[@]}" --steer-limit-deg 150 --brake-limit-pct 100
       --acc-limit-pct "$ACC_LIMIT" --status-interval-s 1)
if [ $RT = 1 ]; then
  sudo -v || die "sudo 필요(격리 코어·SCHED_FIFO). 없이 하려면 --no-rt"
  bg can_guard sudo chrt -f 90 sudo -u "$USER" env PATH="$PATH" PYTHONPATH="${PYTHONPATH:-}" HOME="$HOME" \
     "${GUARD[@]}" --cpu $GUARD_CPU --rt-priority 90
else
  bg can_guard "${GUARD[@]}"; say "⚠️ --no-rt: can_guard 비격리 실행 — 이 세션의 지연 값은 '비격리'"
fi
for _ in $(seq 60); do [ -e /dev/shm/a1_can_guard_cmd ] && break; sleep 0.1; done
sleep 1.2   # 시작 전 점검(1 s) 대기
grep -q "시작 거부" "$D/can_guard.log" && die "can_guard 시작 거부 — 다른 0x210 송신자(원격조종 0x210? 미확인 송신자?) $(grep '시작 거부' "$D/can_guard.log")"
[ -e /dev/shm/a1_can_guard_cmd ] || die "can_guard 공유메모리 없음 — $D/can_guard.log"
say "can_guard 시작: $(grep -m1 '실행 환경' "$D/can_guard.log")"

# ── 3. 브리지 + 어댑터 ──────────────────────────────────
LAUNCH=(ros2 launch a1_control_adapter track.launch.py channel:="$CH" params:="$D/track_params.yaml" adapter:=$ADAPTER)
if [ $RT = 1 ]; then bg nodes taskset -c $NODE_CPU "${LAUNCH[@]}"; else bg nodes "${LAUNCH[@]}"; fi
if [ $ADAPTER = true ]; then
  for _ in $(seq 40); do ros2 topic list 2>/dev/null | grep -q "^/control/adapter/status$" && break; sleep 0.25; done
  st=$(timeout 5 ros2 topic echo --once /control/adapter/status 2>/dev/null | grep -E "armed|writing|note" | tr '\n' ' ')
  [ -n "$st" ] && say "어댑터: $st" || die "어댑터 상태 토픽 없음 — $D/nodes.log"
else
  for _ in $(seq 40); do ros2 topic list 2>/dev/null | grep -q "^/control/status/control_info$" && break; sleep 0.25; done
  say "브리지만 실행(--no-adapter) — 명령은 lift_cmd 로"
fi

# ── 4. 녹화 ────────────────────────────────────────────
bg record bash "$HERE/record.sh" "$D" "$CH"
say "녹화 시작: $D/bag (+ candump/Kvaser 원본)"
if [ $ADAPTER = false ]; then
  LC_IF=(--channel "$CH"); [ "$MODE" = kvaser ] && LC_IF=(--interface kvaser --channel "$KV_CH" --bitrate 500000)
  cat <<MSG | tee -a "$D/bringup.log"

✅ 준비 완료(브리지만). 표 측정 명령 도구(다른 터미널):
   python3 $REPO/safety/can_guard/lift_cmd.py ${LC_IF[*]} --steer-limit-deg 150 --brake-limit-pct 100 --acc-limit-pct $ACC_LIMIT --log $D/lift_cmd.log
   (명령: auto on / steer <핸들각°> / brake <%> / acc <%> / zero / auto off / q — 끝낼 땐 auto off 먼저)
   메모:  echo "\$(date +%s.%N) \$(date +%H:%M:%S) 내용" >> $D/notes.txt
   종료:  lift_cmd 에서 auto off → q, 그다음 bash $HERE/stop.sh
MSG
  exit 0
fi
cat <<MSG | tee -a "$D/bringup.log"

✅ 준비 완료 — 어댑터는 disarm(차량 무시, shadow). 상태 보기:  ros2 topic echo /control/adapter/status
   arm(축별):  ros2 service call /control/adapter/arm_steer std_srvs/srv/SetBool "{data: true}"
               ros2 service call /control/adapter/arm_long  std_srvs/srv/SetBool "{data: true}"
   즉시 해제:  ros2 service call /control/adapter/disarm std_srvs/srv/Trigger "{}"
   메모:       echo "\$(date +%s.%N) \$(date +%H:%M:%S) 내용" >> $D/notes.txt
   종료:       bash $HERE/stop.sh
MSG
