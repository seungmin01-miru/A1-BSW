#!/usr/bin/env bash
# [대회 PC] 최종 검증 + 회수. 우리 PC 와 같은 기준(verify · bench · soak · can_guard SIL)으로 측정하고, 결과를 번들 저장장치에 모은다.
#   sudo bash 90_validate.sh [soak분, 기본 30]      (a1-bsw-rt-poll 로 부팅한 상태, 부하 없는 시각에)
#   A1_QUICK=1 sudo -E bash 90_validate.sh          bench 생략(soak 만)
# 판정 기준(우리 PC): soak 30분 최악 ≤ ~50µs·100µs 초과 0건이 이상적, 100µs 이상이면 BIOS(C-state/터보/PL1) 차이 조사.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"; need_root
SOAK_MIN=${1:-30}
RT=/opt/a1-bsw/tools/rt; [[ -f $RT/a1_rt.sh ]] || die "00_local_repo.sh 먼저"
grep -q isolcpus /proc/cmdline || die "격리 부팅이 아님 — a1-bsw-rt-poll 로 부팅 (40_rt.sh stage2 끝나고 재부팅)"
U=${SUDO_USER:-root}; REPO=$(eval echo "~$U")/git/A1-BSW; [[ -d $REPO ]] || REPO=${A1_REPO:-$REPO}
day=$(date +%Y-%m-%d_%H%M); host=$(hostname)
R=/opt/a1-bsw/results/${day}_competition_pc; mkdir -p "$R"
exec > >(tee -a "$R/90_validate.log") 2>&1
echo "== [90] 최종 검증 — $host $(date) — 커널 $(uname -r) =="

echo "-- 1) verify";  bash "$RT/a1_rt.sh" verify > "$R/verify.txt" 2>&1; grep -E '✅|⚠️|❌' "$R/verify.txt"
if [[ ${A1_QUICK:-0} != 1 ]]; then echo "-- 2) bench (~5분)"; bash "$RT/a1_rt.sh" bench > "$R/bench.txt" 2>&1; tail -15 "$R/bench.txt"; fi
echo "-- 3) soak ${SOAK_MIN}분";  bash "$RT/a1_rt.sh" soak "$SOAK_MIN" > "$R/soak.txt" 2>&1; tail -15 "$R/soak.txt"

echo "-- 4) can_guard 유닛테스트 + SIL(P-1/P-2)"
if [[ -d $REPO/safety/can_guard ]]; then
  ( modprobe vcan 2>/dev/null; bash "$REPO/sil/vcan/vcan_up.sh" vcan0 ) >/dev/null 2>&1 && ok "vcan0 up" || warn "vcan0 생성 실패(모듈 vcan?)"
  ( cd "$REPO/safety/can_guard"
    sudo -u "$U" python3 -m pytest test/ -q 2>&1 | tail -2 | tee "$R/can_guard_unit.txt"
    sudo -u "$U" python3 sil_tests/p1_kill_control_node.py --channel vcan0 2>&1 | tail -6 | tee "$R/sil_p1.txt"
    sudo -u "$U" python3 sil_tests/p2_kill_perception.py   --channel vcan0 2>&1 | tail -6 | tee "$R/sil_p2.txt" )
else warn "저장소 없음($REPO) — 50_workspace.sh 먼저"; fi

echo "-- 5) 회수 자료 수집"
{ echo "### 사후 스냅샷"; uname -a; cat /proc/cmdline; dkms status; nvidia-smi -L 2>&1; dpkg --get-selections; } > "$R/post_snapshot.txt" 2>&1
cp -a /opt/a1-bsw/tools/rt/logs "$R/rt_logs" 2>/dev/null || true
cp /var/lib/a1-bsw-deploy/*.done "$R/" 2>/dev/null || true
for f in "$(dirname "$0")"/survey_*.txt; do [[ -f $f ]] && cp "$f" "$R/"; done
[[ -d $REPO/.git ]] && { B0=$(cat "$(find_bundle 2>/dev/null)/src/HEAD.txt" 2>/dev/null || true)
  [[ -n $B0 ]] && git -C "$REPO" bundle create "$R/changes.bundle" "$B0"..HEAD 2>/dev/null && ok "현장 변경 커밋 → changes.bundle" || true
  git -C "$REPO" status --short > "$R/git_status.txt" 2>&1; git -C "$REPO" diff > "$R/git_diff.patch" 2>&1; }
OUTB=$(find_bundle 2>/dev/null) && { D="$OUTB/results/${host}_$(date +%Y%m%d_%H%M%S)"; mkdir -p "$D"; cp -a "$R/." "$D/"
  cp "$OUTB"/results/survey_*.txt "$D/" 2>/dev/null || true
  [[ -n ${SUDO_USER:-} ]] && chown -R "$SUDO_USER" "$D" 2>/dev/null || true; sync
  ok "회수 자료 → $D"; ls -1 "$D" | sed 's/^/     /'; } || warn "번들 저장장치를 못 찾음 — $R 을 직접 복사해 가져오세요"
echo "== [90] 끝 — 저장장치를 뽑기 전에 위 파일 목록을 확인하세요 =="
