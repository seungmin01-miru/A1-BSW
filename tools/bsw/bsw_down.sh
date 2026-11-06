#!/usr/bin/env bash
# BSW 끄기 — 정지 확인 → ESTOP(브레이크 잡고 auto 유지) → 노드·can_guard·녹화 정리 → CAN 로그 압축·sha256.
#   bash tools/bsw/bsw_down.sh [세션폴더] [--force]     # 기본 ~/a1_race_capture/bsw_latest
#
# 왜 이 순서인가(CLAUDE.md 1.4): auto 000 을 보내면 페일세이프 브레이크가 풀린다. 그래서 disarm 하지 않고 먼저
# can_guard ESTOP(가속 0, 브레이크 30 → 82 %, auto 유지)으로 잡은 뒤 끈다. 끈 뒤 0x210 이 끊겼을 때 차량 동작은
# 미확정(TODO-2)이므로 **바퀴 굄·사람 대기** 후에 끈다.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
source "$HERE/bsw.env"
FORCE=0; ARGS=()
for x in "$@"; do [ "$x" = --force ] && FORCE=1 || ARGS+=("$x"); done
D=$(readlink -f "${ARGS[0]:-$BSW_CAPTURE/bsw_latest}" 2>/dev/null)
[ -d "${D:-}" ] || { echo "세션 폴더 없음: ${ARGS[0]:-$BSW_CAPTURE/bsw_latest}"; exit 1; }

# 1. 정지 확인 — can_guard 상태 줄의 차속(0x201)
v=$(grep "차속" "$D/can_guard.log" 2>/dev/null | tail -1 | sed -n 's/.*차속 \([0-9.?]*\) km\/h.*/\1/p')
if [ "${v:-?}" = "?" ] || [ -z "${v:-}" ]; then
  [ $FORCE = 1 ] || { echo "❌ 차속을 모름(can_guard 로그) — 차가 선 것을 눈으로 확인했으면 --force"; exit 1; }
elif awk -v a="$v" -v b="$BSW_STOP_KPH" 'BEGIN{exit !(a > b)}'; then
  [ $FORCE = 1 ] || { echo "❌ 차속 $v km/h — 정지 뒤에 끈다(--force 로 무시)"; exit 1; }
fi
echo "[bsw_down] 차속 ${v:-?} km/h"

# 2. ESTOP 으로 브레이크를 잡는다(auto 비트 유지). 이미 ESTOP 이면 그대로.
if pgrep -f "^(/usr/bin/)?python3 .*safety/can_guard/can_guard.py" >/dev/null; then
  python3 "$REPO/safety/can_guard/estop.py" --source bsw_down >/dev/null 2>&1 \
    && echo "[bsw_down] ESTOP — 브레이크 30 → 82 % 램프 대기 2 s" && sleep 2
fi

# 3. 정리(disarm 생략 — auto 000 이 나가지 않게)
bash "$REPO/tools/track_day/stop.sh" "$D" --no-disarm
pkill -u "$USER" -f "tools/bsw/bsw_monitor.py" 2>/dev/null && echo "[bsw_down] 상태판 닫음"

# 4. CAN 로그 압축 + sha256 (원본은 남긴다 — raw/ 로 옮길 때 sha256 대조)
cd "$D" || exit 1
base=$(basename "$D")
: > can_logs.sha256
for f in kvaser_ch*.log candump_*.log can_guard_events.csv; do
  [ -s "$f" ] || continue
  out="${base}_${f%.*}.${f##*.}.zst"
  zstd -q -19 -f "$f" -o "$out" && sha256sum "$f" "$out" >> can_logs.sha256
done
{ echo "- 끝: $(date '+%F %T %z')"; echo "- CAN 로그(zstd):"; ls -1 ./*.zst 2>/dev/null | sed 's|^\./|  - |'; } >> SESSION.md
echo "[bsw_down] CAN 로그 압축·sha256 → $D/can_logs.sha256"
cat can_logs.sha256 | sed 's/^/  /'
raw=$(ls kvaser_ch*.log candump_*.log 2>/dev/null | head -1)
[ -n "$raw" ] && python3 "$REPO/bsw_test/00_SAF_estop/scripts/analysis.py" "$raw" --events can_guard_events.csv 2>/dev/null \
  | grep -E "^# |송신자 둘" | sed 's/^/  [00 분석] /' || true
echo "⚠️ 0x200 auto 비트가 남아 있으면(0x210 끊김 동작 미확정, TODO-2) 바퀴 굄 유지하고 사람이 인수."
