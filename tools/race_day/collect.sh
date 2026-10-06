#!/usr/bin/env bash
# 대회장 데이터 수집 패키지 — 실행 한 줄로 원시 CAN·ROS2 토픽·can_guard 흔적·GPS/INS 시리얼·시스템 상태를
# 전부 모아 하나의 tar.gz 로 묶는다. can_guard/ROS2 브리지/CAN 인터페이스 기동은 이 스크립트의 일이 아니다
# (팀이 따로 함) — 이 스크립트는 떠 있는 걸 최대한 붙잡아 기록하고, 안 떠 있어도 죽지 않는다(모듈별 best-effort).
#
#   sudo bash collect.sh        # 시작 → 화면에 안내가 뜨면 그대로 두고 → 끝나면 Ctrl+C
#
# ⚠️ 이 스크립트는 WiFi/네트워크/라디오를 절대 건드리지 않는다(주최 측 원격 조종과 호환되도록 의도적으로
# 뺐다 — tools/rt/soak_guard.sh 의 "대회 조건"(WiFi 끔)과는 다른 스크립트다, 혼동하지 말 것).
# CAN 은 수신만 한다(candump/python-can `bus.recv()`, `bus.send()` 호출 없음 — 버스에 아무것도 안 쏨).
set -u
SELF_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SELF_DIR/../.." && pwd)
TS=$(date +%Y%m%d_%H%M%S)
BASE="$HOME/a1_race_capture"
OUT="$BASE/$TS"
CG_LOG="$HOME/a1_race_capture_can_guard.stderr.log"   # can_guard 팀이 이 고정 경로로 리다이렉트하면 자동 수거(README 참고)
START_EPOCH=$(date +%s)

mkdir -p "$OUT"/can "$OUT"/ros2 "$OUT"/serial "$OUT"/system "$OUT"/can_guard

PIDS=()
log() { echo "[collect] $*"; }

snapshot() {
  local when=$1
  {
    echo "=== $when: $(date -Iseconds) ==="
    echo "--- uname -a ---"; uname -a
    echo "--- /proc/cmdline ---"; cat /proc/cmdline 2>/dev/null
    echo "--- git HEAD (A1-BSW) ---"; git -C "$REPO" rev-parse HEAD 2>/dev/null || echo "(git 정보 없음)"
    echo "--- ip -details link show ---"; ip -details link show 2>/dev/null
    echo "--- cpufreq governor/freq (코어별) ---"
    for c in /sys/devices/system/cpu/cpu[0-9]*; do
      n=$(basename "$c")
      gov=$(cat "$c/cpufreq/scaling_governor" 2>/dev/null || echo "?")
      freq=$(cat "$c/cpufreq/scaling_cur_freq" 2>/dev/null || echo "?")
      echo "$n governor=$gov freq=${freq}kHz"
    done
  } > "$OUT/system/${when}.txt" 2>&1
}

log "출력 디렉터리: $OUT"
snapshot start

# --- 1. CAN 원시 캡처 (candump 1차 + python-can 보조, 둘 다 수신 전용) ---
CAN_IFS=$( { ip -o link show type can; ip -o link show type vcan; } 2>/dev/null \
          | awk -F': ' '{print $2}' | awk '{print $1}' | sort -u)
if [[ -z "$CAN_IFS" ]]; then
  log "CAN 인터페이스 없음 — 건너뜀(팀이 can0/can1 을 먼저 올려야 함)"
  echo "(수집 시작 시점에 CAN 인터페이스 없었음)" > "$OUT/can/NOTE.txt"
else
  for ifc in $CAN_IFS; do
    if command -v candump >/dev/null; then
      log "candump 시작: $ifc"
      # candump -l 은 파일명에 인터페이스명이 안 들어가고 타임스탬프만 쓴다 — 여러 인터페이스를
      # 동시에 캡처하면 같은 초에 시작될 경우 파일명이 겹칠 수 있어(실제 재현 확인) 인터페이스별
      # 하위 디렉터리로 분리한다.
      mkdir -p "$OUT/can/$ifc"
      ( cd "$OUT/can/$ifc" && exec candump -l "$ifc" ) &
      PIDS+=($!)
    else
      log "candump 없음 — $ifc 는 python-can 로거만 사용"
    fi
    log "python-can 보조 로거 시작: $ifc"
    python3 "$SELF_DIR/can_csv_logger.py" --channel "$ifc" --out "$OUT/can/${ifc}.csv" \
      2>>"$OUT/can/${ifc}_pylogger.stderr" &
    PIDS+=($!)
  done
fi

# --- 2. ROS2 bag (best-effort — ROS2 환경/토픽이 있을 때만) ---
(
  source /opt/ros/humble/setup.bash 2>/dev/null || exit 1
  [[ -f "$REPO/ros2_ws/install/setup.bash" ]] && source "$REPO/ros2_ws/install/setup.bash" 2>/dev/null
  command -v ros2 >/dev/null 2>&1 || exit 1
  topics=$(timeout 5 ros2 topic list 2>/dev/null | grep -v '^/rosout$' || true)
  if [[ -z "$topics" ]]; then
    echo "ROS2 토픽 없음 — 건너뜀"
    exit 1
  fi
  echo "ROS2 토픽 감지 — bag 기록 시작:"
  echo "$topics"
  cd "$OUT/ros2" && exec ros2 bag record -a -o "capture_$TS"
) > "$OUT/ros2/detect.log" 2>&1 &
PIDS+=($!)

