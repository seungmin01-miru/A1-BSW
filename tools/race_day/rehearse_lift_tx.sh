#!/usr/bin/env bash
# lift_tx.py 자동 리허설 — vcan0 에서 가짜 차량(sil/vcan/fake_a1_vehicle.py)을 상대로 전 기능을 확인하고
# 항목별 PASS/FAIL 을 출력한다. sudo 불필요(vcan0 이 이미 떠 있어야 함). 실차 버스(can0/can1)는 건드리지 않는다.
#
#   bash tools/race_day/rehearse_lift_tx.sh          # 약 1분
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
CH=vcan0
W=$(mktemp -d "${TMPDIR:-/tmp}/lift_rehearsal.XXXXXX")
PASS=0; FAIL=0
ok()   { echo "  ✅ PASS  $*"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ FAIL  $*"; FAIL=$((FAIL+1)); }
LTX() { python3 "$HERE/lift_tx.py" --channel $CH --precheck-s 1 "$@"; }
FV_PID=""; REC_PID=""; FS_PID=""
# lift_tx 를 fifo stdin 으로 백그라운드 실행 → LPID = python 자신의 PID (이름 검색으로 찾지 않는다)
start_ltx_bg() {   # $1 = 이름
  rm -f "$W/$1.fifo"; mkfifo "$W/$1.fifo"
  python3 "$HERE/lift_tx.py" --channel $CH --precheck-s 1 --log "$W/$1.log" < "$W/$1.fifo" > "$W/$1.out" 2>&1 &
  LPID=$!
  exec 3> "$W/$1.fifo"     # 쓰기 쪽을 열어 둬야 lift_tx 가 입력 끝(EOF)으로 종료하지 않는다
}
close_ltx_stdin() { exec 3>&- ; }
cleanup() { for p in $FV_PID $REC_PID $FS_PID; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup EXIT

ip link show $CH >/dev/null 2>&1 || { echo "vcan0 없음 — sudo bash $REPO/sil/vcan/vcan_up.sh 먼저"; exit 2; }
echo "작업 폴더: $W"

start_rec() { candump -L $CH > "$W/$1.candump" & REC_PID=$!; sleep 0.3; }
stop_rec()  { sleep 0.3; kill $REC_PID 2>/dev/null; wait $REC_PID 2>/dev/null; REC_PID=""; }
start_fv()  { python3 "$REPO/sil/vcan/fake_a1_vehicle.py" --channel $CH 2>/dev/null & FV_PID=$!; sleep 0.5; }
stop_fv()   { kill $FV_PID 2>/dev/null; wait $FV_PID 2>/dev/null; FV_PID=""; }
# 다른 송신자 흉내: steer=77(0x4d) auto=7 을 20 ms 로
start_foreign() { ( while :; do cansend $CH 210#4D00000000070000; sleep 0.02; done ) & FS_PID=$!; }
stop_foreign()  { kill $FS_PID 2>/dev/null; wait $FS_PID 2>/dev/null; FS_PID=""; }

# 분석기: candump 파일 → 조건 확인 (수정본 DBC 로 디코드)
analyze() { python3 - "$REPO/DBC/A1_dbc_fixed.dbc" "$@" <<'EOF'
import sys, re, statistics, cantools
db = cantools.database.load_file(sys.argv[1]); mode = sys.argv[2]; path = sys.argv[3]
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
if mode == 'session':
    dts = [b[0] - a[0] for a, b in zip(ours, ours[1:])]
    med = statistics.median(dts) * 1000 if dts else 0
    out(len(ours) > 100 and 19 <= med <= 21, f'0x210 {len(ours)}개, 주기 중앙값 {med:.2f} ms (기대 20 ms)')
    out(max(dts) * 1000 < 40 if dts else False, f'0x210 최대 간격 {max(dts)*1000 if dts else 0:.1f} ms (기대 40 ms 미만)')
    first = ours[0][1]
    out(all(first[k] == 0 for k in first), f'첫 프레임 auto 0·명령 0: {first}')
    last = ours[-1][1]
    out(all(last[k] == 0 for k in last), f'마지막 프레임 auto 0·명령 0(안전 종료): {last}')
    st = {d['steer_command'] for _, d in ours}; br = {d['break_command'] for _, d in ours}; ac = {d['acc_command'] for _, d in ours}
    out({10, -10} <= st and 30 in br and 5 in ac, f'보낸 값 등장: 조향 {sorted(st)}, 브레이크 {sorted(br)}, 가속 {sorted(ac)}')
    out(max(abs(x) for x in st) <= 30 and max(ac) <= 10 and max(br) <= 60, '한계 밖 값(조향 100, 가속 50) 송신 0건')
    both = [d for _, d in ours if d['acc_command'] > 0 and d['break_command'] > 0]
    out(not both, f'가속·브레이크 동시 송신 {len(both)}건')
    autos = [(d['steer_is_auto_command'], d['break_is_auto_command'], d['acc_is_auto_command']) for _, d in ours]
    out((1, 1, 1) in autos, 'auto on 이 실제로 송신됨')
    # 가속 자동 원위치: 가속 5 가 처음 나온 뒤 2초 + 여유 0.2초 안에 0 으로
    t5 = next(t for t, d in ours if d['acc_command'] == 5)
    t0 = next((t for t, d in ours if t > t5 and d['acc_command'] == 0), None)
    out(t0 is not None and t0 - t5 <= 2.3, f'가속 자동 원위치 {t0 - t5 if t0 else -1:.2f} s (기대 2초대)')
    # 가짜 차량 조향 위치가 명령 10° 를 같은 배율로 추종했는지(×0.1 인코딩 왕복 확인)
    t10 = next(t for t, d in ours if d['steer_command'] == 10)
    pos = [d['steer_postion'] for t, d in info if t10 + 0.6 < t < t10 + 0.9]
    out(bool(pos) and 8.5 <= max(pos) <= 10.5, f'가짜 차량 조향 위치가 10° 명령을 추종: {max(pos) if pos else None}')
elif mode == 'stops_after':
    t_kill = float(sys.argv[4])
    after = [t for t, _ in ours if t > t_kill + 0.05]
    out(not after, f'강제 종료 뒤 0x210 {len(after)}개 (기대 0 — 이후는 차량 자체 타임아웃의 몫)')
elif mode == 'no_ours':
    out(not ours, f'우리 0x210 {len(ours)}개 (기대 0), 다른 송신자 {len(foreign)}개')
elif mode == 'term_safe':
    last = ours[-1][1] if ours else None
    out(bool(ours) and all(last[k] == 0 for k in last), f'SIGTERM 뒤 마지막 프레임 auto 0·명령 0: {last}')
elif mode == 'foreign_mid':
    t_f = float(sys.argv[4])
    after = [t for t, _ in ours if t > t_f + 0.1]
    out(not after, f'다른 송신자 등장 후 우리 0x210 {len(after)}개 (기대 0 — 방해하지 않고 즉시 빠짐)')
EOF
}
report() { while IFS= read -r l; do case "$l" in OK*) ok "${l#OK }";; NG*) bad "${l#NG }";; esac; done; }

echo; echo "[1] 정상 세션 — 명령·한계·인터록·자동 원위치·안전 종료·20 ms 주기·배율"
start_fv; start_rec session
LTX --log "$W/session.log" > "$W/session.out" 2>&1 <<'EOS'
wait 0.5
auto on
wait 0.3
steer 10
wait 1.0
steer -10
wait 0.5
steer 100
acc 50
zero
brake 30
wait 0.5
acc 5
brake 0
acc 5
wait 2.6
status
q
EOS
rc=$?; stop_rec; stop_fv
[ $rc -eq 0 ] && ok "종료 코드 0" || bad "종료 코드 $rc"
grep -q '거부: steer 100' "$W/session.log" && grep -q '거부: acc 50' "$W/session.log" && ok "한계 밖 입력 거부 기록" || bad "한계 밖 입력 거부 기록 없음"
grep -q '거부: 브레이크가 걸린 동안' "$W/session.log" && ok "브레이크 중 가속 거부 기록" || bad "브레이크 중 가속 거부 기록 없음"
grep -q '조향 위치' "$W/session.log" && ok "status 에 차량 0x200 표시" || bad "status 에 차량 0x200 없음"
report < <(analyze session "$W/session.candump")

echo; echo "[2] 강제 종료(SIGKILL) — 송신이 즉시 멈춰야 함(C6 차량 타임아웃 관측의 전제)"
start_fv; start_rec kill
start_ltx_bg kill; sleep 2.0
T_KILL=$(date +%s.%N); kill -9 "$LPID"; wait "$LPID" 2>/dev/null; close_ltx_stdin
sleep 0.5; stop_rec; stop_fv
report < <(analyze stops_after "$W/kill.candump" "$T_KILL")

echo; echo "[3] 시작 전 다른 0x210 송신자 있음 → 시작 거부(종료 코드 3), 우리는 한 프레임도 안 보냄"
start_fv; start_foreign; sleep 0.3; start_rec foreign
LTX --log "$W/foreign.log" < /dev/null > "$W/foreign.out" 2>&1; rc=$?
stop_rec; stop_foreign; stop_fv
[ $rc -eq 3 ] && ok "종료 코드 3" || bad "종료 코드 $rc (기대 3)"
report < <(analyze no_ours "$W/foreign.candump")

echo; echo "[4] 차량 메시지 없음 → 시작 거부(종료 코드 4)"
start_rec novehicle
LTX --log "$W/novehicle.log" < /dev/null > "$W/novehicle.out" 2>&1; rc=$?
stop_rec
[ $rc -eq 4 ] && ok "종료 코드 4" || bad "종료 코드 $rc (기대 4)"
report < <(analyze no_ours "$W/novehicle.candump")

echo; echo "[5] SIGTERM → 안전 종료 프레임(auto 0, 명령 0) 송신 후 종료"
start_fv; start_rec term
start_ltx_bg term; printf 'auto on\nsteer 10\n' >&3; sleep 2.0
kill -TERM "$LPID"; wait "$LPID"; rc=$?; close_ltx_stdin; sleep 0.3
stop_rec; stop_fv
[ $rc -eq 0 ] && ok "SIGTERM 종료 코드 0" || bad "SIGTERM 종료 코드 $rc"
report < <(analyze term_safe "$W/term.candump")

echo; echo "[6] 실행 중 다른 0x210 송신자 등장 → 즉시 정지(종료 코드 6), 이후 우리 프레임 0"
start_fv; start_rec fmid
start_ltx_bg fmid; sleep 2.0
T_F=$(date +%s.%N); start_foreign
wait "$LPID"; rc=$?; close_ltx_stdin; sleep 0.5
stop_foreign; sleep 0.3; stop_rec; stop_fv
[ "$rc" = "6" ] && ok "종료 코드 6" || bad "종료 코드 $rc (기대 6)"
report < <(analyze foreign_mid "$W/fmid.candump" "$T_F")

echo
echo "=================================================="
echo " 결과: PASS $PASS / FAIL $FAIL      (기록: $W)"
echo "=================================================="
[ $FAIL -eq 0 ]
