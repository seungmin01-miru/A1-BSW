#!/usr/bin/env bash
# Kvaser linuxcan(드라이버) + CANlib SDK + Python canlib 설치 — 이 PC(6.8.1-1059-realtime) 전용 안전판.
#
#   sudo bash tools/kvaser/install_kvaser.sh          # 설치 (약 3분)
#   sudo bash tools/kvaser/install_kvaser.sh check    # 설치 상태만 확인 (아무것도 안 바꿈)
#   sudo bash tools/kvaser/install_kvaser.sh uninstall
#
# 근거: tools/race_day/Practice_04_CAN.pdf (Chapter 1). 실습 자료(Jetson Orin, Ubuntu 20.04)와 다른 점:
#   ✗ `sudo apt remove dkms` 는 **하지 않는다** — 이 PC 는 NVIDIA 470 이 nvidia-dkms-470-server 로 dkms 에 의존한다.
#     지우면 RT 커널의 GPU 드라이버가 같이 제거된다(2026-10-07 apt-cache rdepends 로 확인).
#   ✗ `apt-get install linux-headers-generic` 도 하지 않는다 — 필요한 건 지금 커널의 헤더
#     (linux-headers-6.8.1-1059-realtime, 이미 설치됨)뿐이고, generic 메타패키지는 다른 커널 헤더를 끌어온다.
#   ✓ 왜 linuxcan 이 필요한가: Kvaser Leaf v3(USB PID 0x0117)는 이 커널(6.8)의 SocketCAN 드라이버 kvaser_usb
#     지원 목록에 없다(modinfo 확인) → 커널 내장 드라이버로는 can 인터페이스가 안 생긴다.
#   ⚠ linuxcan 설치는 SocketCAN kvaser_usb / kvaser_pciefd / kvaser_pci 를 blacklist 한다(linuxcan README) —
#     이 PC 의 PEAK 카드(peak_pciefd)와는 무관하다. 대신 Kvaser 장치는 can0 같은 인터페이스가 아니라 CANlib
#     채널(0, 1, …)로 보인다 → 우리 도구는 python-can 의 kvaser 백엔드로 쓴다(tools/kvaser/README.md).
#   ⚠ 모듈은 **지금 커널용으로만** 설치된다. 커널을 바꾸면(generic 부팅 등) 이 스크립트를 다시 돌린다.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${KVASER_WORK:-/var/tmp/a1_kvaser_build}
USER_NAME=${SUDO_USER:-ailab}
URL_LINUXCAN="https://www.kvaser.com/downloads-kvaser/?utm_source=software&utm_ean=7330130980754&utm_status=latest"
URL_SDK="https://www.kvaser.com/downloads-kvaser/?utm_source=software&utm_ean=7330130981966&utm_status=latest"
MODE=${1:-install}

say() { echo "[kvaser] $*"; }
as_user() { if [ "$(id -u)" -eq 0 ]; then sudo -u "$USER_NAME" "$@"; else "$@"; fi; }
need_root() { [ "$(id -u)" -eq 0 ] || { echo "sudo 로 실행하세요: sudo bash $0 $MODE"; exit 1; }; }

check() {
  say "커널: $(uname -r)"
  say "dkms 에 의존하는 패키지(지우면 안 됨): $(apt-cache rdepends --installed dkms 2>/dev/null | tail -n +3 | sort -u | tr -d ' ' | tr '\n' ' ')"
  for m in mhydra leaf kvcommon kvvirtualcan; do
    if modinfo "$m" >/dev/null 2>&1; then say "모듈 $m: 설치됨 ($(modinfo -F vermagic "$m" | cut -d' ' -f1))"; else say "모듈 $m: 없음"; fi
  done
  say "로드된 Kvaser 모듈: $(lsmod | awk '/^(mhydra|leaf|kvcommon|kvvirtualcan)/{print $1}' | tr '\n' ' ')"
  say "blacklist: $(ls /etc/modprobe.d/ 2>/dev/null | grep -i kvaser | tr '\n' ' ')"
  ldconfig -p | grep -q libcanlib.so && say "libcanlib: 설치됨" || say "libcanlib: 없음"
  as_user python3 -c "from canlib import canlib; print('[kvaser] python canlib:', canlib.prodversion(), '/ 채널 수', canlib.getNumberOfChannels())" 2>&1 | tail -1 || true
  if lsusb | grep -qi -E "0bfd|kvaser"; then lsusb | grep -i -E "0bfd|kvaser" | sed 's/^/[kvaser] USB: /'; else say "USB: 연결된 Kvaser 장치 없음"; fi
  [ -x /usr/doc/canlib/examples/listChannels ] && /usr/doc/canlib/examples/listChannels 2>&1 | sed 's/^/[kvaser] listChannels: /' | head -8 || true
}

