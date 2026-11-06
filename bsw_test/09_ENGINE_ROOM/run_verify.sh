#!/usr/bin/env bash
# 대회 PC 판정 묶음 — 차 없이 도는 시험을 한 번에 돌리고 VERDICT.md 하나로 정리한다. 약 30~40분, sudo 비밀번호 1회.
#
#   cd ~/git/A1-BSW && bash bsw_test/09_ENGINE_ROOM/run_verify.sh
#
# 옵션: --only pytest,lift,sil,race,cruise,er,bsw   일부만(기본 전부)
#       --no-bsw        bsw_up → 점검 → 입력 시험 → bsw_down 단계를 뺀다
#       --skip-setup    setup_env.sh(패키지·vcan·colcon build)를 건너뛴다
#       --no-rt         격리 코어·SCHED_FIFO 없이(그러면 판정이 아니라 참고값)
#
# **차량·실제 CAN 에 닿지 않는다.** 전 단계 vcan0 + 가짜 차량이다. 그래도 차량 근처에서 돌리지 말고, 다른 BSW 가 떠 있으면 시작을 거부한다.
# ROS_DOMAIN_ID 를 따로 잡아 같은 망의 모델 노드(/control/target)와 섞이지 않게 한다.
# 판정 조건: ~/git/A1-BSW, 격리 코어 있음, RT, 추적 파일 수정 없음. 하나라도 어긋나면 VERDICT.md 에 "참고값" 으로 적는다(CLAUDE.md).
# 합격 숫자는 각 시험에 있는 기준을 그대로 쓴다 — 여기서 새로 정하지 않는다.
#
# 종료 코드: 0 전부 통과, 1 실패 있음, 2 시작 전 점검 실패. 결과: bsw_test/09_ENGINE_ROOM/results/verify_<시각>_<호스트>/
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
ONLY=pytest,lift,sil,race,cruise,er,bsw; SKIP_SETUP=0; RT=auto
while [ $# -gt 0 ]; do case "$1" in
  --only) ONLY=${2:?}; shift;; --no-bsw) ONLY=${ONLY/,bsw/};; --skip-setup) SKIP_SETUP=1;; --no-rt) RT=0;;
  -h|--help) sed -n 2,16p "$0"; exit 0;;
  *) echo "모르는 옵션 $1 (--help)"; exit 2;; esac; shift; done
want() { [[ ",$ONLY," == *",$1,"* ]]; }

STAMP=$(date +%Y%m%d_%H%M%S)
OUT="$HERE/results/verify_${STAMP}_$(hostname -s)"
mkdir -p "$OUT"
V="$OUT/VERDICT.md"
ROWS="$OUT/.rows"; : > "$ROWS"
FAILS=0
export ROS_DOMAIN_ID=${VERIFY_DOMAIN_ID:-77}
PY=${PYTHON:-python3}
say() { echo "[verify] $*" | tee -a "$OUT/verify.log"; }

# ── 시작 전 점검 ───────────────────────────────────────
if pgrep -f "safety/can_guard/can_guard.py" >/dev/null; then
  say "❌ can_guard 가 이미 실행 중 — 끄고 다시(bsw_down). 시험 쪽이 실행 중인 BSW 를 건드리면 안 된다"; exit 2
fi
ISO=$(cat /sys/devices/system/cpu/isolated 2>/dev/null)
[ "$RT" = auto ] && { [ -n "$ISO" ] && RT=1 || RT=0; }
NEED_SUDO=0
[ "$RT" = 1 ] && NEED_SUDO=1
ip link show vcan0 >/dev/null 2>&1 || NEED_SUDO=1
if [ $SKIP_SETUP = 0 ]; then
  NEED_SUDO=1
fi
KEEP=""
if [ $NEED_SUDO = 1 ]; then
  say "sudo 비밀번호 1 회(vcan0·RT·패키지 설치 — 실제 CAN 은 안 건드림)"
  sudo -v || { say "❌ sudo 실패"; exit 2; }
  ( while true; do sudo -n true 2>/dev/null; sleep 50; done ) & KEEP=$!
fi
cleanup() { [ -n "$KEEP" ] && kill "$KEEP" 2>/dev/null; rm -f "$ROWS"; }
trap cleanup EXIT

