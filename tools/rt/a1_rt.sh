#!/usr/bin/env bash
# A1-BSW 메인 PC — RT 커널 이행 (안 B′: Ubuntu Pro linux-realtime-hwe-22.04 = 6.8-rt)
# 원칙: 현재 동작 환경(6.8.0-138-generic + NVIDIA 470 + WiFi)은 건드리지 않는다.
#
#   sudo bash a1_rt.sh pin       1) 부팅 때마다 커널 선택 메뉴(10초) 표시, 기본값은 6.8 generic (설정 백업 포함)
#   sudo bash a1_rt.sh install   2) 6.8-rt 커널 "추가" 설치 — 기존 패키지 변경이 0건일 때만 진행
#                                3) 재부팅 → 메뉴에서 6.8.1-...-realtime 선택
#        bash a1_rt.sh verify    4) RT로 부팅한 상태에서 실행 — 커널/GPU/WiFi/CAN/커널경고 점검
#   sudo bash a1_rt.sh hwlat     5) 하드웨어 지연(BIOS SMI) 점검만 60초 — 재부팅 불필요
#   sudo bash a1_rt.sh tune      6) "RT + 튜닝"(isolcpus 등) 부팅 항목을 메뉴에 추가 — 기본 부팅은 안 바꿈
#   sudo bash a1_rt.sh bench     7) §5.A cyclictest 실측 — 무부하 / CPU부하 / CPU+GPU부하 + 하드웨어 지연 (약 5분)
#                                   격리 코어가 있으면 그 코어에서 측정, 없으면 CPU 0-7(튜닝 전 바닥값)
#   sudo bash a1_rt.sh soak [분] 8) 장시간(기본 30분) 부하 측정 — 드문 스파이크 확인, 대회 전 필수
#   sudo bash a1_rt.sh trace [분] [µs] 9) 지연 원인 추적 — 임계 초과 순간의 인터럽트·IPI·태스크 기록
#   sudo bash a1_rt.sh drive          10) 주행 프로필 — 콜드 부팅 직후 실행: 부하 10분 → 대기 5분 → 부하 10분 (실제 10분 주행 ×2 재현)
#                                        3회 반복해 콜드 시동 과도 상태를 포함한 값을 얻는다. 요약: drive-report
#   sudo bash a1_rt.sh arm       (선택) 원격(SSH)용 — 다음 1회 부팅만 6.8-rt로 예약
#   sudo bash a1_rt.sh rollback  *) 6.8-rt 패키지·DKMS 설정 제거 (메뉴·generic 기본값은 유지)
#
# 모든 출력은 tools/rt/logs/<명령>_<시각>.log 에 남는다 (git 추적 제외).

set -euo pipefail

GEN=6.8.0-138-generic
ROOT_UUID=3cb4c60d-1e8a-444b-8efc-612a444cc5b6
BASE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)   # 저장소 tools/rt/ — 로그·백업은 여기 하위(.gitignore)
GRUB_CFG=/boot/grub/grub.cfg
GRUB_DROPIN=/etc/default/grub.d/99z-a1-bsw-safe-default.cfg
GRUB_TUNED=/etc/grub.d/11_a1_bsw_rt_tuned
GRUB_TUNED_GEN=/etc/grub.d/12_a1_bsw_generic_tuned
TRACEFS=${TRACEFS:-/sys/kernel/tracing}
# RT 튜닝 파라미터 — i7-12700: P코어 8개(CPU 0-15, 하이퍼스레드) + E코어 4개(CPU 16-19)
TUNE_ISO=8-15                 # 격리: 물리 P코어 4~7 (양쪽 스레드 모두) — RT 태스크 전용
TUNE_HOUSE=0-7,16-19          # 하우스키핑·인터럽트: 나머지 P코어 + E코어
TUNE_CMDLINE="isolcpus=managed_irq,domain,$TUNE_ISO nohz_full=$TUNE_ISO rcu_nocbs=$TUNE_ISO rcu_nocb_poll irqaffinity=$TUNE_HOUSE intel_idle.max_cstate=1 processor.max_cstate=1 nmi_watchdog=0 nosoftlockup skew_tick=1"
DKMS_CONF=/etc/dkms/framework.conf
DKMS_MARK="# A1-BSW: NVIDIA 드라이버를 PREEMPT_RT 커널용으로 빌드 (NVIDIA 공식 미지원 우회)"
NV_MAKELOG=/var/lib/dkms/nvidia-srv/470.256.02/build/make.log
# GPU 부하: glmark2 기본 장면 순환에 들어 있는 terrain 장면은 NVIDIA 470 이 셰이더를 컴파일하지 못해
# ("highp" 문법 오류) glmark2 가 segfault 한다 → GPU 사용률이 높고 안정적인 refract 장면만 반복 (~87%)
GL_LOAD=(glmark2 --off-screen --run-forever -s 3840x2160 -b refract)

cmd=${1:-}
case "$cmd" in pin|install|arm|verify|hwlat|tune|bench|soak|trace|drive|drive-report|rollback) ;; *) sed -n '2,20p' "$0"; exit 1 ;; esac

ts=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BASE/logs"
LOG="$BASE/logs/${cmd}_$ts.log"
exec > >(tee -a "$LOG") 2>&1

ok()   { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; }
die()  { echo "  ❌ 중단: $*"; echo "  (로그: $LOG)"; exit 1; }
need_root() { [[ $EUID -eq 0 ]] || die "sudo 로 실행해야 합니다"; }

# 커널 버전 → GRUB 엔트리 경로. 서브메뉴가 남아 있으면 'submenu>entry', 없으면 entry ID만.
entry_path() {
  local id="gnulinux-$1-advanced-${ROOT_UUID}"
  if grep -qF "'gnulinux-advanced-${ROOT_UUID}'" "$GRUB_CFG"; then echo "gnulinux-advanced-${ROOT_UUID}>$id"
  else echo "$id"; fi
}
rt_installed() { ls /boot | sed -nE 's/^vmlinuz-(6\.8\.[0-9]+-[0-9]+-realtime)$/\1/p' | sort -V | tail -1; }

