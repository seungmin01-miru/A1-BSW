#!/usr/bin/env bash
# 트랙 녹화 — ROS2 bag(mcap) + (SocketCAN 이면) candump. bringup.sh 가 부른다. 단독으로도 쓸 수 있다:
#   bash tools/track_day/record.sh <세션폴더> <CAN채널>      # Ctrl+C 로 종료
#
# 녹화 토픽은 주행 보고서(1000002569.png 형식: 속도·종/횡가속·핸들각 명령/실측·타이어각·요레이트·페달·브레이크·
# AUTO 비트·GPS 궤적·고도)를 나중에 다시 만들 수 있게 고른 것이다(docs/a1_vehicle_characteristics.md 6절).
# 없는 토픽은 ros2 bag 이 그냥 기다린다(나중에 뜨면 그때부터 기록).
set -u
D=${1:?세션 폴더}; CH=${2:-}
mkdir -p "$D"
TOPICS=(
  /control/target                  # 제어기 출력(가감속 m/s², 타이어각 °)
  /control/adapter/status          # 어댑터 변환 결과·arm 상태·입력 나이
  /control/status/control_info     # 0x200 조향·브레이크 실측, 축별 auto
  /control/status/wheel_info       # 0x201 바퀴 속도
  /control/status/command_on_bus   # 버스 위 0x210(우리 것이든 다른 송신자든)
  /diagnostics
  /gsof_client/odom /gsof_client/navsat /gsof_client/gsof/ins_solution_49 /gsof_client/gsof/ins_solution_rms_50
  /a1_gnss/odom /a1_gnss/navsat
)
PIDS=()
if [ -n "$CH" ] && ip link show "$CH" >/dev/null 2>&1; then
  candump -L "$CH" > "$D/candump_${CH}.log" & PIDS+=($!)
fi
ros2 bag record -s mcap -o "$D/bag" "${TOPICS[@]}" > "$D/bag_record.log" 2>&1 & PIDS+=($!)
trap 'kill -INT "${PIDS[@]}" 2>/dev/null; wait' INT TERM
wait