if [ $SKIP_SETUP = 0 ]; then
  say "환경 준비: setup_env.sh --build-ws"
  bash "$REPO/bsw_test/00_SAF_estop/scripts/setup_env.sh" --build-ws > "$OUT/setup.log" 2>&1 \
    || { say "❌ 환경 준비 실패 — $OUT/setup.log"; exit 2; }
fi
ip link show vcan0 >/dev/null 2>&1 || sudo bash "$REPO/sil/vcan/vcan_up.sh" >> "$OUT/setup.log" 2>&1
ip link show vcan0 >/dev/null 2>&1 || { say "❌ vcan0 없음 — sudo bash $REPO/sil/vcan/vcan_up.sh"; exit 2; }
command -v candump >/dev/null || { say "❌ candump 없음 — sudo apt install can-utils"; exit 2; }
set +u; source /opt/ros/humble/setup.bash || { say "❌ ROS2 Humble 없음"; exit 2; }
if [ $SKIP_SETUP = 0 ]; then (cd "$REPO/ros2_ws" && colcon build --symlink-install >> "$OUT/setup.log" 2>&1) || { say "❌ colcon build 실패"; exit 2; }; fi
source "$REPO/ros2_ws/install/setup.bash" || { say "❌ ros2_ws 빌드 필요"; exit 2; }; set -u

# ── 판정 조건 ───────────────────────────────────────────
COMMIT=$(git -C "$REPO" rev-parse --short HEAD)
BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)
DIRTY=0; git -C "$REPO" diff --quiet HEAD -- . || DIRTY=1
WHY=()
[ "$REPO" = "$HOME/git/A1-BSW" ] || WHY+=("저장소가 ~/git/A1-BSW 가 아님($REPO)")
[ -n "$ISO" ] || WHY+=("격리 코어 없음")
[ "$RT" = 1 ] || WHY+=("RT 없음")
[ $DIRTY = 0 ] || WHY+=("추적 파일 수정 있음")
want bsw || WHY+=("bsw 단계 생략")

# ── 단계 ────────────────────────────────────────────────
stage() {   # stage <키> <제목> <제한 s> <명령...>
  local key=$1 title=$2 lim=$3; shift 3
  want "$key" || return 0
  local log="$OUT/$key.log" t0=$SECONDS rc
  say "▶ $title (제한 $((lim / 60)) 분)"
  timeout "$lim" bash -c '"$@"' _ "$@" > "$log" 2>&1 < /dev/null; rc=$?     # bash -c: 단계가 셸 함수일 수 있다(timeout 은 함수를 못 부른다)
  local dt=$((SECONDS - t0))
  if [ $rc = 0 ]; then say "  ✅ 통과 ${dt} s"; echo "| $title | ✅ 통과 | ${dt} s | \`$key.log\` |" >> "$ROWS"
  else FAILS=$((FAILS + 1)); say "  ❌ 실패 rc=$rc ${dt} s — $log"; echo "| $title | ❌ 실패(rc $rc) | ${dt} s | \`$key.log\` |" >> "$ROWS"; fi
}

run_pytest() {
  cd "$REPO" && env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest -q -p no:cacheprovider safety/can_guard/test \
    tools/race_day/test_lift_tx.py sil/vcan/test_fake_race_server.py tools/bsw/test_bsw_monitor_race.py \
    ros2_ws/src/a1_control_adapter/test/test_conversion.py tools/engine_room/test_fit_tools.py bsw_test/common/scripts
}
run_sil() {
  if [ "$RT" = 1 ]; then bash "$REPO/bsw_test/00_SAF_estop/scripts/run_sil_check.sh" --rt auto --track
  else bash "$REPO/bsw_test/00_SAF_estop/scripts/run_sil_check.sh" --track; fi
}
run_race() {
  cd "$REPO" || return 1
  python3 safety/can_guard/sil_tests/r_race.py --interface socketcan --channel vcan0 --latency 300 --out "$OUT/race" || return 1
  python3 safety/can_guard/sil_tests/r_race.py --interface socketcan --channel vcan0 --no-scenarios --latency 500 --load --out "$OUT/race_load"
}
run_er() {
  python3 "$REPO/bsw_test/09_ENGINE_ROOM/scripts/sil_engine_room.py" --out "$OUT/engine_room"; local rc=$?
  find "$OUT/engine_room" \( -name candump.log -o -name gnss.csv \) -exec zstd -q -f --rm {} \; 2>/dev/null   # 커밋 용량
  return $rc
}
run_bsw() {
  local rc=0 args=(--sil --name VERIFY --no-ui --yes)
  [ "$RT" = 1 ] || args+=(--no-rt)
  bash "$REPO/tools/bsw/bsw_up.sh" "${args[@]}" || rc=1
  if [ $rc = 0 ]; then
    python3 "$REPO/tools/bsw/race_up_check.py" "$REPO" || rc=1
    python3 "$HERE/scripts/e2e_bsw_up.py" --session "$(readlink -f "$HOME/a1_race_capture/bsw_latest")" || rc=1
    bash "$REPO/tools/bsw/bsw_down.sh" || rc=1
  else
    bash "$REPO/tools/bsw/bsw_down.sh" --force
  fi
  pgrep -f "safety/can_guard/can_guard.py" >/dev/null && { echo "❌ can_guard 가 남아 있음"; rc=1; }
  return $rc
}