# 부하 프로세스는 setsid 로 별도 프로세스 그룹에 띄운다 — stress-ng 처럼 자식을 여럿 만드는
# 도구를 부모만 죽이면 자식이 남아 다음 측정을 오염시키므로, 그룹 단위로 정리한다.
start_load() { setsid "$@" >/dev/null 2>&1 & BG_PIDS+=($!); }

# 로그용 tee(프로세스 치환)를 쓰면 신호(Ctrl+C·SIGTERM·SSH 끊김)로 죽을 때 bash가 EXIT 트랩을
# 실행하지 않는다. 그래서 스크립트가 사라지면 부하를 정리하는 감시자를 별도 세션에 띄운다.
WATCHDOG=""
start_watchdog() {
  [[ -n "${BG_PIDS[*]:-}" ]] || return 0
  setsid bash -c "while kill -0 $$ 2>/dev/null; do sleep 2; done
    for p in ${BG_PIDS[*]}; do kill -TERM -- -\$p 2>/dev/null || kill -TERM \$p 2>/dev/null; done" \
    >/dev/null 2>&1 &
  WATCHDOG=$!
}
kill_load() {   # 이미 끝난 프로세스면 kill 이 실패하므로 절대 실패를 반환하지 않게 한다 (set -e)
  local p
  for p in "${BG_PIDS[@]:-}"; do
    [[ -n "$p" ]] || continue
    kill -TERM -- "-$p" 2>/dev/null || kill -TERM "$p" 2>/dev/null || true
  done
  BG_PIDS=()
  [[ -n "$WATCHDOG" ]] && kill "$WATCHDOG" 2>/dev/null || true
  WATCHDOG=""
}

