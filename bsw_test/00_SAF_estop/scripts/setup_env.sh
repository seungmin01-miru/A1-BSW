#!/usr/bin/env bash
# 00 SAF-ESTOP — 대회 차량 PC 에서 SIL 확인(run_sil_check.sh)을 돌릴 환경 준비. 여러 번 실행해도 안전(멱등).
#
#   bash bsw_test/00_SAF_estop/scripts/setup_env.sh               # 점검 + 빠진 것 설치(vcan 은 sudo 물어봄)
#   bash bsw_test/00_SAF_estop/scripts/setup_env.sh --build-ws    # + ros2_ws colcon build (rehearse_track 용)
#   bash bsw_test/00_SAF_estop/scripts/setup_env.sh --check       # 점검만, 아무것도 안 바꿈
#
# 하는 일 (tools/deploy 30_ros2.sh·90_validate.sh 와 같은 버전·경로 기준)
#   1. vcan0 생성·UP           — sil/vcan/vcan_up.sh (sudo). 차량 버스(can0/Kvaser)는 건드리지 않음
#   2. 사용자 python3 패키지   — python-can 4.6.1, cantools 44.0.0, pytest
#                                오프라인 wheel(/var/local/a1-wheels 또는 $A1_WHEELS) 우선, 없으면 인터넷 pip --user
#   3. (선택) ros2_ws 빌드     — /opt/ros/humble 있을 때만
#   4. 저장소 상태              — 브랜치·커밋, can_guard 실행 중 여부
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
CHECK=0; BUILD_WS=0
for a in "$@"; do case "$a" in --check) CHECK=1;; --build-ws) BUILD_WS=1;; *) echo "알 수 없는 인자: $a"; exit 2;; esac; done
WHEELS=${A1_WHEELS:-/var/local/a1-wheels}
NEED=0
ok()   { echo "  ✅ $*"; }
warn() { echo "  ⚠️  $*"; NEED=1; }
PY() { env -u PYTHONPATH python3 "$@"; }   # ROS 의 PYTHONPATH 가 섞이면 pytest 플러그인이 깨짐

echo "== 00 SAF-ESTOP SIL 환경 — $(hostname) $(date '+%F %T')"

echo "-- 1. vcan0"
if ip -br link show vcan0 2>/dev/null | grep -q UP; then ok "vcan0 UP"
elif ((CHECK)); then warn "vcan0 없음/DOWN → sudo bash $REPO/sil/vcan/vcan_up.sh"
else
  sudo bash "$REPO/sil/vcan/vcan_up.sh" vcan0 >/dev/null && ok "vcan0 생성·UP" || warn "vcan0 실패 (modprobe vcan 가능한 커널인지)"
  echo "     (재부팅 후 자동: sudo bash $REPO/sil/vcan/vcan_up.sh --install)"
fi

echo "-- 2. python3 패키지 ($(python3 --version 2>&1))"
want() {  # want <import 이름> <pip 이름> <버전 또는 빈칸>
  local mod=$1 pkg=$2 ver=$3 have
  have=$(PY -c "import importlib.metadata as m; print(m.version('$pkg'))" 2>/dev/null)
  if PY -c "import $mod" 2>/dev/null && { [ -z "$ver" ] || [ "$have" = "$ver" ]; }; then ok "$pkg $have"; return; fi
  local spec=$pkg; [ -n "$ver" ] && spec="$pkg==$ver"
  if ((CHECK)); then warn "$spec 필요 (현재: ${have:-없음})"; return; fi
  if [ -d "$WHEELS" ] && PY -m pip install -q --user --no-index --find-links "$WHEELS" "$spec" 2>/dev/null; then
    ok "$spec 설치 (오프라인 $WHEELS)"
  elif PY -m pip install -q --user "$spec" 2>/dev/null; then ok "$spec 설치 (인터넷)"
  else warn "$spec 설치 실패 — 오프라인이면 번들 wheels 경로를 A1_WHEELS 로 지정"; fi
}
PY -m pip --version >/dev/null 2>&1 || warn "pip 없음 → sudo apt install python3-pip (오프라인: a1-apt install python3-pip)"
want can python-can 4.6.1
want cantools cantools 44.0.0
want pytest pytest ""

echo "-- 3. ros2_ws (rehearse_track 용, 선택)"
if [ -f "$REPO/ros2_ws/install/setup.bash" ]; then ok "빌드됨"
elif ((BUILD_WS)) && ((CHECK == 0)) && [ -f /opt/ros/humble/setup.bash ]; then
  ( set +u; source /opt/ros/humble/setup.bash; cd "$REPO/ros2_ws" && colcon build --symlink-install 2>&1 | tail -3 ) \
    && ok "colcon build" || warn "colcon build 실패"
else echo "  ·  미빌드 — rehearse_track 은 건너뜀 (원하면 --build-ws)"; fi

echo "-- 4. 저장소"
echo "  ·  $(git -C "$REPO" rev-parse --abbrev-ref HEAD) @ $(git -C "$REPO" log --oneline -1)"
git -C "$REPO" diff --quiet -- safety || warn "safety/ 에 커밋 안 된 수정 있음 — 결과에 (+수정 있음) 으로 표시됨"
pgrep -f "safety/can_guard/can_guard.py" >/dev/null && warn "can_guard 실행 중 — 실차 세션이면 stop.sh 후 진행"
iso=$(cat /sys/devices/system/cpu/isolated 2>/dev/null)
echo "  ·  격리 코어: ${iso:-없음} $([ -n "$iso" ] && echo "→ 실제 배포 형태 측정: run_sil_check.sh --rt auto")"

echo
if ((NEED)); then echo "결과: ⚠️ 위 항목 처리 필요"; exit 1; fi
echo "결과: ✅ 준비됨 → bash $HERE/run_sil_check.sh $([ -n "$iso" ] && echo '--rt auto')"
