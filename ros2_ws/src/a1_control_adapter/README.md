# a1_control_adapter — 제어기 출력 → can_guard 어댑터

제어기는 **목표 가감속 [m/s²]** 과 **타이어각 [°]** 두 값만 낸다(2026-10-26 확정). 이 패키지가 그 값을 핸들각·브레이크 %·
가속 % 로 바꿔 can_guard 공유메모리(`safety/can_guard/protocol.py` 의 `CommandChannel`)에 넣는다. 차량으로 0x210 을 보내는
건 여전히 can_guard 하나뿐이다. 현장 절차는 [`tools/track_day/TRACK_MANUAL.md`](../../../tools/track_day/TRACK_MANUAL.md).

## 제어기 팀이 할 일 — 이 토픽 하나만 발행

| 토픽 | 타입 | 필드 |
|---|---|---|
| `/control/target` | `a1_can_msgs/msg/ControllerOutput` | `header.stamp`(계산 시각), `accel_mps2`(+가속/−감속), `steer_tire_deg`(앞 타이어각, +좌회전) |

- 주기 10~100 Hz 자유(어댑터가 50 Hz 로 다시 쓴다). 150 ms 넘게 끊기면 고장으로 본다.
- QoS 는 RELIABLE·BEST_EFFORT 둘 다 된다(어댑터가 BEST_EFFORT 로 구독).
- NaN/inf, |가감속| > 15 m/s², |타이어각| > 45° 는 고장으로 보고 버린다(정상 범위 안의 큰 값은 표 끝에서 포화).
- 차량 상태 피드백은 `/control/status/control_info`(0x200 핸들각·브레이크 위치·auto, BEST_EFFORT)와 GNSS 토픽.
  핸들각 → 타이어각은 `config/track.yaml` 의 `steer_map` 역변환(`conversion.SteerMap.to_tire`).

## 노드

| 실행 파일 | 역할 |
|---|---|
| `cmd_adapter` | 변환·축별 arm·고장 래치. 서비스 `/control/adapter/arm_steer`·`arm_long`(SetBool), `/control/adapter/disarm`(Trigger). 상태 `/control/adapter/status`(`AdapterStatus`, 50 Hz) |
| `test_target_pub` | 제어기 대역 — `mode:=pattern`(사인 조향 + 가감속 계단) / `mode:=const`(`ros2 param set` 으로 실행 중 변경) |

동작 규칙: 시작 = disarm(auto 0·명령 0 기록, 차량 무시 = shadow) → arm 은 신선한 입력이 있을 때만(조향은 0x200 위치도 필요,
현재 위치에서 120 °/s 로 출발) → arm 중 입력 끊김/불량이면 기록·하트비트 중단(래치) → can_guard HOLDING → STOPPED.
자동 복귀 없음, `disarm` 으로만 해제. can_guard 가 재시작하면 공유메모리 inode 변화를 보고 1초 안에 다시 연결한다.
인지 프로세스가 아직 없어 하트비트도 이 노드가 대신 보낸다(`perception_heartbeat`).

## 표 (`config/track.yaml`) — 전부 잠정

| 표 | 초기값 | 근거 / 확정 방법 |
|---|---|---|
| `steer_map` | 타이어 ±20° ↔ 핸들 ±150°(조향비 7.5), `steer_sign: 1` | 10-08 이미지 판독 / T1·T2 |
| `brake_map` | 0.1·1·2·3·4 m/s² → 0·40·70·90·100 % | 10-08 브레이크 이벤트 / T3 |
| `accel_map` | 0.1 → 0 %, 1.3 → 30 %, 상한 30 % | 10-08 최대 가속 1.3 m/s² / T4 |

`tools/track_day/fit_maps.py` 가 녹화에서 이 조각을 만들어 준다.

## 실행·시험

```bash
cd ~/git/A1-BSW/ros2_ws && colcon build --symlink-install && source install/setup.bash
ros2 launch a1_control_adapter track.launch.py channel:=vcan0 test_pub:=true     # can_guard 는 따로(bringup.sh)
python3 -m pytest -q ros2_ws/src/a1_control_adapter/test                         # 저장소 루트에서, ROS 불필요
bash tools/track_day/rehearse_track.sh                                           # SIL 17항목
```
