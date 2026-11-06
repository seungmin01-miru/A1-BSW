#!/usr/bin/env bash
# 00 SAF-ESTOP — 대회 차량 PC 에서 can_guard 비상 정지 SIL 확인 한 번에 (차량 버스는 안 건드림, vcan0 만 사용).
#
#   bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh            # 비격리(기본)
#   bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt 8     # can_guard 를 코어 8·SCHED_FIFO 90 으로(sudo, 실제 배포 형태)
#   bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto  # 격리 코어(/sys/.../isolated) 첫 번째
#   환경 준비는 setup_env.sh 먼저.
#   bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --track    # ros2_ws 빌드돼 있으면 rehearse_track.sh 도
#
# 결과: bsw_test/00_SAF_estop/results/sil_<날짜시각>_<호스트>/ — summary.md(환경·PASS/FAIL·지연) + 각 로그.
# 이 PC(개발 PC) 결과는 로직 확인용이고, **00 판정에는 대회 PC 결과를 쓴다**(CLAUDE.md 5절).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
CG="$REPO/safety/can_guard"
RT_CPU=""; TRACK=0; TRIALS=20
while [ $# -gt 0 ]; do
  case "$1" in
    --rt) RT_CPU=${2:?코어 번호}; shift 2;;
    --track) TRACK=1; shift;;
    --trials) TRIALS=${2:?}; shift 2;;
    *) echo "알 수 없는 인자: $1"; exit 2;;
  esac
done
PY=${PYTHON:-python3}
OUT="$REPO/bsw_test/00_SAF_estop/results/sil_$(date +%Y%m%d_%H%M%S)_$(hostname -s)"
mkdir -p "$OUT"
S="$OUT/summary.md"
FAILS=0
line() { echo "$*" | tee -a "$S"; }
run() {  # run <이름> <명령...> — 종료 코드로 PASS/FAIL
  local name=$1; shift
  echo "▶ $name"
  if (cd "$CG" && env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$@") > "$OUT/$name.log" 2>&1; then
    line "| $name | ✅ PASS | \`$name.log\` |"
  else
    line "| $name | ❌ FAIL | \`$name.log\` |"; FAILS=$((FAILS+1))
  fi
}

# ── 0. 준비 확인 ─────────────────────────────────────────
ip -br link show vcan0 2>/dev/null | grep -q UP || { echo "vcan0 없음/DOWN → bash $HERE/setup_env.sh"; exit 2; }
env -u PYTHONPATH $PY -c "import can, cantools, pytest, yaml" 2>/dev/null \
  || { echo "$PY 에 python-can/cantools/pytest/PyYAML 없음 → bash $HERE/setup_env.sh (또는 PYTHON=<venv python>)"; exit 2; }
pgrep -f "safety/can_guard/can_guard.py" >/dev/null && { echo "can_guard 가 이미 실행 중(실차 세션?) — stop.sh 후 다시"; exit 2; }

GUARD_ARGS=""
if [ "$RT_CPU" = auto ]; then
  RT_CPU=$(cut -d, -f1 /sys/devices/system/cpu/isolated 2>/dev/null | cut -d- -f1)
  [ -n "$RT_CPU" ] || { echo "격리 코어 없음 — --rt <코어> 로 직접 지정하거나 --rt 빼고 실행"; exit 2; }
fi
if [ -n "$RT_CPU" ]; then
  sudo -v || { echo "--rt 는 sudo 필요"; exit 2; }
  GUARD_ARGS="--cpu $RT_CPU --rt-priority 90"
fi

{
  echo "# 00 SAF-ESTOP SIL 확인 — $(date '+%F %T')"
  echo
  echo "| 항목 | 값 |"; echo "|---|---|"
  echo "| 호스트 | $(hostname) |"
  echo "| 커밋 | $(git -C "$REPO" rev-parse --short HEAD)$(git -C "$REPO" diff --quiet -- safety || echo ' (+수정 있음)') |"
  echo "| 커널 | $(uname -r) $(uname -v | grep -o 'PREEMPT[_A-Z]*' | head -1) |"
  echo "| isolcpus | $(cat /sys/devices/system/cpu/isolated 2>/dev/null || echo '?') |"
  echo "| python / python-can | $(env -u PYTHONPATH $PY -c 'import sys,can;print(sys.version.split()[0], can.__version__)') |"
  echo "| can_guard 실행 형태 | ${GUARD_ARGS:-비격리} |"
  echo "| 반복 | E1~E3·E5 각 $TRIALS 회 |"
  echo
  echo "| 시험 | 결과 | 로그 |"; echo "|---|---|---|"
} > "$S"

# ── 1. 단위 시험 ─────────────────────────────────────────
run unit_pytest $PY -m pytest test/ -q
# ── 2. 비상 정지 시나리오 E1~E7 ───────────────────────────
run sil_e_estop $PY sil_tests/e_estop.py --channel vcan0 --trials "$TRIALS" --guard-args "$GUARD_ARGS"
# ── 3. 기존 회귀 P-1 / P-2 ───────────────────────────────
run sil_p1 $PY sil_tests/p1_kill_control_node.py --channel vcan0
run sil_p2 $PY sil_tests/p2_kill_perception.py --channel vcan0
# ── 3b. 02 프로파일 생성기 (단위 + vcan 2회 재현성·0x210 일치) ──
run unit_profile_lib $PY -m pytest "$REPO/bsw_test/common/scripts" -q
run sil_02_profile $PY "$REPO/bsw_test/02_LON_profile/scripts/sil_profile.py" --channel vcan0 --out "$OUT/sil_02_profile"
# ── 3c. 02 세션(한 세션 여러 회차 + 속도 제어 + 50 m 코스, 가짜 차량 --physics) ──
run sil_02_session $PY "$REPO/bsw_test/02_LON_profile/scripts/sil_session.py" --channel vcan0 --out "$OUT/sil_02_session"
# ── 4. (선택) 트랙 리허설 — 어댑터 disarm 침묵 포함 ─────────
if [ $TRACK = 1 ]; then
  if [ -f "$REPO/ros2_ws/install/setup.bash" ]; then
    echo "▶ rehearse_track"
    if bash "$REPO/tools/track_day/rehearse_track.sh" > "$OUT/rehearse_track.log" 2>&1; then
      line "| rehearse_track | ✅ PASS | \`rehearse_track.log\` |"
    else
      line "| rehearse_track | ❌ FAIL | \`rehearse_track.log\` |"; FAILS=$((FAILS+1))
    fi
  else
    line "| rehearse_track | ⏭ 생략 | ros2_ws 미빌드 |"
  fi
fi

{
  echo
  echo "## 비상 정지 시나리오 상세"
  echo '```'
  grep -E '^\[E[0-9]\]|^전체' "$OUT/sil_e_estop.log"
  echo '```'
  echo
  echo "## 02 세션 SIL 상세"
  echo '```'
  grep -E '^## |^\| [^확-]|^전체' "$OUT/sil_02_session.log" 2>/dev/null
  echo '```'
  echo
  echo "## 판정 메모"
  echo "- E1~E3 기준 ≤ 40 ms, E5 ≤ 100 ms. 실차 제동 시작(≤ 200 ms) = 여기 값 + 차량 수락(≤ 22 ms) + 브레이크 무반응(56~112 ms)."
  echo "- 결과: $([ $FAILS -eq 0 ] && echo 'PASS' || echo "FAIL $FAILS 건")"
} >> "$S"
echo; cat "$S"
[ $FAILS -eq 0 ]