cpulist_expand() {   # "8-11,14" → "8 9 10 11 14"
  local p a b parts
  IFS=, read -ra parts <<<"$1"
  for p in "${parts[@]}"; do
    [[ -z $p ]] && continue
    if [[ $p == *-* ]]; then a=${p%-*}; b=${p#*-}; while ((a <= b)); do printf '%s ' "$a"; ((a++)); done
    else printf '%s ' "$p"; fi
  done
}

# "8-15" 또는 "8-11,14" 형태의 CPU 목록에 포함된 CPU 개수
cpulist_count() {
  local p a b n=0 parts
  IFS=, read -ra parts <<<"$1"
  for p in "${parts[@]}"; do
    if [[ $p == *-* ]]; then a=${p%-*}; b=${p#*-}; n=$((n + b - a + 1)); else n=$((n + 1)); fi
  done
  echo "$n"
}

# 기본 부팅이 generic으로 고정돼 있는지 — 모든 변경 단계의 전제조건
check_default_is_generic() {
  local env first
  env=$(grub-editenv list)
  [[ "$env" == *"saved_entry=$(entry_path "$GEN")"* ]] || die "grubenv 기본 엔트리가 generic이 아님: '$env'"
  grep -qF 'set default="${saved_entry}"' "$GRUB_CFG" || die "grub.cfg가 saved_entry를 쓰지 않음 (GRUB_DEFAULT=saved 미반영)"
  grep -qF "'gnulinux-${GEN}-advanced-${ROOT_UUID}'" "$GRUB_CFG" || die "grub.cfg에 $GEN 엔트리 없음"
  first=$(awk '/^menuentry /{f=1} f && $1=="linux" {print $2; exit}' "$GRUB_CFG")
  if [[ "$first" == "/boot/vmlinuz-$GEN" ]]; then ok "메뉴 첫 줄도 $GEN — saved_entry가 깨져도 generic (이중 안전)"
  else warn "메뉴 첫 줄은 $first (saved_entry가 우선하므로 동작 문제는 없음)"; fi
  ok "기본 부팅 = $GEN"
}

show_menu() {
  echo "-- 부팅 메뉴 (위에서부터 순서대로)"
  grep -E "^(menuentry|submenu) " "$GRUB_CFG" | sed -E "s/^(menuentry|submenu) '([^']+)'.*/     \2/"
}

cmd_pin() {
  need_root
  echo "== [pin] 부팅 메뉴 표시 + 기본값 $GEN =="
  [[ "$(findmnt -no UUID /)" == "$ROOT_UUID" ]] || die "루트 UUID 불일치"
  [[ -f /boot/vmlinuz-$GEN ]] || die "/boot/vmlinuz-$GEN 없음"

  local bk="$BASE/backup_$ts"
  mkdir -p "$bk"
  cp -a /etc/default/grub /etc/default/grub.d "$GRUB_CFG" "$DKMS_CONF" "$bk/"
  grub-editenv list > "$bk/grubenv.txt" || true
  dpkg --get-selections > "$bk/dpkg-selections.txt"
  dkms status > "$bk/dkms-status.txt" 2>&1 || true
  ok "설정 백업: $bk"

  cat > "$GRUB_DROPIN" <<EOF
# A1-BSW ($ts): 부팅 때마다 커널 선택 메뉴 표시, 아무것도 안 누르면 6.8 generic.
# 99-realtime.cfg(GRUB_FLAVOUR_ORDER=realtime)보다 나중에 읽히도록 파일명을 99z- 로 둠.
# 무한 대기(-1)는 쓰지 않음 — 차량에서 무인 재부팅 시 메뉴에서 멈추면 안 되므로.
# 원복: 이 파일 삭제 후 sudo update-grub
GRUB_FLAVOUR_ORDER="generic"
GRUB_DISABLE_SUBMENU=y
GRUB_DEFAULT=saved
GRUB_TIMEOUT_STYLE=menu
GRUB_TIMEOUT=10
EOF
  ok "작성: $GRUB_DROPIN"
  update-grub
  grub-set-default "$(entry_path "$GEN")"
  check_default_is_generic
  show_menu
  echo "== [pin] 완료 — 부팅 때마다 메뉴 10초, 방향키 누르면 대기, 안 누르면 $GEN =="
}

cmd_install() {
  need_root
  echo "== [install] 6.8-rt 커널 추가 설치 =="
  [[ "$(uname -r)" == "$GEN" ]] || die "현재 커널이 $GEN 이 아님 — 정상 환경에서 실행하세요"
  check_default_is_generic
  nvidia-smi -L >/dev/null 2>&1 || die "설치 전 기준선: 현재 nvidia-smi 부터 실패함"

  apt-get update -qq || warn "apt update 경고 (계속 진행)"
  local sim others rt
  sim=$(apt-get -s install linux-realtime-hwe-22.04) || die "apt 설치 시뮬레이션 실패"
  grep -qE '^0 upgraded, [0-9]+ newly installed, 0 to remove' <<<"$sim" \
    || { grep -E 'upgraded|^Inst|^Remv' <<<"$sim"; die "기존 패키지 업그레이드/삭제가 포함됨"; }
  others=$(grep -E '^Inst' <<<"$sim" | grep -v realtime || true)
  [[ -z "$others" ]] || die "realtime 외 패키지가 설치 목록에 있음: $others"
  rt=$(sed -nE 's/^Inst linux-image-(6\.8\.[0-9]+-[0-9]+-realtime) .*/\1/p' <<<"$sim")
  rt=${rt%%$'\n'*}
  [[ -n "$rt" ]] || die "설치될 RT 커널 버전을 찾지 못함"
  ok "설치 대상: $rt — 신규 패키지만, 기존 패키지 변경 0건"

  if ! grep -q '^export IGNORE_PREEMPT_RT_PRESENCE=1$' "$DKMS_CONF"; then
    printf '\n%s\nexport IGNORE_PREEMPT_RT_PRESENCE=1\n' "$DKMS_MARK" >> "$DKMS_CONF"
  fi
  ok "DKMS: IGNORE_PREEMPT_RT_PRESENCE=1 (RT 검사만 건너뜀 — generic 빌드엔 영향 없음)"

  DEBIAN_FRONTEND=noninteractive apt-get install -y linux-realtime-hwe-22.04

  echo "-- 설치 후 점검"
  [[ -f /boot/vmlinuz-$rt ]] && ok "커널 이미지 설치됨: $rt" || die "vmlinuz-$rt 없음"
  grep -q '^CONFIG_PREEMPT_RT=y' "/boot/config-$rt" && ok "CONFIG_PREEMPT_RT=y" || warn "PREEMPT_RT 설정 확인 안 됨"
  local m
  for m in rtw88_8822bu peak_pciefd; do
    modinfo -k "$rt" -n "$m" >/dev/null 2>&1 && ok "모듈 $m 있음" || warn "모듈 $m 없음 (RT에서 해당 장치 미동작)"
  done
  local ds; ds=$(dkms status 2>&1)
  grep -qE "$GEN.*: installed" <<<"$ds" || die "generic용 NVIDIA 모듈 상태 이상 — 확인 필요: $ds"
  ok "NVIDIA 470 ($GEN) 그대로 유지"
  if grep -qE "$rt.*: installed" <<<"$ds"; then ok "NVIDIA 470 ($rt) 빌드 성공"
  else
    warn "NVIDIA 470이 $rt 용으로 빌드되지 않음 → RT 부팅 시 GPU 없음. generic 환경엔 영향 없음"
    warn "원인 확인: tail -40 $NV_MAKELOG"
    warn "수동 재시도: sudo env IGNORE_PREEMPT_RT_PRESENCE=1 dkms install nvidia-srv/470.256.02 -k $rt"
  fi
  check_default_is_generic
  show_menu
  echo "== [install] 완료. 다음: sudo reboot → 메뉴에서 'Ubuntu, with Linux $rt' 선택 =="
}

cmd_arm() {
  need_root
  local rt; rt=$(rt_installed)
  [[ -n "$rt" ]] || die "6.8 realtime 커널이 설치돼 있지 않음 (install 먼저)"
  grep -qF "'gnulinux-$rt-advanced-${ROOT_UUID}'" "$GRUB_CFG" || die "grub.cfg에 $rt 엔트리 없음"
  check_default_is_generic
  grub-reboot "$(entry_path "$rt")"
  grub-editenv list
  ok "다음 1회 부팅만 $rt, 그 뒤로는(실패 시 포함) 자동으로 $GEN"
}

cmd_verify() {
  set +e   # 점검 보고용 — 항목 하나 실패해도 끝까지 수집
  local out
  echo "== [verify] $(uname -r) — $(date '+%F %T') =="
  [[ "$(cat /sys/kernel/realtime 2>/dev/null)" == 1 ]] && ok "/sys/kernel/realtime = 1" || warn "RT 커널이 아님"
  uname -v | grep -q PREEMPT_RT && ok "uname: $(uname -v)" || warn "uname에 PREEMPT_RT 없음"
  local iso; iso=$(tr -d '[:space:]' < /sys/devices/system/cpu/isolated 2>/dev/null)
  if [[ -n "$iso" ]]; then
    ok "격리 코어: $iso (RT 튜닝 항목으로 부팅됨)"
    local -A isoset=(); local c f irq; local bad=()
    for c in $(cpulist_expand "$iso"); do isoset[$c]=1; done
    for f in /proc/irq/*/effective_affinity_list; do
      [[ -r $f ]] || continue
      irq=${f#/proc/irq/}; irq=${irq%%/*}
      for c in $(cpulist_expand "$(cat "$f" 2>/dev/null)"); do
        [[ -n ${isoset[$c]:-} ]] && { bad+=("$irq"); break; }
      done
    done
    if ((${#bad[@]})); then
      # 배정만 된 것과 실제로 발생한 것을 구분 — NVMe CPU별 큐처럼 커널이 관리하는 인터럽트는
      # 옮길 수 없지만, 그 CPU에서 디스크 I/O를 하지 않으면 발생하지 않는다.
      local rep cnt name fired=0
      rep=$(awk -v irqs="${bad[*]}" -v iso="$(cpulist_expand "$iso")" '
        NR == 1 { for (i = 1; i <= NF; i++) { c = $i; sub(/^CPU/, "", c); col[c] = i + 1 }; n = NF
                  split(iso, a, " "); split(irqs, b, " "); for (k in b) want[b[k] ":"] = 1; next }
        ($1 in want) { s = 0; for (k in a) if (a[k] != "") s += $(col[a[k]])
                       nm = ""; for (i = n + 2; i <= NF; i++) nm = nm " " $i
                       printf "%s|%d|%s\n", substr($1, 1, length($1) - 1), s, nm }' /proc/interrupts)
      while IFS='|' read -r irq cnt name; do
        [[ -n $irq ]] || continue
        echo "       IRQ $irq:$name — 격리 코어에서 발생 ${cnt}회"
        ((cnt > 0)) && fired=1
      done <<<"$rep"
      if ((fired)); then warn "격리 코어에서 실제 발생한 인터럽트 있음 (A-2: affinity 재조정 필요)"
      else ok "위 IRQ ${#bad[@]}개는 격리 코어에 배정만 됐고 발생 0회 — 격리 코어에서 디스크 I/O를 하지 않으면 발생 안 함 (A-2 충족)"; fi
    else
      ok "격리 코어에 배정된 장치 인터럽트 없음 (A-2 충족)"
    fi
  else
    warn "격리 코어 없음 — 일반 RT 항목으로 부팅됨 (튜닝 전 상태)"
  fi
  if out=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>&1); then ok "GPU: $out"; else warn "nvidia-smi 실패: $out"; fi
  if out=$(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device | grep ':wifi:'); then ok "WiFi: $out"; else warn "WiFi 장치 없음"; fi
  if out=$(ip -br link show type can 2>/dev/null) && [[ -n "$out" ]]; then ok "CAN:"; echo "$out" | sed 's/^/       /'; else warn "CAN 인터페이스 없음"; fi
  echo "-- 커널 경고 (NVIDIA×RT 충돌 징후: BUG / scheduling while atomic / Call Trace)"
  journalctl -k -b --no-pager -o short-monotonic 2>/dev/null \
    | grep -iE 'BUG:|scheduling while atomic|Call Trace|WARNING:|NVRM|rtw_8822bu|peak_pciefd' | tail -40
  echo "== [verify] 끝 — 결과를 Claude 세션에 알려주세요 (로그: $LOG) =="
}

cmd_hwlat() {
  need_root
  echo "== [hwlat] 하드웨어 지연(BIOS SMI 등) 점검 60초 — $(uname -r) =="
  command -v hwlatdetect >/dev/null || die "hwlatdetect 없음 (rt-tests 패키지 필요)"
  local f="$BASE/logs/hwlat_$ts.txt"
  hwlatdetect --duration=60 --threshold=20 > "$f" 2>&1 || { cat "$f"; die "hwlatdetect 실행 실패"; }
  grep -E 'Max Latency|Samples recorded|Samples exceeding' "$f" | sed 's/^/     /'
  if grep -qE 'Samples exceeding threshold: 0$' "$f"; then
    ok "20µs 초과 없음 → 하드웨어·BIOS 원인 아님. isolcpus 튜닝(tune)이 유효한 대응"
  else
    warn "20µs 초과 발견 → BIOS/SMI 원인 가능성. isolcpus 로는 해결 안 됨 — BIOS 전원관리(C-state·SpeedStep·HW monitor) 항목 점검 필요"
  fi
  echo "== [hwlat] 끝 — 원본: $f =="
}

# 튜닝 부팅 항목을 GRUB 메뉴에 추가. 기존 엔트리를 복제해 커널 파라미터만 덧붙인다.
#   tune rt       → RT 커널 + 격리 튜닝            (id: a1-bsw-rt-tuned)
#   tune generic  → generic 커널 + 격리 튜닝 + preempt=full (id: a1-bsw-generic-tuned)
#     generic 은 NVIDIA 가 공식 지원하는 조합 — RT 의 NVIDIA 문제와 비교 측정용
cmd_tune() {
  need_root
  local target=${2:-rt} rt id blk title newid file extra=""
  case "$target" in
    rt)      rt=$(rt_installed); [[ -n "$rt" ]] || die "6.8 realtime 커널이 설치돼 있지 않음"
             file=$GRUB_TUNED; newid=a1-bsw-rt-tuned
             title="A1-BSW: Linux $rt + RT 튜닝 (격리 CPU $TUNE_ISO)" ;;
    generic) rt=$GEN; file=$GRUB_TUNED_GEN; newid=a1-bsw-generic-tuned; extra=" preempt=full"
             title="A1-BSW: Linux $GEN + 저지연 튜닝 (격리 CPU $TUNE_ISO, preempt=full)" ;;
    *)       die "사용법: tune [rt|generic]" ;;
  esac
  echo "== [tune $target] 튜닝 부팅 항목 추가 =="
  check_default_is_generic
  id="gnulinux-$rt-advanced-${ROOT_UUID}"
  blk=$(awk -v id="'$id'" '/^menuentry /{ f = index($0, id) } f { print } f && /^}/ { exit }' "$GRUB_CFG")
  [[ -n "$blk" ]] || die "grub.cfg 에서 $rt 엔트리를 찾지 못함"
  grep -qE '^[[:space:]]*linux[[:space:]]' <<<"$blk" || die "복제한 엔트리에 linux 줄이 없음"

  blk=$(sed -e "1s/^menuentry '[^']*'/menuentry '$title'/" -e "1s/'$id'/'$newid'/" \
            -e "/^[[:space:]]*linux[[:space:]]/ s|\$| ${TUNE_CMDLINE}${extra}|" <<<"$blk")
  {
    printf '#!/bin/sh\nexec tail -n +3 "$0"\n'
    printf '# A1-BSW (%s): a1_rt.sh tune %s 가 생성. 원복: sudo rm %s && sudo update-grub\n' "$ts" "$target" "$file"
    printf '%s\n' "$blk"
  } > "$file"
  chmod 755 "$file"
  ok "작성: $file"
  update-grub
  grep -qF "'$newid'" "$GRUB_CFG" || die "메뉴에 튜닝 항목이 생성되지 않음"
  awk -v id="'$newid'" '$0 ~ id { f = 1 } f && $1 == "linux" { sub(/^[[:space:]]*/, "     "); print; exit }' "$GRUB_CFG"
  check_default_is_generic
  show_menu
  echo "== [tune $target] 완료 — 기본 부팅은 그대로 generic =="
  echo "   다음 1회만 이 항목으로 부팅: sudo grub-reboot $newid && sudo reboot"
}

