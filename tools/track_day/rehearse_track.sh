#!/usr/bin/env bash
# 트랙 인터페이스 리허설 — 제어기 대역(test_target_pub) → a1_control_adapter → can_guard → vcan0 가짜 차량.
# 실차 버스는 안 건드린다(sudo 불필요). 항목별 PASS/FAIL 출력. 약 1분 30초.
#
#   bash tools/track_day/rehearse_track.sh
#
# 확인하는 것: disarm 중 auto 0 / 입력 없을 때 arm 거부 / 타이어각→핸들각(조향비 7.5)·가감속→% 변환이 0x210 raw 로 /
# arm 순간 핸들 속도 제한 / 10 Hz 느린 제어기에서도 can_guard 워치독 미발동 / 제어기 kill → 고장 래치 → HOLDING →
# STOPPED(조향 고정·가속 0·브레이크) / 래치 중 arm 거부·disarm 해제 / can_guard 재시작 후 자동 재연결 /
# NaN 입력 → 고장 래치.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
CG="$REPO/safety/can_guard"
WS="$REPO/ros2_ws"
CH=vcan0
W=$(mktemp -d "${TMPDIR:-/tmp}/track_rehearsal.XXXXXX")
TAG=trk$$
CMD_SHM="${TAG}_cmd"; HB_SHM="${TAG}_hb"
export ROS_DOMAIN_ID=${REHEARSAL_DOMAIN_ID:-73}
PASS=0; FAIL=0
ok()  { echo "  ✅ PASS  $*"; PASS=$((PASS+1)); }
bad() { echo "  ❌ FAIL  $*"; FAIL=$((FAIL+1)); }
report() { while IFS= read -r l; do case "$l" in OK*) ok "${l#OK }";; NG*) bad "${l#NG }";; esac; done; }
FV_PID=""; REC_PID=""; G_PID=""; A_PID=""; T_PID=""; B_PID=""; D_PID=""
cleanup() { for p in $T_PID $A_PID $B_PID $D_PID $G_PID $FV_PID $REC_PID; do kill "$p" 2>/dev/null; done; wait 2>/dev/null
            rm -f "/dev/shm/$CMD_SHM" "/dev/shm/$HB_SHM"; }
trap cleanup EXIT

ip link show $CH >/dev/null 2>&1 || { echo "vcan0 없음 — sudo bash $REPO/sil/vcan/vcan_up.sh 먼저"; exit 2; }
[ -f "$WS/install/setup.bash" ] || { echo "ros2_ws 빌드 필요: cd $WS && colcon build --symlink-install"; exit 2; }
set +u; source /opt/ros/humble/setup.bash; source "$WS/install/setup.bash"; set -u
echo "작업 폴더: $W (ROS_DOMAIN_ID=$ROS_DOMAIN_ID)"
BIN="$WS/install/a1_control_adapter/lib/a1_control_adapter"   # ros2 run 래퍼 대신 직접 — kill 이 노드에 바로 가게

mark() { date +%s.%N; }
start_guard() { python3 "$CG/can_guard.py" --channel $CH --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" \
    --status-interval-s 1 > "$W/$1" 2>&1 & G_PID=$!
  for _ in $(seq 60); do [ -e "/dev/shm/$CMD_SHM" ] && break; sleep 0.05; done; }
stop_guard() { kill -TERM $G_PID 2>/dev/null; wait $G_PID 2>/dev/null; G_PID=""; }
start_pub() { "$BIN/test_target_pub" --ros-args -p mode:=const "$@" >> "$W/pub.log" 2>&1 & T_PID=$!; }
kill_pub() { kill -9 $T_PID 2>/dev/null; wait $T_PID 2>/dev/null; T_PID=""; }
svc() { timeout 15 ros2 service call "/control/adapter/$1" "std_srvs/srv/$2" "$3" 2>&1 | tr '\n' ' '; }
wait_topic() { for _ in $(seq 40); do ros2 topic list 2>/dev/null | grep -q "^$1\$" && return 0; sleep 0.25; done; return 1; }

