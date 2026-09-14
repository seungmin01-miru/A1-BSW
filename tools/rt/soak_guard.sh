#!/usr/bin/env bash
# 장시간 soak 전후로 측정을 방해하는 것을 잠시 끄고(on), 끝나면 되돌린다(off).
#   bash tools/rt/soak_guard.sh on     # 사용자 권한: 화면잠금·유휴꺼짐 해제 / sudo: apt 타이머 정지
#   bash tools/rt/soak_guard.sh off    # 원복
# 바뀌는 것은 전부 '일시적'이다 — GNOME 설정은 이 파일에 원래 값을 저장했다가 되돌리고,
# apt 타이머는 stop 만 하므로(disable 아님) 재부팅하면 저절로 돌아온다.
set -euo pipefail
STATE=$HOME/.cache/a1_soak_guard.env
KEYS=("org.gnome.desktop.session idle-delay" "org.gnome.desktop.screensaver lock-enabled" "org.gnome.settings-daemon.plugins.power idle-dim")

case "${1:-}" in
on)
  : > "$STATE"
  for k in "${KEYS[@]}"; do echo "$k=$(gsettings get $k)" >> "$STATE"; done
  gsettings set org.gnome.desktop.session idle-delay 0
  gsettings set org.gnome.desktop.screensaver lock-enabled false
  gsettings set org.gnome.settings-daemon.plugins.power idle-dim false
  echo "  ✅ 화면 잠금·유휴 꺼짐·어둡게 해제 (원래 값 저장: $STATE)"
  if sudo -n true 2>/dev/null || [[ $EUID -eq 0 ]]; then S=""; else S="sudo"; fi
  $S systemctl stop apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service 2>/dev/null || true
  echo "  ✅ apt 자동 갱신·업그레이드 타이머 정지 (재부팅 시 자동 복귀)"
  systemctl list-timers --no-pager 2>/dev/null | grep -E 'apt-daily' || echo "  (apt 타이머 없음 — 정지 확인)"
  ;;
off)
  if [[ -f "$STATE" ]]; then
    while IFS='=' read -r k v; do [[ -n $k ]] && gsettings set $k "$v"; done < "$STATE"
    rm -f "$STATE"; echo "  ✅ GNOME 설정 원복"
  else echo "  (저장된 원래 값 없음 — GNOME 설정 변경 안 함)"; fi
  if sudo -n true 2>/dev/null || [[ $EUID -eq 0 ]]; then S=""; else S="sudo"; fi
  $S systemctl start apt-daily.timer apt-daily-upgrade.timer && echo "  ✅ apt 타이머 재시작"
  ;;
*) sed -n '2,6p' "$0"; exit 1 ;;
esac
