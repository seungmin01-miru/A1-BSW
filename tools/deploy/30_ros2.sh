#!/usr/bin/env bash
# [대회 PC] ROS2 Humble + 워크스페이스 의존성 + pip wheel(cantools 44.0.0, python-can 4.6.1, numpy 1.26.4 …). 빠진 것만 설치.
#   sudo bash 30_ros2.sh
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"; need_root
[[ -x /usr/local/bin/a1-apt ]] || die "먼저 00_local_repo.sh"
echo "== [30] ROS2 Humble =="
export DEBIAN_FRONTEND=noninteractive
if [[ -d /opt/ros/noetic ]]; then warn "ROS1 Noetic 흔적 있음 — 22.04 이면 이상함(20.04 포맷 누락?). 그대로 진행은 하지만 확인 요망"; fi
a1-apt install -y ros-humble-desktop ros-humble-ros-base ros-dev-tools python3-colcon-common-extensions python3-rosdep \
  python3-can python3-pytest ros-humble-ament-lint-auto ros-humble-ament-lint-common 2>&1 | tail -3
[[ -f /opt/ros/humble/setup.bash ]] && ok "ROS2 Humble: /opt/ros/humble" || die "ros-humble 설치 실패"
echo "-- pip (오프라인 wheel)"
u=${SUDO_USER:-root}
for spec in cantools==44.0.0 python-can==4.6.1 numpy==1.26.4 pytest-cov==3.0.0; do
  n=${spec%%==*}; v=${spec##*==}
  have=$(sudo -u "$u" python3 -m pip show "$n" 2>/dev/null | sed -n 's/^Version: //p')
  if [[ $have == "$v" ]]; then ok "$n $v 이미 있음"
  else sudo -u "$u" python3 -m pip install -q --user --no-index --find-links /var/local/a1-wheels "$spec" && ok "$n $v 설치" || warn "$n $v 설치 실패"; fi
done
# 사용자 계정에 환경 자동 로드는 건드리지 않는다(.bashrc 미수정) — 절차서대로 source
mark_done 30
echo "== [30] 완료 → 다음: 40_rt.sh =="
