#!/usr/bin/env bash
# 리프트 송신 시험(C단계) 리허설 — can_guard + lift_cmd 를 vcan0 의 가짜 차량(sil/vcan/fake_a1_vehicle.py) 상대로
# 내일 순서(C0~C7) 그대로 돌리고 항목별 PASS/FAIL 을 출력한다. sudo 불필요, 실차 버스(can0/can1)는 안 건드림.
#
#   bash tools/race_day/rehearse_lift_guard.sh          # 약 1분 30초
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
CG="$REPO/safety/can_guard"
CH=vcan0
W=$(mktemp -d "${TMPDIR:-/tmp}/lift_guard_rehearsal.XXXXXX")
TAG=reh$$
CMD_SHM="${TAG}_cmd"; HB_SHM="${TAG}_hb"
LIMITS=(--steer-limit-deg 30 --brake-limit-pct 60 --acc-limit-pct 10)
PASS=0; FAIL=0
ok()  { echo "  ✅ PASS  $*"; PASS=$((PASS+1)); }
bad() { echo "  ❌ FAIL  $*"; FAIL=$((FAIL+1)); }
report() { while IFS= read -r l; do case "$l" in OK*) ok "${l#OK }";; NG*) bad "${l#NG }";; esac; done; }
FV_PID=""; REC_PID=""; FS_PID=""; G_PID=""; L_PID=""
cleanup() { for p in $L_PID $G_PID $FV_PID $REC_PID $FS_PID; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup EXIT

ip link show $CH >/dev/null 2>&1 || { echo "vcan0 없음 — sudo bash $REPO/sil/vcan/vcan_up.sh 먼저"; exit 2; }
echo "작업 폴더: $W"

start_rec() { candump -L $CH > "$W/$1.candump" & REC_PID=$!; sleep 0.3; }
stop_rec()  { sleep 0.3; kill $REC_PID 2>/dev/null; wait $REC_PID 2>/dev/null; REC_PID=""; }
start_fv()  { python3 "$REPO/sil/vcan/fake_a1_vehicle.py" --channel $CH 2>/dev/null & FV_PID=$!; sleep 0.5; }
stop_fv()   { kill $FV_PID 2>/dev/null; wait $FV_PID 2>/dev/null; FV_PID=""; }
start_foreign() { ( while :; do cansend $CH 210#4D00000000070000; sleep 0.02; done ) & FS_PID=$!; }
stop_foreign()  { kill $FS_PID 2>/dev/null; wait $FS_PID 2>/dev/null; FS_PID=""; }
# can_guard: 리프트 시험 한계 + 상태 표시. $1 = 이름, 나머지 = 추가 인자
start_guard() { local n=$1; shift
  python3 "$CG/can_guard.py" --channel $CH --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" "${LIMITS[@]}" \
    --status-interval-s 1 "$@" > "$W/$n.guard.log" 2>&1 & G_PID=$!
  for _ in $(seq 50); do [ -e "/dev/shm/$HB_SHM" ] && break; sleep 0.05; done; }
stop_guard() { kill -TERM $G_PID 2>/dev/null; wait $G_PID 2>/dev/null; G_RC=$?; G_PID=""; }
# lift_cmd: fifo stdin 으로 띄워 명령을 시간에 맞춰 넣는다. $1 = 이름, 나머지 = 추가 인자
start_lcmd() { local n=$1; shift; rm -f "$W/$n.fifo"; mkfifo "$W/$n.fifo"
  python3 "$CG/lift_cmd.py" --channel $CH --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" --log "$W/$n.lcmd.log" "$@" \
    < "$W/$n.fifo" > "$W/$n.lcmd.out" 2>&1 & L_PID=$!
  exec 4> "$W/$n.fifo"; sleep 0.5; }
say() { echo "$*" >&4; }
stop_lcmd() { exec 4>&- ; wait $L_PID 2>/dev/null; L_PID=""; }
mark() { date +%s.%N; }

analyze() { python3 - "$REPO/DBC/A1_dbc_fixed.dbc" "$@" <<'EOF'
import sys, re, statistics, cantools
db = cantools.database.load_file(sys.argv[1]); mode = sys.argv[2]; path = sys.argv[3]; marks = [float(x) for x in sys.argv[4:]]
ours, info, foreign = [], [], []
for line in open(path):
    m = re.match(r'\((\d+\.\d+)\) \S+ ([0-9A-F]+)#([0-9A-F]*)', line)
    if not m: continue
    t, fid, data = float(m.group(1)), int(m.group(2), 16), bytes.fromhex(m.group(3))
    if fid == 0x210:
        (foreign if data.hex() == '4d00000000070000' else ours).append((t, db.decode_message(0x210, data, decode_choices=False)))
    elif fid == 0x200:
        info.append((t, db.decode_message(0x200, data, decode_choices=False)))
def out(ok, msg): print(('OK ' if ok else 'NG ') + msg)
def win(a, b): return [d for t, d in ours if a <= t < b]
def auto(d): return (d['steer_is_auto_command'], d['break_is_auto_command'], d['acc_is_auto_command'])
def zero(d): return all(v == 0 for v in d.values())
if mode == 'session':
    m_start, m_auto, m_steer10, m_steer45, m_brake, m_acc, m_percoff, m_end = marks
    dts = [b[0] - a[0] for a, b in zip(ours, ours[1:])]
    med = statistics.median(dts) * 1000
    out(19 <= med <= 21 and max(dts) < 0.05, f'0x210 주기 중앙값 {med:.2f} ms, 최대 간격 {max(dts)*1000:.1f} ms')
    pre = win(0, m_auto)
    out(bool(pre) and all(zero(d) for d in pre), f'C1: auto on 전 {len(pre)}프레임 전부 auto 0·명령 0')
    c2 = win(m_auto + 0.15, m_steer10)
    out(bool(c2) and all(auto(d) == (1, 1, 1) and d['steer_command'] == 0 for d in c2), f'C2: auto on 뒤 auto 111·조향 0 ({len(c2)}프레임)')
    c3 = win(m_steer10 + 0.15, m_steer45)
    out(bool(c3) and all(d['steer_command'] == 10 for d in c3), f'C3: 조향 10° 송신 {sorted({d["steer_command"] for d in c3})}')
    pos = [d['steer_postion'] for t, d in info if m_steer10 + 0.7 < t < m_steer45]
    out(bool(pos) and 8.5 <= max(pos) <= 10.5, f'C3: 가짜 차량 조향 위치 {max(pos) if pos else None}° (×0.1 인코딩 왕복)')
    c3b = win(m_steer45 + 0.15, m_steer45 + 0.75)
    out(bool(c3b) and all(d['steer_command'] == 30 for d in c3b), f'이중 한계: lift_cmd 45° → can_guard 가 30° 로 클램프 {sorted({d["steer_command"] for d in c3b})}')
    c4 = win(m_brake + 0.15, m_brake + 0.75)
    out(bool(c4) and {d['break_command'] for d in c4} == {30} and all(d['acc_command'] == 0 for d in c4), 'C4: 브레이크 30 %, 가속 0')
    c5 = win(m_acc + 0.15, m_percoff)
    out(5 in {d['acc_command'] for d in c5} and all(d['break_command'] == 0 for d in c5), 'C5: 가속 5 %, 브레이크 0')
    allv = [d for t, d in ours]
    out(max(abs(d['steer_command']) for d in allv) <= 30 and max(d['acc_command'] for d in allv) <= 10 and
        max(d['break_command'] for d in allv) <= 60, '한계 밖 값 송신 0건')
    out(not any(d['acc_command'] > 0 and d['break_command'] > 0 for d in allv), '가속·브레이크 동시 송신 0건')
    deg = win(m_percoff + 0.7, m_percoff + 2.3)
    out(bool(deg) and all(d['acc_command'] == 0 for d in deg) and {d['steer_command'] for d in deg} == {7}
        and deg[-1]['break_command'] > deg[0]['break_command'], f'C6: 하트비트 끊김 → 조향 {sorted({d["steer_command"] for d in deg})}° 고정(마지막 명령 7°)·가속 0·브레이크 {deg[0]["break_command"] if deg else "?"}→{deg[-1]["break_command"] if deg else "?"} %')
    stp = win(m_percoff + 3.6, m_end)
    out(bool(stp) and all(auto(d) == (1, 1, 1) and d['break_command'] == 30 and d['acc_command'] == 0
        and d['steer_command'] == 7 for d in stp),
        f'C6: STOPPED(hold) = auto 유지·조향 7° 유지·브레이크 30 %·가속 0 ({len(stp)}프레임)')
elif mode == 'stops_after':
    after = [t for t, _ in ours if t > marks[0] + 0.05]
    out(not after, f'can_guard 강제 종료 뒤 0x210 {len(after)}개 (기대 0 — 이후는 차량 자체 타임아웃의 몫)')
elif mode == 'no_ours':
    out(not ours, f'우리 0x210 {len(ours)}개 (기대 0), 다른 송신자 {len(foreign)}개')
elif mode == 'p1':
    m_kill = marks[0]
    hold = win(m_kill + 0.12, m_kill + 2.0)
    out(bool(hold) and {d['steer_command'] for d in hold} == {12} and all(d['acc_command'] == 0 for d in hold)
        and hold[-1]['break_command'] > hold[0]['break_command'],
        f'lift_cmd kill -9 → 조향 {sorted({d["steer_command"] for d in hold})}° 고정(마지막 명령 12°), 브레이크 {hold[0]["break_command"] if hold else "?"}→{hold[-1]["break_command"] if hold else "?"} %')
    late = win(m_kill + 2.3, m_kill + 3.0)
    out(bool(late) and all(d['break_command'] == 30 and auto(d) == (1, 1, 1) and d['steer_command'] == 12 for d in late),
        'STOPPED: 조향 12° 유지·브레이크 30 % 유지·auto 유지')
EOF
}

echo; echo "[1] C0 — 다른 장치가 0x210 송신 중이면 can_guard 시작 거부(종료 코드 3), 우리 프레임 0"
start_fv; start_foreign; sleep 0.3; start_rec foreign
python3 "$CG/can_guard.py" --channel $CH --cmd-shm "$CMD_SHM" --hb-shm "$HB_SHM" "${LIMITS[@]}" > "$W/foreign.guard.log" 2>&1; rc=$?
stop_rec; stop_foreign; stop_fv
[ $rc -eq 3 ] && ok "종료 코드 3" || bad "종료 코드 $rc (기대 3)"
grep -q '시작 거부' "$W/foreign.guard.log" && ok "거부 사유 로그" || bad "거부 사유 로그 없음"
report < <(analyze no_ours "$W/foreign.candump")
[ ! -e "/dev/shm/$CMD_SHM" ] && ok "거부 후 공유메모리 정리됨" || bad "거부 후 /dev/shm 에 $CMD_SHM 남음"

echo; echo "[2] C1~C6 정상 세션 — INIT → auto on → 조향 → 이중 한계 → 브레이크 → 가속 → 하트비트 끊김 → STOPPED"
start_fv; start_rec session
start_guard session; sleep 1.5                       # 시작 전 점검 1초 + INIT 송신 관찰
start_lcmd session --steer-limit-deg 60              # lift_cmd 한계를 일부러 넓혀 can_guard 한계(30°)를 시험
M_START=$(mark)
M_AUTO=$(mark);    say "auto on";   sleep 0.8
M_S10=$(mark);     say "steer 10";  sleep 1.2
M_S45=$(mark);     say "steer 45";  sleep 0.8
say "steer 0"; sleep 0.3
M_BRK=$(mark);     say "brake 30";  sleep 0.8
say "brake 0"; sleep 0.2
M_ACC=$(mark);     say "acc 5";     sleep 0.8
say "acc 0"; say "steer 7"; sleep 0.5
M_POFF=$(mark);    say "perception off"; sleep 5.0
say "status"; sleep 0.5
M_END=$(mark)
stop_lcmd; sleep 0.3; stop_guard; stop_rec; stop_fv
grep -q 'INIT → ACTIVE' "$W/session.guard.log" && ok "로그: INIT → ACTIVE" || bad "로그에 INIT → ACTIVE 없음"
# 정지 상태(차속 ≤ 3 km/h)라 하트비트가 끊기면 DEGRADED 를 거치지 않고 STOPPED 로 직행하는 게 설계대로다
grep -qE 'ACTIVE → (DEGRADED|STOPPED)' "$W/session.guard.log" && ok "로그: 하트비트 끊김 → $(grep -oE 'ACTIVE → (DEGRADED|STOPPED)' "$W/session.guard.log" | head -1) (정지 상태라 STOPPED 직행 가능)" || bad "로그에 하트비트 끊김 전이 없음"
grep -q '→ STOPPED' "$W/session.guard.log" && ok "로그: STOPPED 도달" || bad "로그에 STOPPED 전이 없음"
grep -q 'plausibility 위반' "$W/session.guard.log" && ok "로그: can_guard 클램프 위반 기록(45° → 30°)" || bad "클램프 위반 로그 없음"
grep -q '보냄: 조향' "$W/session.guard.log" && ok "로그: 1초 상태 표시 줄" || bad "상태 표시 줄 없음"
grep -q "can_guard 가 보낸 0x210" "$W/session.lcmd.log" && ok "lift_cmd status 에 can_guard 송신값 표시" || bad "lift_cmd status 표시 없음"
[ "${G_RC:-1}" -eq 0 ] && ok "can_guard SIGTERM 종료 코드 0" || bad "can_guard 종료 코드 ${G_RC:-?}"
report < <(analyze session "$W/session.candump" $M_START $M_AUTO $M_S10 $M_S45 $M_BRK $M_ACC $M_POFF $M_END)

echo; echo "[3] C6(a) — 바퀴가 도는 중(가짜 차속 > 3 km/h) lift_cmd 강제 종료(제어 노드 죽음, P-1) → HOLDING → STOPPED"
start_fv; start_rec p1
start_guard p1; sleep 1.3
start_lcmd p1; say "auto on"; say "steer 12"; say "acc 10"; sleep 1.5
M_KILL=$(mark); kill -9 $L_PID; exec 4>&- ; wait $L_PID 2>/dev/null; L_PID=""; sleep 3.2
stop_guard; stop_rec; stop_fv
grep -q 'ACTIVE → HOLDING' "$W/p1.guard.log" && ok "로그: ACTIVE → HOLDING" || bad "로그에 HOLDING 전이 없음"
grep -q 'HOLDING → STOPPED' "$W/p1.guard.log" && ok "로그: HOLDING → STOPPED (브레이크로 감속 → 차속 3 km/h 이하 또는 2초)" || bad "로그에 STOPPED 전이 없음"
report < <(analyze p1 "$W/p1.candump" $M_KILL)

echo; echo "[4] C6(c) — can_guard 강제 종료(kill -9) → 0x210 즉시 중단 (실차에서는 여기서 차량 타임아웃 관측)"
start_fv; start_rec gkill
start_guard gkill; sleep 1.3
start_lcmd gkill; say "auto on"; sleep 0.8
M_GK=$(mark); kill -9 $G_PID; wait $G_PID 2>/dev/null; G_PID=""; sleep 0.6
stop_lcmd; stop_rec; stop_fv
report < <(analyze stops_after "$W/gkill.candump" $M_GK)
rm -f "/dev/shm/$CMD_SHM" "/dev/shm/$HB_SHM"   # kill -9 는 정리를 못 한다(다음 실행의 create() 도 알아서 지우지만 명시적으로)

echo; echo "[5] 실행 중 다른 0x210 송신자 등장 → can_guard 가 경고(멈추지는 않음)"
start_fv; start_rec frun
start_guard frun; sleep 1.3
start_foreign; sleep 1.2; stop_foreign
stop_guard; stop_rec; stop_fv
grep -q '다른 0x210 송신자 감지' "$W/frun.guard.log" && ok "로그: 다른 0x210 송신자 경고" || bad "다른 송신자 경고 없음"
[ "${G_RC:-1}" -eq 0 ] && ok "경고 후에도 정상 종료(코드 0)" || bad "종료 코드 ${G_RC:-?}"

echo; echo "[6] 정리 확인 — /dev/shm 에 이번 리허설 세그먼트가 남지 않음"
left=$(ls /dev/shm 2>/dev/null | grep -c "^$TAG" || true)
[ "$left" -eq 0 ] && ok "/dev/shm 정리됨" || bad "/dev/shm 에 $left 개 남음"

echo
echo "=================================================="
echo " 결과: PASS $PASS / FAIL $FAIL      (기록: $W)"
echo "=================================================="
[ $FAIL -eq 0 ]