python3 "$REPO/sil/vcan/fake_a1_vehicle.py" --channel $CH 2>/dev/null & FV_PID=$!
candump -L $CH > "$W/bus.candump" & REC_PID=$!
sleep 0.5
start_guard guard1.log
"$WS/install/a1_can_bridge/lib/a1_can_bridge/can_raw_bridge" --ros-args -p channel:=$CH > "$W/bridge.log" 2>&1 & B_PID=$!
"$WS/install/a1_can_bridge/lib/a1_can_bridge/a1_status_decoder" > "$W/decoder.log" 2>&1 & D_PID=$!
"$BIN/cmd_adapter" --ros-args \
  --params-file "$WS/src/a1_control_adapter/config/track.yaml" \
  -p cmd_shm:="$CMD_SHM" -p hb_shm:="$HB_SHM" -p can_guard_dir:="$CG" > "$W/adapter.log" 2>&1 & A_PID=$!
wait_topic /control/adapter/status || echo "  (어댑터 토픽이 늦게 뜸 — adapter.log 확인)"
sleep 1.5

echo; echo "[1] 입력 없음 → arm 거부, disarm 중 auto 0"
r=$(svc arm_steer SetBool "{data: true}")
echo "$r" | grep -q "success=False" && ok "제어기 입력 없을 때 arm_steer 거부" || bad "arm 거부 안 됨: $r"

echo; echo "[2] 10 Hz 제어기(타이어 4°, −1.0 m/s²) — disarm shadow → arm → 변환값 송신"
start_pub -p rate_hz:=10.0 -p steer_tire_deg:=4.0 -p accel_mps2:=-1.0
sleep 3
M_DIS0=$(mark); sleep 1.5; M_DIS1=$(mark)
st=$(timeout 5 ros2 topic echo --once /control/adapter/status 2>/dev/null | tr '\n' ' ')
echo "$st" | grep -q "out_steer_wheel_deg: 30.0" && echo "$st" | grep -q "out_brake_pct: 40.0" \
  && ok "shadow 상태 토픽: 핸들 30.0°·브레이크 40 % (disarm 중에도 변환값 표시)" || bad "상태 토픽 값 이상: $st"
r1=$(svc arm_steer SetBool "{data: true}"); M_ARM_S=$(mark)
r2=$(svc arm_long SetBool "{data: true}"); M_ARM=$(mark)
echo "$r1 $r2" | grep -o "success=True" | wc -l | grep -q 2 && ok "arm_steer·arm_long 수락" || bad "arm 실패: $r1 / $r2"
sleep 2
M_ACC0=$(mark); ros2 param set /a1_test_target_pub accel_mps2 0.7 >/dev/null 2>&1; M_ACC=$(mark)
sleep 2

echo; echo "[3] 제어기 kill -9 → 고장 래치 → can_guard HOLDING → STOPPED"
kill_pub; M_KILL=$(mark)
sleep 3.5
r=$(svc arm_long SetBool "{data: true}")
echo "$r" | grep -q "success=False" && ok "고장 래치 중 arm 거부" || bad "래치 중 arm 이 받아짐: $r"
r=$(svc disarm Trigger "{}")
echo "$r" | grep -q "success=True" && ok "disarm 으로 래치 해제" || bad "disarm 실패: $r"

echo; echo "[4] can_guard 재시작 → 어댑터 자동 재연결(disarm → auto 0)"
stop_guard; start_guard guard2.log; M_RESTART=$(mark)
sleep 3; M_RE1=$(mark)

echo; echo "[5] NaN 입력 → 고장 래치"
start_pub -p rate_hz:=50.0 -p steer_tire_deg:=0.0 -p accel_mps2:=-0.5
sleep 3
r=$(svc arm_long SetBool "{data: true}")
echo "$r" | grep -q "success=True" && ok "재시작 후 arm_long 수락" || bad "재시작 후 arm 실패: $r"
sleep 1
ros2 topic pub --once /control/target a1_can_msgs/msg/ControllerOutput "{accel_mps2: .nan, steer_tire_deg: 0.0}" >/dev/null 2>&1
M_NAN=$(mark); sleep 1.5
st=$(timeout 5 ros2 topic echo --once /control/adapter/status 2>/dev/null | tr '\n' ' ')
echo "$st" | grep -q "writing: false" && echo "$st" | grep -q "입력 거부" \
  && ok "NaN → 래치(writing false, note=입력 거부)" || bad "NaN 래치 안 됨: $st"
sleep 1
kill_pub; svc disarm Trigger "{}" >/dev/null; sleep 0.5
stop_guard
for p in $A_PID $B_PID $D_PID; do kill $p 2>/dev/null; done; wait $A_PID $B_PID $D_PID 2>/dev/null; A_PID=""; B_PID=""; D_PID=""
kill $REC_PID 2>/dev/null; wait $REC_PID 2>/dev/null; REC_PID=""

