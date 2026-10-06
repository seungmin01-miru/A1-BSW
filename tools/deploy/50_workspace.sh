#!/usr/bin/env bash
# [대회 PC · 일반 사용자로 실행, sudo 금지] 저장소 복원 + ROS2 워크스페이스 빌드/테스트 + can_guard 유닛테스트.
#   bash 50_workspace.sh [저장소를 둘 경로, 기본 ~/git/A1-BSW]
# 이미 저장소가 있으면 덮어쓰지 않고, 번들의 커밋이 더 새로운지만 알려준다(현장 변경분 보호).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"
[[ $EUID -ne 0 ]] || die "일반 사용자로 실행 (sudo 없이)"
B=$(find_bundle) || die "번들을 찾지 못함 — A1_BUNDLE=/경로"
DEST=${1:-$HOME/git/A1-BSW}
echo "== [50] 저장소 복원 → $DEST =="
if [[ -d $DEST/.git ]]; then
  warn "이미 저장소가 있음 — 덮어쓰지 않음. 번들 HEAD=$(cat "$B/src/HEAD.txt"), 현재 HEAD=$(git -C "$DEST" rev-parse HEAD)"
  git -C "$DEST" cat-file -e "$(cat "$B/src/HEAD.txt")" 2>/dev/null && ok "번들 커밋이 이미 포함됨" || warn "번들의 커밋이 없음 → git -C $DEST pull $B/src/a1-bsw.bundle 로 가져올 수 있음"
else
  mkdir -p "$(dirname "$DEST")"; git clone -q "$B/src/a1-bsw.bundle" "$DEST"; ok "복원: $(git -C "$DEST" log --oneline | head -1)"
  git -C "$DEST" checkout -q "$(cat "$B/src/HEAD.txt")" 2>/dev/null || true
fi
[[ -f /opt/ros/humble/setup.bash ]] || die "ROS2 Humble 없음 — 30_ros2.sh"
set +u; source /opt/ros/humble/setup.bash; set -u
cd "$DEST/ros2_ws"
echo "-- colcon build";  colcon build --symlink-install 2>&1 | tail -4
echo "-- colcon test";   colcon test 2>&1 | tail -4; colcon test-result --verbose 2>&1 | tail -3 || true
echo "-- can_guard 유닛테스트"; (cd "$DEST/safety/can_guard" && python3 -m pytest test/ -q 2>&1 | tail -3)
mark_done 50 2>/dev/null || true
echo "== [50] 완료 → sudo bash 90_validate.sh =="
