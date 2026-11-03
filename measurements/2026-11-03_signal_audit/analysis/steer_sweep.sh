#!/usr/bin/env bash
set -u
REPO=/home/ailab/git/A1-BSW; D=$REPO/measurements/2026-11-03_signal_audit/raw/l4h; N=$REPO/measurements/2026-11-03_signal_audit/raw/notes.txt
SP=/tmp/claude-1000/-home-ailab/d7f431f1-0fe2-4c06-9314-16afb4caa1c5/scratchpad
note() { echo "$(date +%s.%N) $(date +%T) $*" >> $N; echo "$(date +%T) $*"; }
lc() { echo "$*" > $D/lc.fifo; note "lift_cmd: $*"; }
abort() { note "⛔ 중단: $*"; [ -p $D/lc.fifo ] && echo "steer 0" > $D/lc.fifo; sleep 0.5; bash $REPO/tools/track_day/stop.sh >/dev/null 2>&1; kill $HOLD $LC $GUARDP 2>/dev/null; exit 1; }
mkdir -p $D; rm -f $D/lc.fifo; mkfifo $D/lc.fifo
note "재기동 bringup l4h (조향 한계 150)"
bash $REPO/tools/track_day/bringup.sh --kvaser 0 --no-adapter --no-rt --acc-limit 0 --out $D > $D/bringup_stdout.txt 2>&1 &
BR=$!
for i in $(seq 600); do grep -q "INIT — 안전 상태" $D/can_guard.log 2>/dev/null && break; grep -q "❌" $D/bringup_stdout.txt 2>/dev/null && { cat $D/bringup_stdout.txt; exit 1; }; sleep 0.05; done
grep -q "INIT — 안전 상태" $D/can_guard.log || { echo "can_guard INIT 안 보임"; cat $D/bringup_stdout.txt; exit 1; }
sleep 86400 > $D/lc.fifo & HOLD=$!
cd $REPO && python3 -u safety/can_guard/lift_cmd.py --steer-limit-deg 150 --brake-limit-pct 100 --acc-limit-pct 0 --brake-hold-s 3600 --log $D/lift_cmd.log < $D/lc.fifo > $D/lift_cmd_stdout.txt 2>&1 & LC=$!
sleep 0.4; lc "auto brake on"; lc "brake 100"; note "can_guard INIT → 브레이크 재적용"
wait $BR; tail -3 $D/bringup_stdout.txt | head -1
sleep 1; grep -q "송신 실패" $D/can_guard.log && abort "송신 실패"
python3 -u $SP/l4_motion_guard.py $D/lc.fifo 2>&1 | grep --line-buffered -E "🛑|⚠" > $D/motion_guard.txt & GUARDP=$!
sleep 1; lc "auto steer on"; sleep 1.5
for x in 10 -10 2 -2 5 -5 20 -20 30 -30 60 -60 90 -90 120 -120 150 -150; do
  lc "steer $x"; sleep 3; lc "steer 0"; sleep 2
  grep -q "송신 실패" $D/can_guard.log && abort "송신 실패"
  [ -s $D/motion_guard.txt ] && { cat $D/motion_guard.txt; abort "감시 경보"; }
done
note "조향 스윕 끝 — 브레이크 100 유지, auto 110"
tail -1 $D/can_guard.log
