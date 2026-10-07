#!/usr/bin/env bash
# Kvaser 경로 리허설 — CANlib 가상 채널 두 개(서로 연결된 가상 버스)로, 실차 장치 없이 확인한다:
#   가짜 실차(가상 채널 B) ↔ can_guard --interface kvaser(가상 채널 A) + lift_cmd + kvaser_mirror(A → kv0)
# 전제: sudo bash tools/kvaser/install_kvaser.sh 완료, kv0 생성(sudo ip link add dev kv0 type vcan && sudo ip link set kv0 up)
#
#   bash tools/kvaser/rehearse_kvaser.sh       # 약 40초
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
CG="$REPO/safety/can_guard"
W=$(mktemp -d "${TMPDIR:-/tmp}/kvaser_rehearsal.XXXXXX")
TAG=kvr$$; CMD_SHM="${TAG}_cmd"; HB_SHM="${TAG}_hb"; BR=500000
PASS=0; FAIL=0
ok()  { echo "  ✅ PASS  $*"; PASS=$((PASS+1)); }
bad() { echo "  ❌ FAIL  $*"; FAIL=$((FAIL+1)); }
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; rm -f "/dev/shm/$CMD_SHM" "/dev/shm/$HB_SHM"; }
trap cleanup EXIT

# 가상 채널 찾기(진짜 Leaf 가 꽂혀 있으면 번호가 밀리므로 이름으로 찾는다)
read -r VA VB < <(python3 - <<'EOF'
from canlib import canlib
v = [ch for ch in range(canlib.getNumberOfChannels()) if 'virtual' in canlib.ChannelData(ch).channel_name.lower()]
print(*(v[:2] if len(v) >= 2 else ['-', '-']))
EOF
) || { echo "CANlib 사용 불가 — sudo bash tools/kvaser/install_kvaser.sh 먼저"; exit 2; }
[ "$VA" != "-" ] || { echo "가상 채널 2개가 없음 — sudo modprobe kvvirtualcan"; exit 2; }
ip link show kv0 >/dev/null 2>&1 || { echo "kv0 없음 — sudo ip link add dev kv0 type vcan && sudo ip link set kv0 up"; exit 2; }
echo "가상 채널 A=$VA(can_guard·미러) B=$VB(가짜 실차), 작업 폴더 $W"

python3 "$REPO/sil/vcan/fake_a1_vehicle.py" --interface kvaser --channel "$VB" --bitrate $BR 2>/dev/null & PIDS+=($!)
sleep 0.8

echo; echo "[1] 미러: Kvaser 채널 A(silent) → kv0"
python3 "$HERE/kvaser_mirror.py" --channel "$VA" --bitrate $BR --listen-only --log "$W/mirror_lo.log" 2> "$W/mirror_lo.err" & M=$!
sleep 1.5
n=$(timeout --foreground 2 candump kv0 | awk '{print $2}' | sort | uniq -c | tr '\n' ' ')
kill $M; wait $M 2>/dev/null
echo "$n" | grep -q " 200" && echo "$n" | grep -q " 201" && ok "kv0 에 0x200·0x201 복사됨: $n" || bad "kv0 복사 안 됨: $n"
grep -q '^([0-9.]*) kv0 200#' "$W/mirror_lo.log" && ok "원본 기록(candump -L 형식)" || bad "원본 기록 없음"

echo; echo "[2] can_guard(kvaser) 시작 전 점검 — 가상 버스에 다른 0x210 송신자 있으면 거부"
python3 - "$VB" "$CG" <<'EOF' & F=$!
import sys, time, can
sys.path.insert(0, sys.argv[2]); import kvaser_compat; kvaser_compat.apply()
b = can.Bus(channel=sys.argv[1], interface='kvaser', bitrate=500000)
t = time.time()
while time.time() - t < 4:
    b.send(can.Message(arbitration_id=0x210, data=bytes.fromhex('4d00000000070000'), is_extended_id=False)); time.sleep(0.02)
