#!/usr/bin/env bash
# [대회 PC · 공통 첫 단계] 번들의 deb 저장소를 로컬 디스크에 복사하고 apt 소스로 등록 + 오프라인 apt 래퍼 설치.
#   sudo bash 00_local_repo.sh
# 이후 모든 설치 단계는 a1-apt(=번들 저장소만 보는 apt-get)를 쓴다 → 인터넷 불필요, 저장장치를 뽑아도 재부팅 후 계속 가능.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); source "$HERE/lib.sh"; need_root
B=$(find_bundle) || die "번들(a1_bundle/repo/Packages.gz)을 찾지 못함 — A1_BUNDLE=/경로 로 지정"
echo "== [00] 번들: $B =="
( cd "$B" && sha256sum -c SHA256SUMS --quiet 2>&1 | grep -v ': OK$' | head -5 ) && warn "위 파일 체크섬 이상(복사 오류?)" || ok "번들 체크섬 통과"
need=$(du -sk "$B/repo" "$B/wheels" | awk '{s+=$1} END{print int(s/1024)}'); avail=$(df -Pm /var/local | awk 'NR==2{print $4}')
((avail > need + 2048)) || die "디스크 여유 부족: 필요 ${need}MB+2GB, 여유 ${avail}MB"
mkdir -p /var/local/a1-repo /var/local/a1-wheels
rsync -a --delete "$B/repo/" /var/local/a1-repo/ 2>/dev/null || { rm -rf /var/local/a1-repo/*; cp -a "$B/repo/." /var/local/a1-repo/; }
cp -a "$B/wheels/." /var/local/a1-wheels/ 2>/dev/null || true
ok "복사: /var/local/a1-repo ($(ls /var/local/a1-repo/pool | wc -l) deb), /var/local/a1-wheels"
echo 'deb [trusted=yes] file:/var/local/a1-repo ./' > /etc/apt/sources.list.d/a1-bundle.list
cat > /usr/local/bin/a1-apt <<'WRAP'
#!/bin/sh
# 번들 저장소만 사용하는 apt-get (오프라인)
exec apt-get -o Dir::Etc::SourceList=/etc/apt/sources.list.d/a1-bundle.list -o Dir::Etc::SourceParts=/dev/null "$@"
WRAP
chmod 755 /usr/local/bin/a1-apt
dpkg --print-foreign-architectures | grep -qx i386 || { dpkg --add-architecture i386; ok "i386 아키텍처 추가"; }
a1-apt update -qq 2>&1 | tail -3
a1-apt -s install linux-image-realtime-hwe-22.04 >/dev/null 2>&1 && ok "번들 저장소 동작 확인(RT 커널 해석됨)" || warn "번들에서 RT 커널 패키지를 해석하지 못함"
# 우리 스크립트/rt 도구를 로컬 고정 위치에 복사 (USB 를 뽑아도 로그·백업이 남도록)
mkdir -p /opt/a1-bsw/tools; rsync -a "$B/scripts/rt/" /opt/a1-bsw/tools/rt/ 2>/dev/null || { mkdir -p /opt/a1-bsw/tools/rt; cp -a "$B/scripts/rt/." /opt/a1-bsw/tools/rt/; }
ok "/opt/a1-bsw/tools/rt 준비 (a1_rt.sh 실행 위치)"
mark_done 00
echo "== [00] 완료 → 다음: 상황에 따라 10(커널)/20(NVIDIA)/30(ROS2) =="
