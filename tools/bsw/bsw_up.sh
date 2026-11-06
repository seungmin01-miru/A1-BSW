#!/usr/bin/env bash
# BSW 한 번에 켜기(경주·자율 주행용) — CAN 장치 → can_guard(RT) → 상태 브리지 + 어댑터(자동 arm) → CAN 로그 녹화.
# 끝나면 "모델이 /control/target 을 내면 바로 움직이는" 상태다. ROS bag 은 녹화하지 않는다(다른 팀 몫).
#
#   bash tools/bsw/bsw_up.sh [--name RUN] [옵션]       # 기본: 대회 차량 PC, Kvaser 0, 앱 필수, 자동 arm, RT
#   bash tools/bsw/bsw_down.sh                          # 끝: 정지 확인 → ESTOP 브레이크 → 정리 → CAN 로그 압축·sha256
#   bash tools/bsw/bsw_status.sh                        # 지금 상태 한 화면
#
# 옵션: --name RUN        세션 이름(파일 이름에 들어감, 예 LAP1). 기본 RUN
#       --sil             차량 없이 vcan0 + 가짜 차량(리허설)
#       --peak [can0]     PEAK 카드
#       --manual-arm      자동 arm 끔(arm 서비스로 직접)
#       --no-app          앱(0x156/0x157) 없어도 시작(앱 = 비상 정지 수단 — 물리 E-stop 있을 때만)
#       --no-rt           격리 코어·SCHED_FIFO 없이(지연 값 '비격리')
#       --no-ui           상태판 창을 띄우지 않음(대회 본 주행 등 볼 사람이 없을 때)
#       --acc-limit P     can_guard 가속 한계 % (기본 bsw.env)
#       --params YAML     어댑터 표(기본 패키지 track.yaml)
#       --yes             확인 질문 생략
# 내부적으로 tools/track_day/bringup.sh 를 부른다(같은 코드 — 현장 시험과 경주가 같은 경로를 쓴다).
# 상태판(bsw_monitor.py)은 읽기 전용(kv0 사본, Kvaser 채널 안 엶)·nice 10·격리 코어 밖이라 can_guard 와 경합하지 않는다.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=bsw.env
source "$HERE/bsw.env"
NAME=RUN; YES=0; PARAMS=(); MODE=$BSW_CAN; PEAK=$BSW_PEAK_IF
while [ $# -gt 0 ]; do case "$1" in
  --name) NAME=${2:?}; shift;; --sil) MODE=sil;; --peak) MODE=peak; [ "${2:-}" ] && [[ "$2" != --* ]] && { PEAK=$2; shift; };;
  --manual-arm) BSW_AUTO_ARM=0;; --no-app) BSW_APP_REQUIRED=0;; --no-rt) BSW_RT=0;;
  --acc-limit) BSW_ACC_LIMIT=${2:?}; shift;; --params) PARAMS=(--params "${2:?}"); shift;;
  --no-ui) BSW_UI=0;;
  --yes|-y) YES=1;; -h|--help) sed -n 2,21p "$0"; exit 0;;
  *) echo "모르는 옵션 $1 (--help)"; exit 2;; esac; shift; done
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "--name 은 영문·숫자·._- 만"; exit 2; }

D="$BSW_CAPTURE/$(date +%Y%m%d_%H%M%S)_BSW-${NAME}"
ask() { [ $YES = 1 ] && return 0; read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[yY] ]]; }

# ── 0. 사전 점검 ────────────────────────────────────────
pgrep -f "^(/usr/bin/)?python3 .*safety/can_guard/can_guard.py" >/dev/null && {
  echo "❌ can_guard 가 이미 실행 중 — bash $HERE/bsw_down.sh 먼저"; exit 1; }
set +u; source /opt/ros/humble/setup.bash; source "$REPO/ros2_ws/install/setup.bash" 2>/dev/null \
  || { echo "❌ ros2_ws 빌드 필요: cd $REPO/ros2_ws && colcon build"; exit 1; }; set -u
if [ "$BSW_AUTO_ARM" = 1 ]; then
  n=$(timeout 5 ros2 topic info /control/target 2>/dev/null | awk '/Publisher count/{print $3}')
  if [ "${n:-0}" -gt 0 ]; then
    echo "⚠️ 모델이 이미 /control/target 을 발행 중(publisher $n) — 자동 arm 이면 BSW 가 뜨자마자 차가 움직인다."
    ask "모델 명령이 '정지'인지 확인했나? 계속" || { echo "중단 — 모델을 멈추거나 --manual-arm"; exit 1; }
  fi