b.shutdown()
EOF
sleep 0.5
python3 "$CG/can_guard.py" --interface kvaser --channel "$VA" --bitrate $BR --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" > "$W/foreign.log" 2>&1; rc=$?
wait $F 2>/dev/null
[ $rc -eq 3 ] && ok "다른 0x210 송신자 → 시작 거부(코드 3)" || bad "종료 코드 $rc (기대 3)"

echo; echo "[3] C단계 흐름: 미러(normal) + can_guard + lift_cmd → auto on, steer 10 → 가짜 실차가 추종"
python3 "$HERE/kvaser_mirror.py" --channel "$VA" --bitrate $BR 2> "$W/mirror.err" & PIDS+=($!)
candump -L kv0 > "$W/c.candump" & PIDS+=($!)
sleep 0.5
python3 "$CG/can_guard.py" --interface kvaser --channel "$VA" --bitrate $BR --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" \
  --steer-limit-deg 30 --brake-limit-pct 60 --acc-limit-pct 10 --status-interval-s 1 > "$W/guard.log" 2>&1 & G=$!
for _ in $(seq 60); do [ -e "/dev/shm/$HB_SHM" ] && break; sleep 0.05; done
sleep 1.3
rm -f "$W/in.fifo"; mkfifo "$W/in.fifo"
python3 "$CG/lift_cmd.py" --interface kvaser --channel "$VA" --bitrate $BR --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" \
  --log "$W/lcmd.log" < "$W/in.fifo" > "$W/lcmd.out" 2>&1 & L=$!
exec 5> "$W/in.fifo"; sleep 0.5
echo "auto on" >&5; sleep 0.5; echo "steer 10" >&5; sleep 1.5; echo "status" >&5; sleep 0.5
T_KILL=$(date +%s.%N); kill -9 $G; wait $G 2>/dev/null; sleep 0.8
exec 5>&-; wait $L 2>/dev/null
grep -q 'INIT → ACTIVE' "$W/guard.log" && ok "can_guard(kvaser) INIT → ACTIVE" || bad "ACTIVE 전이 없음"
grep -q "can_guard 가 보낸 0x210" "$W/lcmd.log" && ok "lift_cmd 가 can_guard 송신값을 봄(로컬 TX 에코)" || bad "lift_cmd 에 can_guard 송신값 없음"
python3 - "$REPO/DBC/A1_dbc_fixed.dbc" "$W/c.candump" "$T_KILL" <<'EOF' | while IFS= read -r l; do case "$l" in OK*) echo "  ✅ PASS  ${l#OK }";; NG*) echo "  ❌ FAIL  ${l#NG }";; esac; done
import sys, re, statistics, cantools
db = cantools.database.load_file(sys.argv[1]); tk = float(sys.argv[3])
c210, c200 = [], []
for line in open(sys.argv[2]):
    m = re.match(r'\((\d+\.\d+)\) \S+ ([0-9A-F]+)#([0-9A-F]*)', line)
    if not m: continue
    t, fid, d = float(m.group(1)), int(m.group(2), 16), bytes.fromhex(m.group(3))
    if fid == 0x210: c210.append((t, db.decode_message(0x210, d, decode_choices=False)))
    if fid == 0x200: c200.append((t, db.decode_message(0x200, d, decode_choices=False)))
def out(ok, msg): print(('OK ' if ok else 'NG ') + msg)
dts = [b[0] - a[0] for a, b in zip(c210, c210[1:]) if b[0] < tk]
out(len(dts) > 20 and 18 <= statistics.median(dts) * 1000 <= 22, f'kv0 에서 본 can_guard 0x210 {len(c210)}개, 주기 {statistics.median(dts)*1000 if dts else 0:.1f} ms')
out(10 in {d['steer_command'] for _, d in c210}, '조향 10° 명령이 버스에 나감')
pos = [d['steer_postion'] for t, d in c200 if t < tk]
out(bool(pos) and 8.5 <= max(pos) <= 10.5, f'가짜 실차 조향 위치 {max(pos) if pos else None}° (배율 1)')
after = [t for t, _ in c210 if t > tk + 0.1]
out(not after, f'can_guard kill -9 뒤 0x210 {len(after)}개 (기대 0)')
EOF

echo
echo "=================================================="
echo " Kvaser 리허설: 위 항목 확인 (기록: $W)"
echo "=================================================="
