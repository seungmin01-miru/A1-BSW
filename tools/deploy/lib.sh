# A1-BSW 대회 PC 배포 공용 함수 — 각 스크립트가 source 한다.
ok()   { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }
die()  { echo "  ❌ 중단: $*"; exit 1; }
need_root() { [[ $EUID -eq 0 ]] || die "sudo 로 실행해야 합니다: sudo bash $0 $*"; }

# 번들 위치 탐지: A1_BUNDLE 환경변수 > 스크립트 상위(a1_bundle/scripts/..) > 저장소 옆 ~/a1_bundle
find_bundle() {
  local d here; here=$(cd "$(dirname "${BASH_SOURCE[1]:-$0}")" && pwd)
  for d in "${A1_BUNDLE:-}" "$here/.." "$HOME/a1_bundle" ${SUDO_USER:+/home/$SUDO_USER/a1_bundle} /media/*/a1_bundle /media/*/*/a1_bundle /mnt/*/a1_bundle /mnt/a1_bundle; do
    [[ -n $d && -f $d/repo/Packages.gz ]] && { (cd "$d" && pwd); return 0; }
  done
  return 1
}
# 저장소(tools/rt 를 가진 곳) 위치: 번들 안 scripts/ 는 tools/rt 사본을 함께 가진다
find_rt_dir() {
  local here d; here=$(cd "$(dirname "${BASH_SOURCE[1]:-$0}")" && pwd)
  for d in "$here/../rt" "$here/rt" "${A1_REPO:-/nonexistent}/tools/rt" "$HOME/git/A1-BSW/tools/rt" "$HOME/A1-BSW/tools/rt"; do
    [[ -f $d/a1_rt.sh ]] && { (cd "$d" && pwd); return 0; }
  done
  return 1
}
# 단계 진행 표식 — 재부팅 후 어디까지 했는지 확인용
STATE_DIR=/var/lib/a1-bsw-deploy
mark_done() { mkdir -p "$STATE_DIR" 2>/dev/null && echo "$(date '+%F %T')" > "$STATE_DIR/$1.done"; }
is_done()   { [[ -f $STATE_DIR/$1.done ]]; }
