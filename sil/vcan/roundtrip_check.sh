#!/usr/bin/env bash
# vcan0 왕복 확인 (B-2): candump 수신 대기 → cansend 송신 → 같은 프레임이 수신됐는지 자동 판정.
# sudo 불필요 (vcan0 이 이미 up 이면). can-utils 필요: sudo apt install can-utils
set -euo pipefail
IF=${1:-vcan0}
command -v candump >/dev/null || { echo "can-utils 없음: sudo apt install can-utils"; exit 1; }
ip link show "$IF" >/dev/null 2>&1 || { echo "$IF 없음: sudo bash vcan_up.sh"; exit 1; }

FRAME="712#0102030405060708"          # 0x712 EAIT_INFO_SPD 자리, 임의 페이로드
OUT=$(mktemp)
candump -n 1 -T 2000 "$IF" > "$OUT" &  # 프레임 1개 받거나 2초 지나면 종료
sleep 0.3
cansend "$IF" "$FRAME"
wait $! || true
echo "송신: $FRAME"; echo "수신: $(cat "$OUT" | tr -s ' ')"
if grep -qE "712\s+\[8\]\s+01 02 03 04 05 06 07 08" "$OUT"; then echo "✅ 왕복 OK ($IF)"; rm -f "$OUT"; exit 0
else echo "❌ 왕복 실패"; rm -f "$OUT"; exit 1; fi