case "$MODE" in
  check) check; exit 0 ;;
  uninstall)
    need_root
    read -r -p "[kvaser] 설치된 Kvaser 드라이버·라이브러리·canlib 을 전부 지웁니다. 계속하려면 yes 입력: " ans
    [ "$ans" = "yes" ] || { say "취소"; exit 0; }
    [ -d "$WORK/kvlibsdk" ] && make -C "$WORK/kvlibsdk" uninstall || true
    [ -d "$WORK/linuxcan" ] && make -C "$WORK/linuxcan" uninstall || true   # blacklist 도 되돌린다(config.mak)
    depmod -a "$(uname -r)"; ldconfig
    as_user python3 -m pip uninstall -y canlib || true
    say "제거 완료"; exit 0 ;;
  install) need_root ;;
  *) echo "사용법: sudo bash $0 [install|check|uninstall]"; exit 1 ;;
esac

[ -d "/lib/modules/$(uname -r)/build" ] || { say "지금 커널($(uname -r))의 헤더가 없습니다 — 중단(헤더 패키지는 이 스크립트가 설치하지 않음)"; exit 1; }
command -v make >/dev/null && command -v gcc >/dev/null || { say "build-essential 이 없습니다: sudo apt-get install build-essential pkg-config"; exit 1; }

mkdir -p "$WORK"; cd "$WORK"
for pair in "linuxcan|$URL_LINUXCAN" "kvlibsdk|$URL_SDK"; do
  name=${pair%%|*}; url=${pair#*|}
  if [ ! -f "$name.tar.gz" ]; then say "$name 내려받기"; curl -fsSL -o "$name.tar.gz" "$url"; fi
  rm -rf "$name"; tar -xzf "$name.tar.gz" 2>/dev/null
done

say "linuxcan 빌드 (드라이버 + libcanlib)"
make -C linuxcan -j"$(nproc)" > linuxcan_build.log 2>&1 || { tail -20 linuxcan_build.log; exit 1; }
say "linuxcan 설치 (모듈·libcanlib·udev 규칙, SocketCAN kvaser_* blacklist)"
make -C linuxcan install > linuxcan_install.log 2>&1 || { tail -20 linuxcan_install.log; exit 1; }
say "kvlibsdk 빌드·설치"
make -C kvlibsdk -j"$(nproc)" > sdk_build.log 2>&1 || { tail -20 sdk_build.log; exit 1; }
make -C kvlibsdk install > sdk_install.log 2>&1 || { tail -20 sdk_install.log; exit 1; }
ldconfig

# linuxcan 의 modules_install 은 빌드 디렉터리에 System.map 이 없으면 depmod 를 건너뛴다(2026-10-07 실제로 발생 —
# 모듈 파일은 extra/kvaser/ 에 들어갔지만 modprobe 가 못 찾음). 그래서 여기서 직접 돌린다.
say "모듈 목록 갱신 (depmod)"
if [ -f "/boot/System.map-$(uname -r)" ]; then depmod -a -F "/boot/System.map-$(uname -r)" "$(uname -r)"; else depmod -a "$(uname -r)"; fi
ls "/lib/modules/$(uname -r)/extra/kvaser/"mhydra.ko* >/dev/null 2>&1 || { say "🛑 mhydra.ko 가 설치 위치에 없음 — linuxcan_install.log 확인"; exit 1; }
say "모듈 로드 (Leaf v3 = mhydra, 가상 채널 = kvvirtualcan)"
for m in kvcommon mhydra kvvirtualcan; do
  modprobe "$m" || { say "🛑 modprobe $m 실패 — dmesg | tail 확인"; exit 1; }
done
modprobe leaf 2>/dev/null || true    # 구형 Leaf(v2 이하)용 — 없어도 Leaf v3 에는 지장 없음
say "Python canlib 설치 (사용자 $USER_NAME, --user)"
as_user python3 -m pip install --user --quiet canlib

say "===== 설치 확인 ====="
check
ldconfig -p | grep -q libcanlib.so || { say "🛑 libcanlib 가 등록되지 않음 — linuxcan_install.log 확인"; exit 1; }
as_user python3 -c "from canlib import canlib; canlib.getNumberOfChannels()" >/dev/null 2>&1 \
  || { say "🛑 Python canlib 동작 안 함"; exit 1; }
say "✅ 완료. Kvaser 장치를 USB 에 꽂으면 listChannels 에 실제 채널이 보인다(가상 채널 0·1 은 리허설용)."
say "   다음: sudo ip link add dev kv0 type vcan && sudo ip link set kv0 up  →  bash tools/kvaser/rehearse_kvaser.sh"
say "   ⚠ 확인 후 uninstall 을 실행하지 말 것 — 방금 설치한 것을 전부 지운다."
