#!/usr/bin/env bash
# 장시간 soak 전후로 측정을 방해하는 것을 잠시 끄고(on), 끝나면 되돌린다(off).
#   bash tools/rt/soak_guard.sh on        # 화면잠금·유휴꺼짐 해제 + apt 타이머 정지 (sudo 는 내부에서 필요할 때만)
#   bash tools/rt/soak_guard.sh on race   # ★ 대회 조건: apt 타이머 정지 + WiFi/WWAN 라디오 끔 + PackageKit·unattended-upgrades·백그라운드 타이머 정지
#                                         #   + 60초 뒤 화면 잠금·꺼짐(디스플레이 비활성 — 9/18 S2 로 스톨 0 조건 확인). snapd 는 건드리지 않음
#   bash tools/rt/soak_guard.sh check     # 지금 상태 표 (soak 시작 전 확인용)
#   bash tools/rt/soak_guard.sh off       # 원복 (라디오·타이머·서비스·GNOME 설정)
# 바뀌는 것은 전부 '일시적'이다 — GNOME 설정은 원래 값을 저장했다가 되돌리고, 서비스·타이머는 stop 만 하므로(disable 아님)
# 재부팅하면 저절로 돌아온다. `sudo bash …` 로 실행해도 GNOME 설정은 로그인 사용자 것을 바꾼다.
set -uo pipefail

# --- 실행 주체 정리: gsettings 는 로그인 사용자 세션에서, systemctl/nmcli 는 root 로 ---
if [[ $EUID -eq 0 ]]; then
  U=${SUDO_USER:-$(logname 2>/dev/null || echo ailab)}; S=""
else
  U=$USER; S="sudo"
fi
UID_U=$(id -u "$U"); HOME_U=$(getent passwd "$U" | cut -d: -f6)
STATE=$HOME_U/.cache/a1_soak_guard.env
gs() { if [[ $EUID -eq 0 ]]; then sudo -u "$U" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_U/bus" gsettings "$@"; else gsettings "$@"; fi; }
KEYS=("org.gnome.desktop.session idle-delay" "org.gnome.desktop.screensaver lock-enabled" "org.gnome.settings-daemon.plugins.power idle-dim")

APT_UNITS=(apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service)
# snapd.service/socket 은 정지하지 않는다 — 정지하면 snapd-desktop-integration 이 1 Hz 오류 루프에 빠진다(9/17 밤 확인)
RACE_UNITS=(packagekit.service unattended-upgrades.service
            fwupd-refresh.timer motd-news.timer ua-timer.timer man-db.timer logrotate.timer fstrim.timer update-notifier-motd.timer)

case "${1:-}" in
on)
  race=0; [[ "${2:-}" == race ]] && race=1
  : > "$STATE"
  for k in "${KEYS[@]}"; do echo "gs $k=$(gs get $k)" >> "$STATE"; done
  if ((race)); then
    # 대회 조건 = 디스플레이 꺼짐(9/18 S2: 화면 잠금+DPMS 만으로 스톨 0). 60초 뒤 자동 잠금 → 화면 꺼짐. glmark2 오프스크린 부하는 계속 돈다.
    gs set org.gnome.desktop.session idle-delay 60
    gs set org.gnome.desktop.screensaver lock-enabled true
    gs set org.gnome.settings-daemon.plugins.power idle-dim false
    echo "  ✅ [$U] 60초 유휴 후 화면 잠금·꺼짐 (대회 조건: 디스플레이 비활성)"
  else
    gs set org.gnome.desktop.session idle-delay 0
    gs set org.gnome.desktop.screensaver lock-enabled false
    gs set org.gnome.settings-daemon.plugins.power idle-dim false
    echo "  ✅ [$U] 화면 잠금·유휴 꺼짐·어둡게 해제 (원래 값 저장: $STATE)"
  fi
  $S systemctl stop "${APT_UNITS[@]}" 2>/dev/null || true
  echo "  ✅ apt 자동 갱신·업그레이드 타이머 정지"
  if ((race)); then
    echo "wifi=$(nmcli -t radio wifi 2>/dev/null || echo unknown)" >> "$STATE"
    echo "wwan=$(nmcli -t radio wwan 2>/dev/null || echo unknown)" >> "$STATE"
    $S nmcli radio wifi off 2>/dev/null && $S nmcli radio wwan off 2>/dev/null && echo "  ✅ WiFi·WWAN 라디오 끔 (nmcli)"
    $S rfkill block wifi wwan 2>/dev/null || true
    $S systemctl stop "${RACE_UNITS[@]}" 2>/dev/null || true
    echo "race=1" >> "$STATE"
    echo "  ✅ 대회 조건: PackageKit·snapd·unattended-upgrades 정지, 백그라운드 타이머 ${#RACE_UNITS[@]}개 정지 (재부팅 시 자동 복귀)"
  fi
  bash "$0" check
  ;;
check)
  echo "  라디오: wifi=$(nmcli -t radio wifi 2>/dev/null) wwan=$(nmcli -t radio wwan 2>/dev/null)  rfkill: $(rfkill list 2>/dev/null | grep -c 'Soft blocked: yes')개 차단"
  printf "  %-32s %s\n" "유닛" "상태"
  for u in "${APT_UNITS[@]}" "${RACE_UNITS[@]}"; do printf "  %-32s %s\n" "$u" "$(systemctl is-active "$u" 2>/dev/null)"; done | sed 's/ active$/ active   ← 켜져 있음/'
  echo "  GNOME: idle-delay=$(gs get org.gnome.desktop.session idle-delay 2>/dev/null) lock=$(gs get org.gnome.desktop.screensaver lock-enabled 2>/dev/null)"
  ;;
off)
  if [[ -f "$STATE" ]]; then
    while IFS='=' read -r k v; do
      case "$k" in
        "gs "*) gs set ${k#gs } "$v" ;;
        wifi) [[ $v == enabled ]] && { $S nmcli radio wifi on; $S rfkill unblock wifi; echo "  ✅ WiFi 라디오 복귀"; } ;;
        wwan) [[ $v == enabled ]] && { $S nmcli radio wwan on; $S rfkill unblock wwan; echo "  ✅ WWAN 라디오 복귀"; } ;;
        race) $S systemctl start "${RACE_UNITS[@]}" 2>/dev/null || true; echo "  ✅ PackageKit·snapd·타이머 재시작" ;;
      esac
    done < "$STATE"
    rm -f "$STATE"; echo "  ✅ GNOME 설정 원복"
  else echo "  (저장된 원래 값 없음 — GNOME·라디오 변경 안 함)"; fi
  $S systemctl start apt-daily.timer apt-daily-upgrade.timer 2>/dev/null && echo "  ✅ apt 타이머 재시작"
  bash "$0" check
  ;;
*) sed -n '2,9p' "$0"; exit 1 ;;
esac
