#!/usr/bin/env bash
# [대회 PC] RT 커널 이행 — tools/rt/a1_rt.sh 를 오프라인 모드로 감싼 단계 실행기.
#   sudo bash 40_rt.sh stage1     pin → install → arm(다음 1회만 RT 부팅 예약)   → sudo reboot
#   sudo bash 40_rt.sh stage2     (RT 로 부팅한 뒤) verify → tune rt-poll → finalize → sudo reboot
#   sudo bash 40_rt.sh stage3     (운용 항목 a1-bsw-rt-poll 로 부팅한 뒤) verify — 격리·유닛·GPU·WiFi·CAN 최종 확인
#   sudo bash 40_rt.sh undo       finalize undo (기본 부팅을 generic 으로) — 롤백은 a1_rt.sh rollback
# 로그: /opt/a1-bsw/tools/rt/logs/ (90_validate.sh 가 회수)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"; need_root
RT=/opt/a1-bsw/tools/rt; [[ -f $RT/a1_rt.sh ]] || die "먼저 00_local_repo.sh (/opt/a1-bsw/tools/rt 준비)"
export A1_OFFLINE=1
cpu=$(lscpu | sed -nE 's/^Model name:\s+//p')
[[ $cpu == *12700* ]] || die "CPU 가 i7-12700 이 아님($cpu) — TUNE_ISO=8-15 는 12700(P코어 8개+HT, E코어 4개) 전제. lscpu 를 보고 a1_rt.sh 의 TUNE_ISO/TUNE_HOUSE 를 조정한 뒤 진행"
mokutil --sb-state 2>/dev/null | grep -qi enabled && die "Secure Boot 켜짐 — 서명 안 된 NVIDIA 모듈이 로드되지 않음. BIOS 에서 끄거나 MOK 등록 후 재실행"
run() { bash "$RT/a1_rt.sh" "$@"; }
case "${1:-}" in
  stage1)
    echo "== [40 stage1] pin → install → arm =="
    run pin; run install; run arm
    mark_done 40-stage1
    echo "== 완료 → sudo reboot. 재부팅 후 uname -r 이 6.8.1-…-realtime 이어야 함. 그 다음 sudo bash 40_rt.sh stage2 ==";;
  stage2)
    [[ $(cat /sys/kernel/realtime 2>/dev/null) == 1 ]] || die "RT 커널로 부팅한 상태가 아님 (uname -r = $(uname -r)). stage1 후 재부팅했는지, arm 이 적용됐는지 확인"
    echo "== [40 stage2] verify → tune rt-poll → finalize =="
    run verify || true
    nvidia-smi -L >/dev/null 2>&1 || die "RT 부팅에서 GPU 없음 — dkms status / make.log 확인. finalize 하지 않음 (기본 부팅이 바뀌지 않아 generic 으로 안전 복귀)"
    run tune rt-poll; run finalize
    mark_done 40-stage2
    echo "== 완료 → sudo reboot (a1-bsw-rt-poll 로 올라옴). 그 다음 sudo bash 40_rt.sh stage3 ==";;
  stage3)
    grep -q isolcpus /proc/cmdline || die "격리 부팅(a1-bsw-rt-poll)이 아님 — cmdline: $(cat /proc/cmdline)"
    run verify; mark_done 40-stage3
    echo "== 완료 → 50_workspace.sh (일반 사용자로), 그 다음 90_validate.sh ==";;
  undo) run finalize undo;;
  *) sed -n '2,10p' "$0"; exit 1;;
esac
