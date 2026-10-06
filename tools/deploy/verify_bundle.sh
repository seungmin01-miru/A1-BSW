#!/usr/bin/env bash
# [우리 PC · 출발 전, root 불필요] 번들 완결성 검사: "설치된 것 없음 + 번들 저장소만" 조건으로 모든 대상 패키지 설치를 시뮬레이션.
# 의존성 누락이 있으면 여기서 E: 로 드러난다. (상황 A 의 새로 깐 22.04.5 를 가장 엄격하게 흉내낸 것)
#   bash verify_bundle.sh [~/a1_bundle]
set -uo pipefail
B=${1:-$HOME/a1_bundle}; [[ -f $B/repo/Packages.gz ]] || { echo "❌ $B/repo/Packages.gz 없음"; exit 1; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT; mkdir -p "$W"/state/lists/partial "$W"/cache/archives/partial; : > "$W/status"
echo "deb [trusted=yes] file:$B/repo ./" > "$W/src.list"
APT=(apt-get -o Debug::NoLocking=1 -o Dir::State="$W/state" -o Dir::State::status="$W/status" -o Dir::Cache="$W/cache" \
     -o Dir::Etc::SourceList="$W/src.list" -o Dir::Etc::SourceParts=/dev/null -o APT::Architecture=amd64)
"${APT[@]}" update -qq 2>&1 | tail -2
# 상황 A 는 i386 도 필요(NVIDIA 32비트 라이브러리)
fail=0
chk() { local out; out=$("${APT[@]}" -s install -y --no-install-recommends "$@" 2>&1); if grep -qE '^E:' <<<"$out"; then echo "❌ $*"; grep -E '^E:|^ +Depends|unmet' <<<"$out" | head -6; fail=1; else echo "✅ $* → $(grep -c '^Inst' <<<"$out")개"; fi; }
chk linux-image-6.8.1-1059-realtime linux-modules-6.8.1-1059-realtime linux-modules-extra-6.8.1-1059-realtime linux-headers-6.8.1-1059-realtime linux-tools-6.8.1-1059-realtime linux-realtime-6.8-headers-6.8.1-1059 linux-realtime-6.8-tools-6.8.1-1059
chk linux-image-6.8.0-138-generic linux-headers-6.8.0-138-generic linux-modules-extra-6.8.0-138-generic
chk dkms build-essential gcc-12 g++-12 rt-tests stress-ng glmark2 msr-tools trace-cmd can-utils git python3-pip mokutil dmidecode rsync
chk nvidia-driver-470-server nvidia-dkms-470-server nvidia-utils-470-server xserver-xorg-video-nvidia-470-server
chk ros-humble-desktop ros-dev-tools python3-colcon-common-extensions python3-can python3-pytest ros-humble-ament-lint-auto ros-humble-ament-lint-common python3-rosdep
# i386 (nvidia 32bit libs) — 아키텍처를 추가해야 해석됨
if dpkg --print-foreign-architectures | grep -qx i386; then chk libnvidia-gl-470-server:i386 libnvidia-compute-470-server:i386; else echo "ℹ️  i386 미활성 이 PC — 00_local_repo.sh 가 대회 PC 에서 활성화하므로 여기선 생략"; fi
# wheel
ls "$B"/wheels/*.whl >/dev/null 2>&1 && python3 -m pip install -q --dry-run --no-index --find-links "$B/wheels" cantools==44.0.0 python-can==4.6.1 numpy==1.26.4 pytest-cov==3.0.0 >/dev/null 2>&1 && echo "✅ pip wheel 오프라인 해석" || { echo "❌ pip wheel 해석 실패"; fail=1; }
(cd "$B" && sha256sum -c SHA256SUMS --quiet 2>&1 | head -3) && echo "✅ SHA256SUMS" || { echo "❌ 체크섬"; fail=1; }
((fail)) && { echo "== 번들 불완전 =="; exit 1; } || echo "== 번들 완결성 OK =="
