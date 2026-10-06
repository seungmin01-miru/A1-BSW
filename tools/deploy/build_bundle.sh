#!/usr/bin/env bash
# [우리 PC · 온라인] 대회 PC 용 오프라인 번들 a1_bundle/ 생성.
#   sudo bash build_bundle.sh            # ~/a1_bundle 에 생성 (A1_BUNDLE_OUT 로 변경)
#   A1_SKIP_ISO=1 sudo -E bash …         # ISO(4.7GB) 다운로드 생략
#   A1_DRYRUN=1 bash build_bundle.sh     # root 없이 의존성 계산만 시뮬레이션(-s) — 누락·충돌 점검용
# 핵심: apt 의 dpkg status 를 "빈 파일"로 주고 --download-only → 설치된 것이 없다고 보고 의존성 전체를 받는다.
#       그래서 새로 설치한 22.04.5(상황 A)든 기존 22.04(상황 B)든 번들만으로 해결된다.
#       root 가 필요한 이유: Ubuntu Pro(esm realtime) 인증(/etc/apt/auth.conf.d) 이 root 전용.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"
REPO_ROOT=$(cd "$HERE/../.." && pwd)
OUT=${A1_BUNDLE_OUT:-${SUDO_USER:+/home/$SUDO_USER}/a1_bundle}; OUT=${OUT:-$HOME/a1_bundle}
[[ $OUT == /a1_bundle ]] && OUT=$HOME/a1_bundle
DRY=${A1_DRYRUN:-0}
[[ $DRY == 1 ]] || need_root
W=${A1_WORK:-/tmp/a1_bundle_work}; mkdir -p "$W"/{state/lists/partial,cache/archives/partial}
: > "$W/status"

# ---- 고정 버전 (우리 PC 에서 8시간 검증된 조합) ----
RT_VER=6.8.1-1059.60~22.04.1
NV_VER=470.256.02-0ubuntu0.22.04.1
GEN_ABI=6.8.0-138

# 그룹별 패키지. 이름=버전 으로 고정한 것은 없으면 경고 후 이름만으로 재시도(최신).
GRP_BASE=(linux-image-$GEN_ABI-generic linux-headers-$GEN_ABI-generic linux-modules-extra-$GEN_ABI-generic
          linux-generic-hwe-22.04 dkms build-essential gcc-12 g++-12 make curl git ethtool mokutil dmidecode pciutils usbutils
          python3-pip can-utils rt-tests stress-ng glmark2 msr-tools trace-cmd linux-tools-common grub-efi-amd64-signed shim-signed)
# 메타패키지(linux-realtime-hwe-22.04)는 esm 에 최신(1060, 미검증)만 남아 있어 쓰지 않는다 → 검증된 1059 의 버전 명시 패키지를 직접 받는다.
RT_ABI=6.8.1-1059
GRP_RT=(linux-image-$RT_ABI-realtime=$RT_VER linux-modules-$RT_ABI-realtime=$RT_VER linux-modules-extra-$RT_ABI-realtime=$RT_VER
        linux-headers-$RT_ABI-realtime=$RT_VER linux-tools-$RT_ABI-realtime=$RT_VER
        linux-realtime-6.8-headers-$RT_ABI=$RT_VER linux-realtime-6.8-tools-$RT_ABI=$RT_VER)
GRP_NV=(nvidia-driver-470-server=$NV_VER nvidia-dkms-470-server=$NV_VER nvidia-utils-470-server=$NV_VER
        nvidia-compute-utils-470-server=$NV_VER xserver-xorg-video-nvidia-470-server=$NV_VER
        libnvidia-gl-470-server:i386 libnvidia-compute-470-server:i386 libnvidia-decode-470-server:i386
        libnvidia-encode-470-server:i386 libnvidia-fbc1-470-server:i386 libnvidia-ifr1-470-server:i386)
GRP_ROS=(ros-humble-desktop ros-humble-ros-base ros-dev-tools python3-colcon-common-extensions python3-rosdep
         python3-can python3-pytest ros-humble-ament-lint-auto ros-humble-ament-lint-common ros-humble-launch-testing-ament-cmake)