export -f run_pytest run_sil run_race run_er run_bsw
export OUT HERE REPO RT PY
say "결과 폴더 $OUT, 커밋 $COMMIT($BRANCH), RT=$RT, 시험: $ONLY"
stage pytest "단위 시험" 300 run_pytest
stage lift   "rehearse_lift_guard(can_guard·lift_cmd 31 항목)" 300 bash "$REPO/tools/race_day/rehearse_lift_guard.sh"
stage sil    "비상 정지 E1~E7·P1·P2·세션 SIL·트랙 리허설" 1200 run_sil
stage race   "경기 신호 /race/state 10 시나리오·지연(기준 중앙 ≤ 5 ms)" 420 run_race
stage cruise "가속 히스테리시스(기준 ≤ 5 회/분, 입력 고정)" 300 python3 "$REPO/bsw_test/common/scripts/adapter_cruise_check.py" --out "$OUT/cruise"
stage er     "엔진룸 표·표 갱신·원 돌기 SIL" 1500 run_er
stage bsw    "BSW 시작 → 시작 점검 → 입력 시험 → 끄기" 300 run_bsw

# ── VERDICT ────────────────────────────────────────────
pick() { grep -hE "$1" "$2" 2>/dev/null | sort -u | cut -c1-150 | sed 's/^/    /'; }
{
  if [ ${#WHY[@]} = 0 ]; then KIND="**판정**"; else KIND="**참고값**($(IFS=,; echo "${WHY[*]}"))"; fi
  if [ $FAILS = 0 ]; then echo "# 대회 PC 시험 결과: ✅ 전부 통과 — $KIND"; else echo "# 대회 PC 시험 결과: ❌ 실패 $FAILS 건 — $KIND"; fi
  echo
  echo "| 항목 | 값 |"; echo "|---|---|"
  echo "| 시각 | $(date '+%F %T %z') |"
  echo "| 호스트·저장소 | $(hostname) · \`$REPO\` |"
  echo "| 커밋 | \`$COMMIT\` ($BRANCH)$([ $DIRTY = 1 ] && echo ' + 수정 있음') |"
  echo "| 커널·격리 코어 | $(uname -r) · ${ISO:-없음} |"
  echo "| RT | $RT |"
  echo
  echo "| 시험 | 결과 | 시간 | 로그 |"; echo "|---|---|---|---|"
  cat "$ROWS"
  echo; echo "## 주요 숫자"; echo '```'
  pick '^\[E[1-7]\]' "$OUT/sil.log"
  pick '결과: PASS' "$OUT/lift.log"
  pick '지연 (idle|load)' "$OUT/race.log"
  pick '^(PASS|참고).*(hysteresis|old)' "$OUT/cruise.log"
  pick '^ *(PASS|FAIL)' "$OUT/er.log"
  echo '```'
  echo; echo "실패가 있으면 해당 로그부터 본다. 11-14 전 할 일: \`bsw_test/09_ENGINE_ROOM/docs/checklist.md\`. 차량에는 아무것도 하지 않았다."
} > "$V"
cat "$V"
echo
echo "결과 기록: git -C $REPO add $OUT/VERDICT.md $OUT/*.log && git -C $REPO commit -m '대회 PC 판정 $COMMIT'   # 나머지 CSV·로그는 폴더에만"
[ $FAILS = 0 ]
