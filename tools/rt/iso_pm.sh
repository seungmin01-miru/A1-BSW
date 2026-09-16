#!/usr/bin/env bash
# 격리 코어에만 전원관리 차단 (1-6b 후속, A-3 유닛의 원형). 부팅 파라미터 변경 없음 — 재부팅하면 원상복귀.
#   sudo bash tools/rt/iso_pm.sh on [MHz] 격리 코어: cpufreq governor=performance + min=max=MHz 로 클럭 고정(기본 = base_frequency, 보통 2100)
#                                          + cpuidle C-state 전부 disable(POLL 제외). MHz 를 안 주면 기준 클럭에 고정.
#                                          ⚠️ 최대 터보(4900)로 고정하면 8스레드 폴링이 패키지 전력 제한(PL1 35 W)을 넘겨 클럭이 요동 → 오히려 악화(2026-09-15 C 실험)
#   sudo bash tools/rt/iso_pm.sh off      저장해 둔 원래 governor 복원, C-state disable 해제
#   sudo bash tools/rt/iso_pm.sh all [MHz] **전 코어**(0-19) 를 각자 base_frequency(또는 MHz)에 min=max 고정 + no_turbo=1 + C-state disable — C3 실험용
#                                          (C2 에서 격리 코어만 고정해선 부족했음: 하우스키핑 코어의 클럭 전이가 패키지 전체를 멈춤)
#   sudo bash tools/rt/iso_pm.sh eco [MHz=800] [smt-off]  ★ 운용 후보(폴링 비용 절감): 격리 코어를 min=max=MHz(기본 800) 로 낮게 고정
#                                          + smt-off 면 격리 코어의 SMT 짝(홀수 CPU 9,11,13,15) 을 offline → 폴링 스레드 8→4.
#                                          idle=poll + intel_pstate=disable 부팅(rt-poll / generic-poll)에서 쓰는 것을 전제. off 로 원복(online 포함).
#   sudo bash tools/rt/iso_pm.sh hk <governor>  ★ 하우스키핑 코어(격리 밖 전부)만 governor 변경 — 예 `hk schedutil`(⑤: 유휴 시 클럭 하강, 부하 시 상승).
#                                          격리 코어는 건드리지 않음. 원래 governor 는 STATE.hk 에 저장, `hk restore` 로 원복. 재부팅하면 자동 원복.
#        bash tools/rt/iso_pm.sh status   코어별 governor / 클럭 / 비활성 C-state 표 (sudo 없이)
# on/all/eco 는 하우스키핑 코어(0-7,16-19)를 건드리지 않는다. 하우스키핑은 `hk` 서브커맨드로만 바꾼다.
set -euo pipefail
STATE=/var/tmp/a1_iso_pm.state
ISO=$(tr -d '[:space:]' < /sys/devices/system/cpu/isolated)
[[ -n "$ISO" ]] || { echo "격리 코어가 없는 부팅 (isolcpus 없음) — 튜닝 항목으로 부팅해야 함"; exit 1; }
expand() { local p a b; IFS=, read -ra parts <<<"$1"; for p in "${parts[@]}"; do if [[ $p == *-* ]]; then a=${p%-*}; b=${p#*-}; seq "$a" "$b"; else echo "$p"; fi; done; }
CPUS=$(expand "$ISO")

status() {
  echo "no_turbo=$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo -) / 격리 코어 $ISO — cpuidle 드라이버: $(cat /sys/devices/system/cpu/cpuidle/current_driver 2>/dev/null || echo 없음) / cpufreq 드라이버: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver 2>/dev/null || echo 없음)"
  printf "  %-5s %-12s %-11s %-9s %s\n" CPU governor "min~max MHz" "cur MHz" "비활성 C-state / 전체"
  local c d g f dis tot
  for c in $CPUS 0 4 16; do
    d=/sys/devices/system/cpu/cpu$c
    g=$(cat $d/cpufreq/scaling_governor 2>/dev/null || echo -)
    f=$(( $(cat $d/cpufreq/scaling_cur_freq 2>/dev/null || echo 0) / 1000 ))
    dis=0; tot=0
    for s in $d/cpuidle/state*; do [[ -e $s/disable ]] || continue; tot=$((tot+1)); [[ "$(cat $s/disable)" == 1 ]] && dis=$((dis+1)); done
    mm="$(( $(cat $d/cpufreq/scaling_min_freq 2>/dev/null || echo 0)/1000 ))~$(( $(cat $d/cpufreq/scaling_max_freq 2>/dev/null || echo 0)/1000 ))"
    printf "  %-5s %-12s %-11s %-9s %s\n" "$c$(grep -qx "$c" <<<"$CPUS" || echo '(HK)')" "$g" "$mm" "$f" "$dis / $tot"
  done
  echo "  (cur MHz 는 HWP 아래에선 자리표시자일 수 있음 — 실측: sudo python3 tools/rt/core_mhz.py)"
}

case "${1:-}" in
eco)
  [[ $EUID -eq 0 ]] || { echo "sudo 필요"; exit 1; }
  MHZ=${2:-800}
  if [[ "${3:-}" == smt-off ]]; then
    : > "$STATE.offline"
    for c in $CPUS; do
      d=/sys/devices/system/cpu/cpu$c
      if [[ "$(cat $d/online 2>/dev/null || echo 1)" == 0 ]]; then echo "$c" >> "$STATE.offline"; continue; fi   # 이미 offline (재실행)
      sib=$(cut -d, -f1 $d/topology/thread_siblings_list | cut -d- -f1)
      [[ "$sib" == "$c" ]] && continue            # 물리 코어의 첫 스레드는 남김
      echo 0 > $d/online && echo "$c" >> "$STATE.offline"
    done
    echo "  SMT 짝 offline: $(tr '\n' ' ' < "$STATE.offline")(재부팅하면 자동 online)"
  fi
  # 아래 on 은 격리 코어 8-15 를 다시 돌며 offline 코어(cpufreq 디렉터리 없음)는 스스로 건너뜀
  bash "$0" on "$MHZ"
  echo "✅ eco: 격리 코어 ${MHZ} MHz 고정$([[ "${3:-}" == smt-off ]] && echo ' + SMT 짝 offline'). 원복: sudo bash $0 off"
  exit 0 ;;
on|all)
  [[ $EUID -eq 0 ]] || { echo "sudo 필요"; exit 1; }
  if [[ $1 == all ]]; then
    CPUS=$(seq 0 $(( $(nproc --all) - 1 )))
    echo "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo -)" > "$STATE.turbo"
    echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null && echo "  no_turbo=1 (전역 터보 차단)"
  fi
  MHZ_ARG=${2:-}
  MHZ=${MHZ_ARG}
  if [[ -z "$MHZ" ]]; then
    MHZ=$(( $(cat /sys/devices/system/cpu/cpu$(echo "$CPUS" | head -1)/cpufreq/base_frequency 2>/dev/null || echo 0) / 1000 ))
    (( MHZ > 0 )) || MHZ=2100
  fi
  : > "$STATE"
  for c in $CPUS; do
    d=/sys/devices/system/cpu/cpu$c
    if [[ -n "$MHZ_ARG" ]]; then KHZ=$((MHZ_ARG * 1000))
    else KHZ=$(cat $d/cpufreq/base_frequency 2>/dev/null || echo $((MHZ * 1000))); fi   # 코어마다 자기 기준 클럭 (P코어 2.1 GHz, E코어 다름)
    if [[ -e $d/cpufreq/scaling_governor ]]; then
      echo "gov $c $(cat $d/cpufreq/scaling_governor) $(cat $d/cpufreq/scaling_min_freq) $(cat $d/cpufreq/scaling_max_freq)" >> "$STATE"
      echo performance > $d/cpufreq/scaling_governor
      # min ≤ max 제약: 내릴 땐 max 먼저, 올릴 땐 min 먼저
      cur_min=$(cat $d/cpufreq/scaling_min_freq)
      if (( KHZ >= cur_min )); then echo $KHZ > $d/cpufreq/scaling_max_freq; echo $KHZ > $d/cpufreq/scaling_min_freq
      else echo $KHZ > $d/cpufreq/scaling_min_freq; echo $KHZ > $d/cpufreq/scaling_max_freq; fi
    fi
    for s in $d/cpuidle/state*; do
      [[ -e $s/disable ]] || continue
      [[ "$(cat $s/name)" == POLL ]] && continue          # POLL(C0 스핀)은 남긴다 — 잠들지 않는 상태
      echo 1 > $s/disable
    done
  done
  echo "✅ $([[ $1 == all ]] && echo '전 코어 0-'$(( $(nproc --all) - 1 )) || echo "격리 코어 $ISO"): governor=performance, 클럭 min=max=${MHZ_ARG:-각 코어 base} MHz 고정, C-state(POLL 제외) 비활성. 원복: sudo bash $0 off (또는 재부팅)"
  status ;;