report < <(python3 - "$REPO/DBC/A1_dbc_fixed.dbc" "$W/bus.candump" "$W/guard1.log" "$W/guard2.log" \
  $M_DIS0 $M_DIS1 $M_ARM_S $M_ARM $M_ACC0 $M_ACC $M_KILL $M_RESTART $M_RE1 $M_NAN <<'PY'
import re, sys, statistics, cantools
db = cantools.database.load_file(sys.argv[1]); guard = open(sys.argv[3]).read(); guard2 = open(sys.argv[4]).read()
dis0, dis1, arm_s, arm, acc0, acc, kill, restart, re1, nan = map(float, sys.argv[5:])
f = []
for line in open(sys.argv[2]):
    m = re.match(r'\((\d+\.\d+)\) \S+ 210#([0-9A-F]+)', line)
    if m:
        f.append((float(m.group(1)), db.decode_message(0x210, bytes.fromhex(m.group(2)), decode_choices=False)))
def out(c, s): print(('OK ' if c else 'NG ') + s)
def win(a, b): return [d for t, d in f if a <= t < b]
def auto(d): return (d['steer_is_auto_command'], d['break_is_auto_command'], d['acc_is_auto_command'])
dts = [b[0] - a[0] for a, b in zip(f, f[1:]) if not (restart - 1 < a[0] < restart + 1)]
out(max(dts) < 0.03, f'0x210 주기 중앙값 {statistics.median(dts)*1000:.1f} ms, 최대 {max(dts)*1000:.1f} ms (재시작 구간 제외)')
d = win(dis0, dis1)
out(bool(d) and all(auto(x) == (0, 0, 0) and x['steer_command'] == 0 and x['break_command'] == 0 for x in d),
    f'disarm(shadow): {len(d)}프레임 auto 000·명령 0')
first = [x['steer_command'] for x in win(arm_s - 2.5, arm_s + 1.5) if x['steer_is_auto_command']]
out(bool(first) and first[0] < 29.0 and 30.0 in first,
    f'arm 순간 핸들 속도 제한: 첫 값 {first[0] if first else None}° → 30.0° (120 °/s)')
a = win(arm + 0.3, acc0)
out(bool(a) and all(auto(x) == (1, 1, 1) and x['steer_command'] == 30.0 and x['break_command'] == 40
        and x['acc_command'] == 0 for x in a),
    f'타이어 4° → 핸들 30.0°(raw 300), −1.0 m/s² → 브레이크 40 %, 가속 0 ({len(a)}프레임)')
b = win(acc + 0.4, kill)
out(bool(b) and all(x['acc_command'] == 15 and x['break_command'] == 0 for x in b),
    f'+0.7 m/s² → 가속 15 %, 브레이크 0 ({sorted({x["acc_command"] for x in b})})')
out('INIT → ACTIVE' in guard and guard.count('→ HOLDING') == 1,
    f'10 Hz 입력 구간 워치독 미발동(HOLDING 전이는 kill 때 1번뿐: {guard.count("→ HOLDING")}회)')
h = win(kill + 0.35, kill + 3.0)
out(bool(h) and {x['steer_command'] for x in h} == {30.0} and all(x['acc_command'] == 0 for x in h)
    and max(x['break_command'] for x in h) > 0,
    f'kill 뒤 조향 {sorted({x["steer_command"] for x in h})}° 고정·가속 0·브레이크 최대 {max((x["break_command"] for x in h), default=None)} %')
out('→ STOPPED' in guard, 'can_guard STOPPED 도달')
r = win(restart + 1.8, re1)
out(bool(r) and all(auto(x) == (0, 0, 0) for x in r), f'can_guard 재시작 후 재연결·disarm auto 000 ({len(r)}프레임)')
n = win(nan + 0.4, nan + 1.2)
out(('ACTIVE → HOLDING' in guard2 or 'ACTIVE → STOPPED' in guard2) and bool(n) and all(x['acc_command'] == 0 for x in n),
    f'NaN 입력 뒤 can_guard HOLDING/STOPPED(가짜 차량 정지 중이라 바로 STOPPED 가능)·가속 0 (브레이크 {sorted({x["break_command"] for x in n})[-1:] if n else "?"} %)')
PY
)
echo; echo "결과: PASS $PASS / FAIL $FAIL   (로그: $W)"
[ $FAIL -eq 0 ]