# 측정용 cyclictest 명령을 CT 배열에, 첫 측정 CPU 번호를 CT_BASE 에 채운다.
# -N 은 쓰지 않는다: 값·히스토그램이 ns 단위가 되어 -h 400 이 0–400 ns 범위가 됨.
# isolcpus 가 켜지면 새 프로세스의 허용 CPU 가 격리 코어 밖(예: 0-7,16-19)으로 제한되고,
# cyclictest 는 -a 목록을 그 허용 범위 안에서만 해석해 "No allowable cpus to run on" 으로 죽는다.
# → taskset 으로 허용 범위를 전체 CPU 로 넓히고, 관리(main) 스레드는 하우스키핑 코어에 둔다.
build_ct() {
  local iso nthr hk online
  CT=(cyclictest -p 99 -m -i 1000 -q -h 400)
  CT_BASE=0
  iso=$(tr -d '[:space:]' < /sys/devices/system/cpu/isolated 2>/dev/null) || iso=""
  if [[ -n "$iso" ]]; then
    nthr=$(cpulist_count "$iso"); CT_BASE=${iso%%[-,]*}
    online=$(cat /sys/devices/system/cpu/online)
    hk=$(awk '/^Cpus_allowed_list:/ {print $2}' /proc/self/status)
    CT=(taskset -c "$online" "${CT[@]}" -t "$nthr" "-a$iso" "--mainaffinity=$hk")   # -a 인자는 붙여 씀
    ok "격리 코어 $iso 에서 측정 (스레드 ${nthr}개, 관리 스레드는 $hk) — 튜닝 적용 상태"
  else
    CT+=(-t 8 -a)
    warn "격리 코어 없음 → CPU 0-7 에서 측정 (튜닝 전 바닥값)"
  fi
}

