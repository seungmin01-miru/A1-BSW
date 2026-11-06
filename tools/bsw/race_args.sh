# shellcheck shell=bash
# 경기 모드 옵션 묶음 — bsw.env 의 BSW_RACE_* → can_guard 옵션. bsw_up 이 source 한다(차량·CAN 에 닿지 않아 혼자 시험할 수 있다:
#   python3 -m pytest -q tools/bsw/test_race_ops.py). 값에 공백이 없어야 한다(bringup --guard-args 가 공백으로 나눈다).

# RACE_GUARD_ARGS 배열을 채운다. bsw.env 를 먼저 source 할 것.
bsw_race_guard_args() {
  RACE_GUARD_ARGS=(--race
    --race-confirm-frames "$BSW_RACE_CONFIRM_FRAMES"
    --race-stop-action "$BSW_RACE_STOP_ACTION"
    --race-signal-timeout-s "$BSW_RACE_SIGNAL_TIMEOUT_S"
    --resume-mode "$BSW_RACE_RESUME_MODE"
    --res-alive-timeout-s "$BSW_RACE_RES_ALIVE_TIMEOUT_S"
    --pre-go-brake-pct "$BSW_RACE_PRE_GO_BRAKE_PCT"
    --slow-target-kph "$BSW_RACE_SLOW_TARGET_KPH"
    --slow-acc-cut-kph "$BSW_RACE_SLOW_ACC_CUT_KPH"
    --slow-brake-kph "$BSW_RACE_SLOW_BRAKE_KPH"
    --finish-cap-kph "$BSW_RACE_FINISH_CAP_KPH"
    --parked-speed-kph "$BSW_RACE_PARKED_SPEED_KPH"
    --parked-after-s "$BSW_RACE_PARKED_AFTER_S"
    --parked-brake-pct "$BSW_RACE_PARKED_BRAKE_PCT"
    --input-grace-s "$BSW_RACE_INPUT_GRACE_S"
    --recover-budget-m "$BSW_RACE_RECOVER_BUDGET_M"
    --recover-delay-s "$BSW_RACE_RECOVER_DELAY_S"
    --recover-alat-max "$BSW_RACE_RECOVER_ALAT_MAX"
    --adapter-hold-after-s "$BSW_RACE_ADAPTER_HOLD_AFTER_S"
    --speed-limit-practice-kph "$BSW_RACE_SPEED_LIMIT_PRACTICE_KPH"
    --speed-limit-fast-period "$BSW_RACE_SPEED_LIMIT_FAST_PERIOD"
    --speed-limit-keep-period "$BSW_RACE_SPEED_LIMIT_KEEP_PERIOD"
    --speed-limit-confirm-frames "$BSW_RACE_SPEED_LIMIT_CONFIRM_FRAMES"
    --speed-limit-mismatch-s "$BSW_RACE_SPEED_LIMIT_MISMATCH_S"
    --speed-limit-release-note-s "$BSW_RACE_SPEED_LIMIT_RELEASE_NOTE_S"
    --speed-limit-startup-timeout "$BSW_RACE_SPEED_LIMIT_STARTUP_TIMEOUT")
  [ -n "$BSW_RACE_TRACK_MIN_RADIUS_M" ] && RACE_GUARD_ARGS+=(--track-min-radius-m "$BSW_RACE_TRACK_MIN_RADIUS_M")
  [ -n "$BSW_RACE_SPEED_LIMIT_RELEASE_KPH" ] && RACE_GUARD_ARGS+=(--speed-limit-release-kph "$BSW_RACE_SPEED_LIMIT_RELEASE_KPH")
  [ "$BSW_RACE_RESUME_LOW_SPEED" = 1 ] && RACE_GUARD_ARGS+=(--resume-low-speed)
  [ "$BSW_RACE_SPEED_LIMIT_TX" = 1 ] && RACE_GUARD_ARGS+=(--speed-limit-tx)
  return 0
}

# 어댑터 표(yaml)의 hold_after_s 값을 출력한다. 없으면 아무것도 출력하지 않는다.
bsw_race_hold_after() {
  awk '/^[[:space:]]*hold_after_s:/ { print $2; exit }' "$1"
}

# 어댑터 hold_after_s 와 can_guard --adapter-hold-after-s 가 같은지 본다(계획서 8.2: 끊긴 시간 = 명령 나이 + 이 값).
# 인자: 어댑터 표 yaml, can_guard 에 넘길 값. 다르거나 못 읽으면 메시지를 내고 1.
bsw_race_check_hold() {
  local v
  v=$(bsw_race_hold_after "$1")
  if [ -z "$v" ]; then
    echo "❌ $1 에 hold_after_s 가 없음 — 경기 모드는 어댑터 hold_after_s 가 있어야 한다"
    return 1
  fi
  if ! awk -v a="$v" -v b="$2" 'BEGIN { exit !(a + 0 == b + 0) }'; then
    echo "❌ 어댑터 hold_after_s=$v ($1) 와 can_guard --adapter-hold-after-s=$2 가 다름 — 끊긴 시간 계산이 어긋난다"
    return 1
  fi
  return 0
}