fi
[ "$BSW_APP_REQUIRED" = 1 ] || echo "⚠️ --no-app: 앱 비상 정지 없이 시작 — 물리 E-stop 담당 확인"
echo "원격 유닛(0x210 직접 송신)은 꺼 두었는가? 켜져 있으면 can_guard 가 시작을 거부한다(종료 3)."

# ── 1. bringup ─────────────────────────────────────────
ARGS=(--out "$D" --acc-limit "$BSW_ACC_LIMIT" --no-bag "${PARAMS[@]}")
case $MODE in kvaser) ARGS+=(--kvaser "$BSW_KV_CH");; peak) ARGS+=(--peak "$PEAK");; sil) ARGS+=(--sil);; esac
[ "$BSW_AUTO_ARM" = 1 ] && ARGS+=(--auto-arm)
[ "$BSW_APP_REQUIRED" = 1 ] && [ "$MODE" != sil ] && ARGS+=(--estop-required)
[ "$BSW_RT" = 1 ] || ARGS+=(--no-rt)
ln -sfn "$D" "$BSW_CAPTURE/bsw_latest"     # bringup 전에: 실패해도 bsw_down --force 로 정리할 수 있게(2026-11-07)
bash "$REPO/tools/track_day/bringup.sh" "${ARGS[@]}" || { echo "❌ bringup 실패 — $D/bringup.log, 정리: bash $HERE/bsw_down.sh --force"; exit 1; }

# ── 2. 세션 기록(results.md 에 필요한 값 — CLAUDE.md 7절) ──
{
  echo "# BSW 세션 $NAME"
  echo "- 시작: $(date '+%F %T %z')  호스트: $(hostname)"
  echo "- 커밋: $(git -C "$REPO" rev-parse --short HEAD)$(git -C "$REPO" diff --quiet || echo ' (+미커밋 변경)')"
  echo "- CAN: $MODE $( [ "$MODE" = kvaser ] && echo "ch$BSW_KV_CH")  RT: $BSW_RT  자동 arm: $BSW_AUTO_ARM  앱 필수: $BSW_APP_REQUIRED  상태판: $BSW_UI"
  echo "- can_guard 한계: --steer-limit-deg 150 --brake-limit-pct 100 --acc-limit-pct $BSW_ACC_LIMIT"
  echo "- 어댑터 표: track_params.yaml(사본)"
  python3 "$HERE/race_up_check.py" --map-only "$REPO" 2>/dev/null | sed 's/^/- /'
} > "$D/SESSION.md"

bash "$HERE/bsw_status.sh" "$D"
# 경기 신호 시작 점검(WP7): /race/state 발행, 0x202 현재값(송신 없음), long_map 버전·source. 못 받으면 모델이 깃발을 못 본다
python3 "$HERE/race_up_check.py" "$REPO" || echo "❌ 경기 신호 점검 실패 — 위 내용을 확인(모델 팀 M-01·M-06 이 동작하지 않는다)"

# ── 3. 상태판 창(읽기 전용) ──────────────────────────────
case $MODE in kvaser) MCH=kv0;; peak) MCH=$PEAK;; sil) MCH=vcan0;; esac
UI_MSG="상태판 끔(--no-ui) — 필요하면: python3 $HERE/bsw_monitor.py --channel $MCH"
if [ "$BSW_UI" = 1 ]; then
  if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    setsid -f nice -n 10 python3 "$HERE/bsw_monitor.py" --channel "$MCH" --session "$D" \
      > "$D/monitor.log" 2>&1 < /dev/null
    UI_MSG="상태판 창 띄움(닫아도 주행에 영향 없음, 다시: python3 $HERE/bsw_monitor.py --channel $MCH)"
  else
    UI_MSG="화면(DISPLAY) 없음 — 상태판 생략. ssh 면: python3 $HERE/bsw_monitor.py --text --channel $MCH"
  fi
fi

cat <<MSG

▶ 모델 팀: /control/target 발행 시작 = 출발(자동 arm 1회). 서 있을 때도 정지 목표(예 accel −3)를 계속 낼 것 —
  150 ms 끊기면 고장 래치 → 브레이크(HOLDING) → 2 s 뒤 STOPPED, 다시 가려면 bsw_down → bsw_up.
▶ 비상 정지: 앱(원격 인수) / python3 $REPO/safety/can_guard/estop.py / 물리 E-stop
▶ 상태판: $UI_MSG
▶ 끝: 차가 선 뒤 bash $HERE/bsw_down.sh   (주행 중 disarm 금지 — 0x210 이 끊기고 브레이크를 걸지 않는다)
MSG