off)
  [[ $EUID -eq 0 ]] || { echo "sudo 필요"; exit 1; }
  if [[ -f "$STATE.offline" ]]; then
    for c in $(cat "$STATE.offline"); do echo 1 > /sys/devices/system/cpu/cpu$c/online && echo "  cpu$c online"; done; rm -f "$STATE.offline"
  fi
  [[ -f "$STATE" ]] && CPUS=$(awk '$1=="gov"{print $2}' "$STATE")   # on/all 어느 쪽이었든 기록된 코어만 원복
  if [[ -f "$STATE.turbo" ]]; then v=$(cat "$STATE.turbo"); [[ $v =~ ^[01]$ ]] && echo "$v" > /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null; rm -f "$STATE.turbo"; echo "  no_turbo 원복($v)"; fi
  for c in $CPUS; do
    d=/sys/devices/system/cpu/cpu$c
    for s in $d/cpuidle/state*; do [[ -e $s/disable ]] && echo 0 > $s/disable || true; done
  done
  if [[ -f "$STATE" ]]; then
    while read -r k c g mn mx; do
      [[ $k == gov ]] || continue
      d=/sys/devices/system/cpu/cpu$c/cpufreq
      [[ -n "$mx" ]] && { echo "$mx" > $d/scaling_max_freq; echo "${mn:-800000}" > $d/scaling_min_freq; }
      echo "$g" > $d/scaling_governor
    done < "$STATE"; rm -f "$STATE"
  fi
  echo "✅ 원복"; status ;;