PIP_PKGS=(cantools==44.0.0 python-can==4.6.1 numpy==1.26.4 pytest==6.2.5 pytest-cov==3.0.0)

APT=(apt-get -o Debug::NoLocking=1 -o Dir::State="$W/state" -o Dir::State::status="$W/status"
     -o Dir::Cache="$W/cache" -o APT::Get::AllowUnauthenticated=0 -o Acquire::Languages=none)
[[ $DRY == 1 ]] && APT+=(-o Dir::Etc::SourceParts=/etc/apt/sources.list.d)   # 비root: esm(인증 필요)은 실패해도 진행

echo "== [1/7] apt 목록 갱신 (전용 상태 디렉터리) =="
"${APT[@]}" update 2>&1 | tail -5 || true

fetch_group() {   # $1 그룹명, 나머지 패키지들 — 버전 고정 실패 시 이름만으로 재시도
  local g=$1; shift; local pk plain args=()
  for pk in "$@"; do
    if [[ $DRY == 1 ]]; then
      "${APT[@]}" -s install -y --no-install-recommends "$pk" >/dev/null 2>&1 && args+=("$pk") || {
        if [[ $g == rt ]]; then warn "rt: $pk 해석 불가 (비root 에선 esm 인증이 없어 정상; sudo 로는 반드시 성공해야 함)"; continue; fi
        plain=${pk%%=*}; "${APT[@]}" -s install -y --no-install-recommends "$plain" >/dev/null 2>&1 \
          && { warn "$g: $pk 고정 불가 → $plain(최신)"; args+=("$plain"); } || warn "$g: $plain 없음(저장소 접근 불가?)"; }
    else
      args+=("$pk")
    fi
  done
  ((${#args[@]})) || { warn "$g: 대상 없음"; return 0; }
  if [[ $DRY == 1 ]]; then
    "${APT[@]}" -s install -y --no-install-recommends "${args[@]}" 2>&1 | grep -cE '^Inst' | sed "s/^/   $g: 설치될 패키지 수 = /"
    return 0
  fi
  local rc=0
  "${APT[@]}" install -y --download-only --no-install-recommends "${args[@]}" >"$W/last.log" 2>&1 || rc=$?
  tail -3 "$W/last.log"
  if ((rc)); then
    warn "$g: 버전 고정 묶음 실패 → 하나씩 재시도"
    for pk in "${args[@]}"; do
      "${APT[@]}" install -y --download-only --no-install-recommends "$pk" >/dev/null 2>&1 && continue
      [[ $g == rt ]] && die "rt: $pk 다운로드 실패 — 검증된 1059 커널을 못 받으면 번들 무효(최신 1060 으로 대체하지 않음)"
      "${APT[@]}" install -y --download-only --no-install-recommends "${pk%%=*}" >/dev/null 2>&1 || warn "$g: $pk 실패"
    done
  fi
}
echo "== [2/7] deb 의존성 전체 다운로드 =="
fetch_group base "${GRP_BASE[@]}"
fetch_group rt   "${GRP_RT[@]}"
fetch_group nvidia "${GRP_NV[@]}"
fetch_group ros  "${GRP_ROS[@]}"
[[ $DRY == 1 ]] && { echo "== DRYRUN 끝 (다운로드 없음) =="; exit 0; }

echo "== [3/7] 로컬 apt 저장소 구성 =="
mkdir -p "$OUT"/{repo/pool,wheels,src,iso,scripts,reference,results}
cp -n "$W"/cache/archives/*.deb "$OUT/repo/pool/" 2>/dev/null || true
(cd "$OUT/repo" && dpkg-scanpackages --multiversion pool /dev/null 2>/dev/null | gzip -9 > Packages.gz)
echo "   deb $(ls "$OUT"/repo/pool | wc -l)개, $(du -sh "$OUT/repo" | cut -f1)"
# 핵심 패키지가 저장소에 들어갔는지 확인
for k in linux-image-6.8.1-1059-realtime linux-modules-6.8.1-1059-realtime linux-headers-6.8.1-1059-realtime linux-image-$GEN_ABI-generic nvidia-driver-470-server nvidia-dkms-470-server ros-humble-desktop dkms gcc-12 rt-tests; do
  zcat "$OUT/repo/Packages.gz" | grep -q "^Package: $k$" && ok "포함: $k" || warn "누락: $k"
done

echo "== [4/7] pip wheel =="
sudo -u "${SUDO_USER:-$USER}" pip3 download -q -d "$OUT/wheels" "${PIP_PKGS[@]}" 2>&1 | tail -2 || warn "pip download 실패"
echo "   wheel $(ls "$OUT/wheels" | wc -l)개"

echo "== [5/7] 저장소 스냅샷(git bundle) =="
git -C "$REPO_ROOT" bundle create "$OUT/src/a1-bsw.bundle" --all 2>&1 | tail -1
git -C "$REPO_ROOT" rev-parse HEAD > "$OUT/src/HEAD.txt"
git -C "$REPO_ROOT" status --short | grep -v '^??' | grep . && warn "커밋 안 된 변경이 있음 — 번들에는 커밋된 것만 들어감" || true

echo "== [6/7] 스크립트·절차서·기준값 복사 =="
cp "$HERE"/*.sh "$HERE"/*.md "$OUT/scripts/" 2>/dev/null
mkdir -p "$OUT/scripts/rt"; cp "$REPO_ROOT"/tools/rt/*.sh "$REPO_ROOT"/tools/rt/*.py "$OUT/scripts/rt/"
cp "$HERE"/README.md "$HERE"/PLAN_A.md "$HERE"/PLAN_B.md "$OUT/" 2>/dev/null || true
cp -r "$HERE/reference/." "$OUT/reference/" 2>/dev/null || true
dpkg --get-selections > "$OUT/reference/dpkg_selections.txt"
cp /etc/grub.d/13_a1_bsw_rt_poll /etc/systemd/system/a1-bsw-rt-tune.service /etc/default/grub.d/99z-a1-bsw-safe-default.cfg "$OUT/reference/" 2>/dev/null || true
cat /proc/cmdline > "$OUT/reference/cmdline.txt"; dkms status > "$OUT/reference/dkms_status.txt" 2>&1 || true
uname -a > "$OUT/reference/uname.txt"; lscpu > "$OUT/reference/lscpu.txt"

echo "== [7/7] ISO + 체크섬 =="
if [[ ${A1_SKIP_ISO:-0} == 1 ]]; then warn "ISO 생략 (A1_SKIP_ISO=1) — 상황 A 대비용 ISO 는 따로 준비"
else
  ISO=ubuntu-22.04.5-desktop-amd64.iso
  if [[ ! -f $OUT/iso/$ISO ]]; then
    curl -fL --retry 3 -o "$OUT/iso/$ISO" "https://releases.ubuntu.com/22.04/$ISO" || warn "ISO 다운로드 실패"
  fi
  if [[ -f $OUT/iso/$ISO ]]; then
    exp=$(curl -fsL https://releases.ubuntu.com/22.04/SHA256SUMS | awk -v f="*$ISO" '$2==f{print $1}')
    act=$(sha256sum "$OUT/iso/$ISO" | cut -d' ' -f1)
    [[ -n $exp && $exp == "$act" ]] && { ok "ISO sha256 일치"; echo "$act  $ISO" > "$OUT/iso/$ISO.sha256"; } || warn "ISO sha256 불일치/확인 불가"
  fi
fi
(cd "$OUT" && find . -type f ! -name SHA256SUMS ! -path './results/*' -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
[[ -n ${SUDO_USER:-} ]] && chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$OUT"
echo "== 완료: $OUT ($(du -sh "$OUT" | cut -f1)) — 검증: cd $OUT && sha256sum -c SHA256SUMS =="
