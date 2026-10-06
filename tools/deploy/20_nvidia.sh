#!/usr/bin/env bash
# [대회 PC] NVIDIA 470.256.02-server 로 맞춘다 (RT 커널과 8시간 검증된 유일한 조합).
#   sudo bash 20_nvidia.sh         (generic 커널로 부팅한 상태에서)   → 재부팅 후 nvidia-smi 확인
# 이미 470.256.02-server 가 정상이면 아무것도 하지 않는다. 다른 버전은 purge 후 교체 — 되돌릴 수 없으니 survey 결과를 먼저 확인.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"; need_root
[[ -x /usr/local/bin/a1-apt ]] || die "먼저 00_local_repo.sh"
uname -r | grep -q generic || die "generic 커널로 부팅한 상태에서 실행 (지금 $(uname -r))"
echo "== [20] NVIDIA =="
cur=$(dpkg-query -W -f='${Version}' nvidia-driver-470-server 2>/dev/null || true)
if [[ $cur == 470.256.02-* ]] && nvidia-smi -L >/dev/null 2>&1; then
  ok "이미 470.256.02-server 정상 동작 — 변경 없음"; nvidia-smi --query-gpu=name,driver_version --format=csv,noheader; mark_done 20; exit 0
fi
export DEBIAN_FRONTEND=noninteractive
old=$(dpkg -l | awk '/^ii/ && ($2 ~ /^(nvidia-|libnvidia-|xserver-xorg-video-nvidia)/) && $2 !~ /470-server/ {print $2}')
if [[ -n $old ]]; then
  echo "   다른 NVIDIA 패키지 제거: $(echo $old | tr '\n' ' ')"
  [[ ${A1_YES:-0} == 1 ]] || { read -r -p "   제거하고 470-server 로 교체할까요? [y/N] " a; [[ $a == y ]] || die "취소"; }
  a1-apt purge -y $old 2>&1 | tail -2
fi
dpkg --print-foreign-architectures | grep -qx i386 || dpkg --add-architecture i386
a1-apt install -y nvidia-driver-470-server xserver-xorg-video-nvidia-470-server nvidia-utils-470-server \
  libnvidia-gl-470-server:i386 libnvidia-compute-470-server:i386 2>&1 | tail -4
dkms status
mark_done 20
echo "== [20] 완료 → sudo reboot 후 nvidia-smi 로 GPU 확인, 그 다음 30_ros2.sh =="
