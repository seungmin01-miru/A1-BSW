#!/usr/bin/env bash
# 대회 PC 현재 상태 조사 — 읽기 전용(설정 변경·설치 없음). sudo 없이도 동작, sudo 로 돌리면 BIOS 정보 추가.
# 결과: survey_<호스트>_<시각>.txt (번들이 있으면 results/ 에도 복사) + 마지막에 판정(A / B-ok / B-kernel / B-nvidia / B-secureboot)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"
ts=$(date +%Y%m%d_%H%M%S); host=$(hostname)
OUT_DIR=${A1_OUT:-$HERE}
BUNDLE=$(find_bundle 2>/dev/null) && OUT_DIR=$BUNDLE/results
mkdir -p "$OUT_DIR" 2>/dev/null || OUT_DIR=/tmp
OUT=$OUT_DIR/survey_${host}_$ts.txt
exec > >(tee "$OUT") 2>&1

sec() { echo; echo "## $*"; }
sec "기본"; date; hostname; uname -a
sec "OS"; cat /etc/os-release 2>/dev/null | grep -E '^(PRETTY_NAME|VERSION_ID)='
sec "ROS"; ls /opt/ros 2>/dev/null || echo "(없음)"; dpkg -l 'ros-noetic-desktop*' 'ros-humble-desktop*' 'ros-humble-ros-base' 2>/dev/null | awk '/^ii/{print $2,$3}'
sec "커널 (설치된 이미지 / 부팅 중)"; ls /boot 2>/dev/null | grep -E '^vmlinuz' ; echo "running: $(uname -r)"; cat /proc/cmdline
sec "NVIDIA"; dpkg -l 2>/dev/null | awk '/^ii/ && /nvidia/{print $2,$3}' | head -30; nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>&1 | head -3; dkms status 2>&1 | head
sec "Secure Boot"; mokutil --sb-state 2>&1 | head -2
sec "CPU/GPU/CAN/WiFi"; lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|Socket'; lspci 2>/dev/null | grep -iE 'vga|3d|nvidia|can|peak|network' ; lsusb 2>/dev/null | grep -iE 'realtek|802|wlan|wifi' ; ip -br link 2>/dev/null
sec "저장장치"; findmnt -no SOURCE,UUID,FSTYPE / ; df -h / /boot 2>/dev/null; lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT 2>/dev/null
sec "BIOS"; { dmidecode -s bios-version -s bios-release-date -s system-product-name 2>/dev/null || cat /sys/class/dmi/id/bios_version /sys/class/dmi/id/bios_date /sys/class/dmi/id/product_name 2>/dev/null; }
sec "GRUB"; grep -E '^GRUB_(DEFAULT|TIMEOUT|CMDLINE)' /etc/default/grub 2>/dev/null; ls /etc/default/grub.d /etc/grub.d 2>/dev/null | tr '\n' ' '; echo; grub-editenv list 2>/dev/null
sec "Python / 도구"; python3 --version; for t in cyclictest stress-ng glmark2 rdmsr hwlatdetect can-utils candump colcon git pip3 pro; do printf '%-12s %s\n' $t "$(command -v $t || echo -)"; done
sec "Ubuntu Pro"; pro status 2>&1 | head -12
sec "패키지 수"; echo "dpkg ii: $(dpkg -l | grep -c '^ii')"; echo "ros-humble-*: $(dpkg -l 'ros-humble-*' 2>/dev/null | grep -c '^ii')"
sec "네트워크"; { timeout 4 curl -sI https://esm.ubuntu.com >/dev/null 2>&1 && echo "인터넷: 가능"; } || echo "인터넷: 불가(또는 방화벽)"
sec "저장소 (a1-bsw)"; ls -d ~/git/A1-BSW ~/A1-BSW 2>/dev/null || echo "(없음)"

# ---- 판정 ----
sec "판정"
verdicts=()
. /etc/os-release 2>/dev/null
if [[ ${VERSION_ID:-} != 22.04 || -d /opt/ros/noetic ]]; then verdicts+=(A)
else
  gen=$(ls /boot 2>/dev/null | sed -nE 's/^vmlinuz-(6\.8\.[0-9]+-[0-9]+-generic)$/\1/p' | sort -V | tail -1)
  [[ -n $gen ]] || verdicts+=(B-kernel)
  nv=$(dpkg-query -W -f='${Version}' nvidia-driver-470-server 2>/dev/null)
  [[ $nv == 470.256.02-* ]] || verdicts+=(B-nvidia)
  mokutil --sb-state 2>/dev/null | grep -qi 'enabled' && verdicts+=(B-secureboot)
  [[ -d /opt/ros/humble ]] || verdicts+=(B-noros)
  ((${#verdicts[@]})) || verdicts+=(B-ok)
fi
cpu=$(lscpu | sed -nE 's/^Model name:\s+//p')
[[ $cpu == *12700* ]] || warn "CPU 가 i7-12700 이 아님($cpu) — TUNE_ISO=8-15 가정이 깨짐. 40_rt.sh 가 중단함"
echo "VERDICT: ${verdicts[*]}"
for v in "${verdicts[@]}"; do case $v in
  A) echo "  A: 포맷 필요 → PLAN_A.md";;
  B-ok) echo "  B-ok: 00 → 40 → 50 → 90 (PLAN_B.md)";;
  B-kernel) echo "  B-kernel: 6.8 generic 없음 → 10_base.sh 필요";;
  B-nvidia) echo "  B-nvidia: NVIDIA 470.256.02-server 아님/없음 → 20_nvidia.sh 필요";;
  B-secureboot) echo "  B-secureboot: BIOS 에서 Secure Boot 끄기(권장) 또는 MOK 등록 필요";;
  B-noros) echo "  B-noros: ROS2 Humble 없음 → 30_ros2.sh 필요";;
esac; done
# 우리 PC 기준과 비교 (reference/ 가 있으면)
REF=$HERE/reference/dpkg_selections.txt
[[ -f $REF ]] && { echo; echo "## 우리 PC 기준 대비 없는 패키지 수(참고): $(comm -13 <(dpkg --get-selections | awk '$2=="install"{print $1}' | sort) <(awk '$2=="install"{print $1}' "$REF" | sort) | wc -l)"; }
echo; echo "결과 파일: $OUT"