hk)
  [[ $EUID -eq 0 ]] || { echo "sudo 필요"; exit 1; }
  gov=${2:-}; [[ -n "$gov" ]] || { echo "사용법: hk <governor|restore>  (가능: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors))"; exit 1; }
  HK=$(for c in $(seq 0 $(( $(nproc --all) - 1 ))); do grep -qx "$c" <<<"$CPUS" || echo $c; done)   # 격리 밖 = 하우스키핑
  if [[ $gov == restore ]]; then
    [[ -f "$STATE.hk" ]] || { echo "저장된 하우스키핑 governor 없음"; exit 1; }
    while read -r c g; do echo "$g" > /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor; done < "$STATE.hk"; rm -f "$STATE.hk"
    echo "✅ 하우스키핑 governor 원복"; status; exit 0
  fi
  grep -qw "$gov" /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors || { echo "governor '$gov' 없음"; exit 1; }
  [[ -f "$STATE.hk" ]] || for c in $HK; do echo "$c $(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor)" >> "$STATE.hk"; done   # 최초 1회만 원본 저장
  for c in $HK; do echo "$gov" > /sys/devices/system/cpu/cpu$c/cpufreq/scaling_governor; done
  echo "✅ 하우스키핑 $(echo $HK | tr ' ' ',') governor=$gov (격리 $ISO 는 그대로). 원복: sudo bash $0 hk restore (또는 재부팅)"
  status ;;
status) status ;;
*) sed -n '2,14p' "$0"; exit 1 ;;
esac
