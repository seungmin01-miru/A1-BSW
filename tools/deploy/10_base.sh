#!/usr/bin/env bash
# [대회 PC] 6.8 HWE generic 커널 + 빌드 도구(dkms, gcc-12 …) + 측정 도구. 이미 있으면 건너뜀.
#   sudo bash 10_base.sh   → 끝나면 재부팅(새 generic 커널 적용). 재부팅 후 uname -r 이 6.8.0-*-generic 이어야 함.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"; need_root
[[ -x /usr/local/bin/a1-apt ]] || die "먼저 00_local_repo.sh"
echo "== [10] 기본 커널·도구 =="
export DEBIAN_FRONTEND=noninteractive
ABI=6.8.0-138
if a1-apt install -y "linux-image-$ABI-generic" "linux-headers-$ABI-generic" "linux-modules-extra-$ABI-generic" 2>&1 | tail -2; then :; else
  warn "$ABI 고정 설치 실패 → 번들의 linux-generic-hwe-22.04 사용"; a1-apt install -y linux-generic-hwe-22.04
fi
a1-apt install -y dkms build-essential gcc-12 g++-12 make git curl ethtool mokutil dmidecode pciutils usbutils python3-pip \
  can-utils rt-tests stress-ng glmark2 msr-tools trace-cmd linux-tools-common rsync 2>&1 | tail -2
gen=$(ls /boot | sed -nE 's/^vmlinuz-(6\.8\.[0-9]+-[0-9]+-generic)$/\1/p' | sort -V | tail -1)
[[ -n $gen ]] || die "6.8 generic 커널이 설치되지 않음"
ok "설치된 최신 6.8 generic: $gen"
update-grub 2>&1 | tail -1
mark_done 10
if [[ $(uname -r) == "$gen" ]]; then echo "== [10] 완료 (이미 $gen 으로 부팅 중) → 다음: 20_nvidia.sh =="
else echo "== [10] 완료 — 지금 커널 $(uname -r) → sudo reboot 후 $gen 으로 올라왔는지 확인하고 20_nvidia.sh =="; fi