# cyclictest -q -h 결과 요약: 스레드 중 최악 Max, 평균 Avg, 히스토그램 범위(400µs) 초과 횟수
# $3 = 첫 스레드가 올라간 CPU 번호 (격리 코어에서 측정하면 0이 아님)
ct_summary() {
  awk -v L="$1" -v B="${3:-0}" '
    /^# Max Latencies:/        { for (i = 4; i <= NF; i++) { v = $i + 0; if (v > mx) { mx = v; cpu = i - 4 } }; found = 1 }
    /^# Avg Latencies:/        { for (i = 4; i <= NF; i++) { s += $i; n++ } }
    /^# Histogram Overflows:/  { for (i = 4; i <= NF; i++) ov += $i }
    END {
      if (!found) { printf "  ⚠️  %-18s 결과 파싱 실패 — 원본 로그 확인\n", L; exit }
      printf "  %s %-14s 최악 %4d µs (CPU %d) | 평균 %4.1f µs | 400µs 초과 %d회\n", (mx <= 100 ? "✅" : "⚠️ "), L, mx, cpu + B, (n ? s / n : 0), ov
    }' "$2"
}

cmd_bench() {
  need_root
  echo "== [bench] §5.A cyclictest 실측 — $(uname -r) — $(date '+%F %T') =="
  [[ "$(cat /sys/kernel/realtime 2>/dev/null)" == 1 ]] || warn "RT 커널이 아님 ($(uname -r), $(grep -oE 'preempt=[a-z]+' /proc/cmdline || echo 'preempt 기본')) — 비교 측정용"

  local p missing=() sim
  for p in rt-tests stress-ng glmark2; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if ((${#missing[@]})); then
    sim=$(apt-get -s install "${missing[@]}") || die "apt 설치 시뮬레이션 실패"
    grep -qE '^0 upgraded, [0-9]+ newly installed, 0 to remove' <<<"$sim" || die "측정 도구 설치가 기존 패키지를 바꿈"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}" >/dev/null
  fi
  ok "측정 도구: rt-tests / stress-ng / glmark2"

  echo "   부팅 파라미터: $(cat /proc/cmdline)"

  local CT CT_BASE; build_ct
  local base=$CT_BASE
  local out="$BASE/logs/cyclictest_$ts" start; start=$(date +%s)
  # 부하 프로세스는 PID로만 기다림 — 인자 없는 wait 는 로그용 tee 까지 기다려 영원히 멈춤
  BG_PIDS=()
  trap kill_load EXIT
  trap 'exit 130' INT TERM HUP   # 신호로 끝나도 EXIT 트랩(kill_load)을 거치게 함

  echo "-- [1/4] 무부하 (100초)"
  "${CT[@]}" -l 100000 > "$out.idle.log" || die "cyclictest 실행 실패 (무부하) — 위 메시지 확인"

  echo "-- [2/4] CPU·IO·메모리 부하 (60초) — §5.A Step 4"
  start_load stress-ng --cpu 8 --io 4 --vm 2 --vm-bytes 1G --timeout 70s
  start_watchdog
  sleep 3
  "${CT[@]}" -l 60000 > "$out.cpu.log" || die "cyclictest 실행 실패 (CPU 부하) — 위 메시지 확인"
  wait "${BG_PIDS[@]}" 2>/dev/null || true
  kill_load

  echo "-- [3/4] CPU 부하 + GPU 부하(glmark2 OpenGL) (60초)"
  local gpu_ok=0 disp xauth util maxutil=0
  BG_PIDS=()
  disp=$(ls /tmp/.X11-unix/ 2>/dev/null | sed -nE 's/^X([0-9]+)$/:\1/p' | head -1) || disp=""
  xauth="/run/user/$(id -u "${SUDO_USER:-root}")/gdm/Xauthority"
  if [[ -n "${SUDO_USER:-}" && -n "$disp" && -f "$xauth" ]]; then
    start_load sudo -u "$SUDO_USER" env DISPLAY="$disp" XAUTHORITY="$xauth" \
      timeout 75s "${GL_LOAD[@]}"
    gpu_ok=1
  else
    warn "데스크톱 세션을 못 찾아 GPU 부하 생략 (CPU 부하만)"
  fi
  start_load stress-ng --cpu 8 --io 4 --vm 2 --vm-bytes 1G --timeout 70s
  start_watchdog
  sleep 5
  "${CT[@]}" -l 60000 > "$out.gpu.log" &
  local ct_pid=$!
  while kill -0 "$ct_pid" 2>/dev/null; do
    util=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1) || util=""
    [[ "$util" =~ ^[0-9]+$ ]] && ((util > maxutil)) && maxutil=$util
    sleep 5
  done
  wait "$ct_pid" || die "cyclictest 실행 실패 (CPU+GPU 부하) — 위 메시지 확인"
  kill_load
  trap - EXIT INT TERM HUP

  # 모든 CPU를 동시에 멈추는 BIOS SMI 같은 하드웨어 지연인지, 소프트웨어(드라이버) 지연인지 구분
  echo "-- [4/4] 하드웨어 지연(SMI 등) 점검 — hwlatdetect (60초)"
  local hw=0
  if command -v hwlatdetect >/dev/null; then
    hwlatdetect --duration=60 --threshold=20 > "$out.hwlat.log" 2>&1 && hw=1 || warn "hwlatdetect 실패 — $out.hwlat.log 확인"
  else
    warn "hwlatdetect 없음 — 생략"
  fi

  echo
  echo "== 결과 (§5.A 목표: 최악 지연 수십~100 µs 급) =="
  ct_summary "무부하" "$out.idle.log" "$base"
  ct_summary "CPU 부하" "$out.cpu.log" "$base"
  ct_summary "CPU+GPU 부하" "$out.gpu.log" "$base"
  if ((gpu_ok)); then
    ((maxutil >= 30)) && ok "GPU 사용률 최대 ${maxutil}% — 부하 정상 인가" || warn "GPU 사용률 최대 ${maxutil}% — 부하가 약함"
  fi
  if ((hw)); then
    echo "-- 하드웨어 지연 (20µs 초과 샘플이 있으면 BIOS/SMI 쪽 원인)"
    grep -E 'Max Latency|Samples exceeding' "$out.hwlat.log" | sed 's/^/     /'
  fi
  echo "-- 측정 중 새로 발생한 커널 경고 (NVIDIA×RT 충돌 징후)"
  journalctl -k --since "@$start" --no-pager -o short-monotonic 2>/dev/null \
    | grep -iE 'BUG:|scheduling while atomic|Call Trace|WARNING:|Xid|NVRM.*(error|fail)|rcu.*stall|throttl' \
    || ok "없음"
  echo "== [bench] 끝 — 원본: $out.{idle,cpu,gpu,hwlat}.log =="
}

# 장시간 측정 — 짧은 측정에서 놓치는 드문 스파이크(1차 측정의 882µs 같은 것)를 잡는다
cmd_soak() {
  need_root
  local mins=${2:-30}
  echo "== [soak] 부하 상태 장시간 측정 ${mins}분 — $(uname -r) =="
  [[ "$mins" =~ ^[0-9]+$ ]] || die "사용법: soak [분]"
  [[ "$(cat /sys/kernel/realtime 2>/dev/null)" == 1 ]] || warn "RT 커널이 아님 ($(uname -r), $(grep -oE 'preempt=[a-z]+' /proc/cmdline || echo 'preempt 기본')) — 비교 측정용"
  command -v cyclictest >/dev/null || die "rt-tests 없음 — bench 를 먼저 한 번 실행"
  echo "   부팅 파라미터: $(cat /proc/cmdline)"
  run_soak "$mins" "$BASE/logs/soak_$ts" "${mins}분 부하"
  echo "== [soak] 끝 — 원본: $BASE/logs/soak_$ts.log =="
}

# 부하(GPU+CPU·메모리·I/O) 아래 cyclictest 를 <분> 동안 돌려 <출력접두>.log / .gpu_util 에 남기고 요약을 찍는다.
run_soak() {   # $1 분, $2 출력 경로 접두, $3 요약 라벨
  local mins=$1 out=$2 label=$3
  local CT CT_BASE; build_ct
  local base=$CT_BASE
  local start; start=$(date +%s)
  local secs=$((mins * 60)) disp xauth gpu_on=0 gpulog="$out.gpu_util"
  BG_PIDS=()
  trap kill_load EXIT
  trap 'exit 130' INT TERM HUP   # 신호로 끝나도 EXIT 트랩(kill_load)을 거치게 함
  disp=$(ls /tmp/.X11-unix/ 2>/dev/null | sed -nE 's/^X([0-9]+)$/:\1/p' | head -1) || disp=""
  xauth="/run/user/$(id -u "${SUDO_USER:-root}")/gdm/Xauthority"
  if [[ -n "${SUDO_USER:-}" && -n "$disp" && -f "$xauth" ]]; then
    start_load sudo -u "$SUDO_USER" env DISPLAY="$disp" XAUTHORITY="$xauth" \
      timeout $((secs + 20))s "${GL_LOAD[@]}"
    gpu_on=1
    ok "GPU 부하(glmark2 ${GL_LOAD[*]: -1}) 시작"
  else
    warn "GPU 부하 생략 (데스크톱 세션 없음)"
  fi
  start_load stress-ng --cpu 8 --io 4 --vm 2 --vm-bytes 1G --timeout $((secs + 10))s
  # GPU 부하가 중간에 끊겨도 결과에 드러나도록 10초마다 사용률과 glmark2 생존 여부를 기록
  ((gpu_on)) && start_load bash -c "sleep 5; while :; do
      u=\$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | head -1)
      pgrep -x glmark2 >/dev/null && a=1 || a=0
      echo \"\$(date +%T) \$u \$a\" >> '$gpulog'; sleep 10; done"
  start_watchdog
  sleep 5
  echo "   측정 시작 $(date '+%T') — 종료 예정 $(date -d "+$secs seconds" '+%T') (중단: Ctrl+C)"
  "${CT[@]}" -D "${mins}m" > "$out.log" || die "cyclictest 실행 실패 — 위 메시지 확인"
  kill_load
  trap - EXIT INT TERM HUP

  echo
  echo "== 결과 ($label) =="
  ct_summary "$label" "$out.log" "$base"
  if ((gpu_on)); then
    local n avg min max dead
    if [[ -s $gpulog ]] && read -r n avg min max dead < <(awk '
        { n++; s += $2; if (n == 1 || $2 < mn) mn = $2; if ($2 > mx) mx = $2; if ($3 == 0) d++ }
        END { printf "%d %d %d %d %d\n", n, s / n, mn, mx, d }' "$gpulog"); then
      if ((dead == 0 && avg >= 30)); then
        ok "GPU 부하 유지: 사용률 평균 ${avg}% (최소 ${min}% / 최대 ${max}%), glmark2 중단 0회 (${n}회 확인)"
      else
        warn "GPU 부하가 측정 내내 유지되지 않음: 평균 ${avg}% (최소 ${min}%), glmark2 없음 ${dead}/${n}회 → CPU 부하 위주 결과로 해석"
      fi
    else
      warn "GPU 사용률 기록 없음 — GPU 부하 확인 불가"
    fi
  fi
  echo "-- 측정 중 커널 경고"
  journalctl -k --since "@$start" --no-pager -o short-monotonic 2>/dev/null \
    | grep -iE 'BUG:|scheduling while atomic|Call Trace|WARNING:|Xid|NVRM.*(error|fail)|rcu.*stall' || ok "없음"
}

# 지연 원인 추적 — cyclictest 가 임계값을 넘는 지연을 만나면 그 순간 ftrace 를 멈춰
# "무엇이 RT 스레드를 막았는지"(인터럽트·IPI·다른 태스크)를 기록으로 남긴다.
TRACE_EVENTS="irq/irq_handler_entry irq/irq_handler_exit irq/softirq_entry ipi/ipi_send_cpu ipi/ipi_entry ipi/ipi_exit sched/sched_switch"
trace_events_set() {   # $1 = 1(켜기) / 0(끄기)
  local ev
  for ev in $TRACE_EVENTS; do
    [[ -e "$TRACEFS/events/$ev/enable" ]] && echo "$1" > "$TRACEFS/events/$ev/enable" || true
  done
}

cmd_trace() {
  need_root
  local mins=${2:-5} thr=${3:-300}
  echo "== [trace] 지연 원인 추적 ${mins}분 (임계 ${thr}µs) — $(uname -r) =="
  [[ "$mins" =~ ^[0-9]+$ && "$thr" =~ ^[0-9]+$ ]] || die "사용법: trace [분] [임계µs]"
  [[ -w "$TRACEFS/tracing_on" ]] || die "ftrace 를 쓸 수 없음: $TRACEFS"
  command -v cyclictest >/dev/null || die "rt-tests 없음 — bench 를 먼저 한 번 실행"
  echo "   부팅 파라미터: $(cat /proc/cmdline)"

  local CT CT_BASE; build_ct
  local out="$BASE/logs/trace_$ts" start; start=$(date +%s)
  local secs=$((mins * 60)) disp xauth

  echo 0 > "$TRACEFS/tracing_on"; : > "$TRACEFS/trace"; echo nop > "$TRACEFS/current_tracer"
  trace_events_set 1
  echo 1 > "$TRACEFS/tracing_on"
  ok "추적 켜짐 (인터럽트·IPI·태스크 전환) — 추적 자체도 약간의 부하가 됨"

  BG_PIDS=()
  trap 'trace_events_set 0; kill_load' EXIT
  trap 'exit 130' INT TERM HUP
  disp=$(ls /tmp/.X11-unix/ 2>/dev/null | sed -nE 's/^X([0-9]+)$/:\1/p' | head -1) || disp=""
  xauth="/run/user/$(id -u "${SUDO_USER:-root}")/gdm/Xauthority"
  if [[ -n "${SUDO_USER:-}" && -n "$disp" && -f "$xauth" ]]; then
    start_load sudo -u "$SUDO_USER" env DISPLAY="$disp" XAUTHORITY="$xauth" \
      timeout $((secs + 20))s "${GL_LOAD[@]}"
    ok "GPU 부하 시작"
  else
    warn "GPU 부하 생략 (데스크톱 세션 없음)"
  fi
  start_load stress-ng --cpu 8 --io 4 --vm 2 --vm-bytes 1G --timeout $((secs + 10))s
  start_watchdog
  sleep 5

  echo "   측정 시작 $(date '+%T') — ${thr}µs 초과가 나오면 그 지점에서 추적 정지"
  # cyclictest -b 는 /sys/kernel/debug/tracing/tracing_on 에 0 을 쓴다(구 경로). 이 PC 는 debugfs 아래에
  # tracing 이 따로 마운트돼 있지 않아 새 경로($TRACEFS)와 어긋날 수 있으므로, 구 경로가 없으면 연결해 준다.
  if [[ ! -e /sys/kernel/debug/tracing/tracing_on ]]; then
    mkdir -p /sys/kernel/debug/tracing 2>/dev/null && mount -t tracefs nodev /sys/kernel/debug/tracing 2>/dev/null \
      && ok "cyclictest 용 구 경로(/sys/kernel/debug/tracing) 연결" || warn "구 경로 연결 실패 — cyclictest 가 추적을 못 멈출 수 있음"
  fi
  "${CT[@]}" -b "$thr" --tracemark -D "${mins}m" > "$out.log" 2>&1 || true
  kill_load
  trap - EXIT INT TERM HUP

  echo
  local brk; brk=$(sed -nE 's/^# Break value: ([0-9]+).*/\1/p' "$out.log")
  local on_new on_old; on_new=$(cat "$TRACEFS/tracing_on" 2>/dev/null); on_old=$(cat /sys/kernel/debug/tracing/tracing_on 2>/dev/null)
  if [[ -n "$brk" ]]; then
    ok "cyclictest 가 ${brk}µs 에서 정지 (Break value) — tracing_on: 신경로=${on_new:-?} 구경로=${on_old:-?}"
  fi
  if [[ "$on_new" == 0 || "$on_old" == 0 || -n "$brk" ]]; then
    tail -600 "$TRACEFS/trace" > "$out.ftrace"
    [[ -s "$out.ftrace" ]] && ok "임계 초과 발생 → 그 직전 기록 저장: $out.ftrace" \
                           || warn "임계 초과는 있었으나 추적 버퍼가 비어 있음 (경로 불일치로 정지 신호가 안 닿았을 가능성)"
    echo "   분석: python3 $(dirname "$0")/trace_analyze.py $out.ftrace"
    echo "-- 지연 직전 기록 (마지막 25줄)"; tail -25 "$out.ftrace" | sed 's/^/     /'
    echo "-- 직전 기록에서 많이 나온 항목"
    grep -oE '(irq_handler_entry: irq=[0-9]+ name=[^ ]+|ipi_entry: \([^)]*\)|softirq_entry: vec=[0-9]+ \[action=[A-Z_]+\])' "$out.ftrace" \
      | sort | uniq -c | sort -rn | head -8 | sed 's/^/     /'
  else
    warn "${mins}분 동안 ${thr}µs 초과가 없어 추적이 발동하지 않음"
  fi
  trace_events_set 0; : > "$TRACEFS/trace"; echo 1 > "$TRACEFS/tracing_on"; echo 1 > /sys/kernel/debug/tracing/tracing_on 2>/dev/null || true
  ct_summary "${mins}분(추적 중)" "$out.log" "$CT_BASE"
  local nbug
  nbug=$(journalctl -k --since "@$start" --no-pager 2>/dev/null | grep -c 'scheduling while atomic') || nbug=0
  echo "  측정 중 NVIDIA 'scheduling while atomic' 발생: ${nbug}회"
  echo "== [trace] 끝 =="
}

# 주행 프로필 — 1차·2차 주행(각 ~10분)을 재현. 콜드 부팅 직후 실행해야 의미가 있다(시동 과도 상태 포함).
#   sudo bash a1_rt.sh drive            # 부팅 후 경과 시간이 15분을 넘으면 경고
# 결과는 logs/drive_<시각>/ 에 run1(부하 10분), idle(대기 5분), run2(부하 10분) 로 남고, drive-report 가 3회분을 표로 모은다.
cmd_drive() {
  need_root
  local up; up=$(cut -d. -f1 /proc/uptime)
  echo "== [drive] 주행 프로필 — $(uname -r), 부팅 후 $((up/60))분 =="
  ((up <= 900)) || warn "부팅 후 $((up/60))분 경과 — 콜드 시동 과도 상태가 빠진다. 재부팅 직후 실행 권장"
  [[ -n "$(tr -d '[:space:]' < /sys/devices/system/cpu/isolated)" ]] || warn "격리 코어 없음 — 튜닝 부팅이 아님"
  command -v cyclictest >/dev/null || die "rt-tests 없음 — bench 를 먼저 한 번 실행"
  local dir="$BASE/logs/drive_$ts"; mkdir -p "$dir"
  echo "   결과 폴더: $dir"
  echo "-- [1/3] 1차 주행 (부하 10분)"
  run_soak 10 "$dir/run1" "1차 주행 10분"
  echo "-- [2/3] 그리드 대기 (무부하 5분, cyclictest 만)"
  local CT CT_BASE; build_ct
  "${CT[@]}" -D 5m > "$dir/idle.log" 2>&1 || warn "대기 구간 cyclictest 실패"
  ct_summary "대기 5분" "$dir/idle.log" "$CT_BASE"
  echo "-- [3/3] 2차 주행 (부하 10분)"
  run_soak 10 "$dir/run2" "2차 주행 10분"
  echo; echo "== [drive] 요약 =="
  ct_summary "1차 주행 10분" "$dir/run1.log" "$CT_BASE"
  ct_summary "대기 5분" "$dir/idle.log" "$CT_BASE"
  ct_summary "2차 주행 10분" "$dir/run2.log" "$CT_BASE"
  echo "== [drive] 끝 — 3회 모으기: bash $0 drive-report =="
}

cmd_drive_report() {
  local d n=0
  printf "%-22s %-14s %-16s %-14s %s\n" "실행(부팅 후 시작)" "1차 최악 µs" "대기 최악 µs" "2차 최악 µs" "400µs 초과(1차/대기/2차)"
  for d in "$BASE"/logs/drive_*/; do
    [[ -f "$d/run1.log" ]] || continue; n=$((n+1))
    local m1 mi m2 o1 oi o2
    m1=$(awk '/^# Max Latencies:/{m=0; for(i=4;i<=NF;i++) if($i+0>m) m=$i+0; print m}' "$d/run1.log")
    mi=$(awk '/^# Max Latencies:/{m=0; for(i=4;i<=NF;i++) if($i+0>m) m=$i+0; print m}' "$d/idle.log" 2>/dev/null || echo "-")
    m2=$(awk '/^# Max Latencies:/{m=0; for(i=4;i<=NF;i++) if($i+0>m) m=$i+0; print m}' "$d/run2.log")
    o1=$(awk '/^# Histogram Overflows:/{s=0; for(i=4;i<=NF;i++) s+=$i; print s}' "$d/run1.log")
    oi=$(awk '/^# Histogram Overflows:/{s=0; for(i=4;i<=NF;i++) s+=$i; print s}' "$d/idle.log" 2>/dev/null || echo "-")
    o2=$(awk '/^# Histogram Overflows:/{s=0; for(i=4;i<=NF;i++) s+=$i; print s}' "$d/run2.log")
    printf "%-22s %-14s %-16s %-14s %s\n" "$(basename "$d" | sed 's/drive_//')" "$m1" "$mi" "$m2" "$o1 / $oi / $o2"
  done
  ((n)) || echo "  (drive 결과 없음)"
}

cmd_rollback() {
  need_root
  echo "== [rollback] 6.8-rt 제거 =="
  local rt abi; rt=$(rt_installed)
  if [[ -n "$rt" ]]; then
    [[ "$(uname -r)" != "$rt" ]] || die "지금 $rt 로 부팅 중 — $GEN 으로 재부팅 후 실행"
    abi=${rt%-realtime}
    DEBIAN_FRONTEND=noninteractive apt-get purge -y \
      linux-realtime-hwe-22.04 linux-image-realtime-hwe-22.04 linux-headers-realtime-hwe-22.04 \
      "linux-image-$rt" "linux-modules-$rt" "linux-modules-extra-$rt" "linux-headers-$rt" "linux-tools-$rt" \
      "linux-realtime-6.8-headers-$abi" "linux-realtime-6.8-tools-$abi"
    ok "$rt 패키지 제거"
  else
    warn "6.8 realtime 커널 없음 — 패키지 원복 생략"
  fi
  sed -i "/^# A1-BSW: NVIDIA/d; /^export IGNORE_PREEMPT_RT_PRESENCE=1$/d" "$DKMS_CONF"
  ok "DKMS 설정 원복"
  rm -f "$GRUB_TUNED" "$GRUB_TUNED_GEN" && ok "튜닝 부팅 항목 제거"
  update-grub
  check_default_is_generic
  echo "== [rollback] 완료. 메뉴·generic 기본값은 유지 (해제: sudo rm $GRUB_DROPIN && sudo update-grub) =="
}

"cmd_${cmd//-/_}" "$@"