# --- 3. can_guard 흔적 (best-effort — 과거 stderr 는 소급 못 잡음, README 의 리다이렉트 관례로 보완) ---
{
  if pgrep -f can_guard.py >/dev/null; then
    echo "can_guard 프로세스 감지(시작 시점):"
    ps -o pid,lstart,cmd -p "$(pgrep -f can_guard.py | tr '\n' ',' | sed 's/,$//')" 2>/dev/null
  else
    echo "can_guard 프로세스 못 찾음(시작 시점)"
  fi
} > "$OUT/can_guard/detect_start.txt"

# --- 4. GPS/INS 원시 시리얼 (best-effort — NMEA 파서 없이 원문 그대로, 줄마다 수신 시각 붙임) ---
for dev in /dev/ttyUSB* /dev/ttyACM*; do
  [[ -e "$dev" ]] || continue
  name=$(basename "$dev")
  log "시리얼 캡처 시작: $dev (어떤 장치인지는 나중에 확인)"
  stty -F "$dev" 115200 raw -echo 2>/dev/null || true
  ( stdbuf -oL cat "$dev" 2>/dev/null \
    | while IFS= read -r line; do printf '%s %s\n' "$(date +%s.%N)" "$line"; done \
    > "$OUT/serial/${name}.log" ) &
  PIDS+=($!)
done

cleanup() {
  echo
  log "종료 신호 받음 — 캡처 정리 중... (몇 초 걸릴 수 있습니다, 기다려 주세요)"
  for p in "${PIDS[@]}"; do kill -TERM "$p" 2>/dev/null || true; done
  sleep 2
  for p in "${PIDS[@]}"; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null || true; done

  snapshot end

  if [[ -f "$CG_LOG" ]]; then
    cp "$CG_LOG" "$OUT/can_guard/can_guard.stderr.log"
    log "can_guard 로그 수거함: $CG_LOG"
  fi
  {
    if pgrep -f can_guard.py >/dev/null; then
      echo "can_guard 프로세스 감지(종료 시점):"
      ps -o pid,lstart,cmd -p "$(pgrep -f can_guard.py | tr '\n' ',' | sed 's/,$//')" 2>/dev/null
    else
      echo "can_guard 프로세스 못 찾음(종료 시점)"
    fi
  } > "$OUT/can_guard/detect_end.txt"

  journalctl -k --since "@$START_EPOCH" --no-pager > "$OUT/system/journal_kernel.log" 2>/dev/null \
    || echo "(journalctl 권한 부족 또는 실패 — 건너뜀. sudo 로 재실행하면 잡힙니다)" > "$OUT/system/journal_kernel.log"

  {
    echo "# 대회장 데이터 수집 — $TS"
    echo
    echo "## CAN"
    for f in "$OUT"/can/*.csv; do
      [[ -f "$f" ]] || continue
      n=$(wc -l < "$f" 2>/dev/null || echo "?")
      echo "- $(basename "$f"): ${n}줄, $(du -h "$f" 2>/dev/null | cut -f1)"
    done
    for d in "$OUT"/can/*/; do
      [[ -d "$d" ]] || continue
      ifc=$(basename "$d")
      for f in "$d"candump-*.log; do
        [[ -f "$f" ]] || continue
        n=$(wc -l < "$f" 2>/dev/null || echo "?")
        echo "- $ifc/$(basename "$f") (candump): ${n}줄, $(du -h "$f" 2>/dev/null | cut -f1)"
      done
    done
    for f in "$OUT"/can/*_pylogger.stderr; do
      # 정상 시작/종료 로그는 항상 몇 줄 찍힌다 — "실패/오류/없음" 류 키워드가 있을 때만 경고
      # (candump 는 별개 프로세스라 이 보조 로거 문제와 무관하게 정상일 수 있음)
      grep -qE '실패|오류|없음|Traceback' "$f" 2>/dev/null || continue
      echo "- ⚠️ $(basename "$f")에 문제 있음:"
      grep -E '실패|오류|없음|Traceback' "$f" | sed 's/^/  /'
    done
    echo
    echo "## ROS2"
    cat "$OUT/ros2/detect.log" 2>/dev/null
    echo
    echo "## can_guard"
    cat "$OUT/can_guard"/detect_*.txt 2>/dev/null
    [[ -f "$OUT/can_guard/can_guard.stderr.log" ]] && echo "- stderr 로그 수거됨(can_guard.stderr.log)"
    echo
    echo "## 시리얼 (GPS/INS 추정, 장치별)"
    for f in "$OUT"/serial/*; do
      [[ -f "$f" ]] || continue
      echo "- $(basename "$f"): $(wc -l < "$f" 2>/dev/null)줄"
    done
  } > "$OUT/MANIFEST.md"

  ARCHIVE="$BASE/a1_race_capture_${TS}.tar.gz"
  tar -czf "$ARCHIVE" -C "$BASE" "$TS"
  echo
  echo "=================================================="
  echo " 수집 완료 — 아래 파일을 USB 로 복사하세요:"
  echo " $ARCHIVE"
  echo "=================================================="
  exit 0
}
trap cleanup SIGINT SIGTERM

log "수집 중입니다 — 끝나면 Ctrl+C 를 누르세요. (결과: $BASE/a1_race_capture_${TS}.tar.gz)"
wait
