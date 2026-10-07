# ros2_ws — Phase C (ROS2 상태 브리지)

실차 A1 프로토콜(`DBC/A1_dbc_fixed.dbc`) 상태 메시지를 ROS2 토픽으로 내보낸다. 2026-10-07 EAIT DBC 시절 디코더
(spd/eps/acc/imu, 0x710~0x713)와 그 메시지·launch 를 지우고 A1 기준 하나로 정리했다(git 기록).

**이 토픽들은 감시 등급**(§5.C 설계 규칙): 10 ms 대 제어 루프가 직접 구독하지 말 것. 차량으로 명령을 보내는 건
`safety/can_guard/can_guard.py` 하나뿐이고, 이 워크스페이스의 노드는 아무것도 송신하지 않는다.

## 패키지

| 패키지 | 종류 | 내용 |
|---|---|---|
| `a1_can_msgs` | ament_cmake (msg) | `CanFrame`(원시 프레임), `ControlInfo`(0x200), `WheelInfo`(0x201), `ControlCommand`(버스 위 0x210) |
| `a1_can_bridge` | ament_python | `can_raw_bridge`(SocketCAN → `/interface/can/read/raw`), `a1_status_decoder`(raw 1회 구독 → 3토픽 + 축별 카운터 진단), `dbc_bits.py`(비트필드 공용 추출), `e2e.py`(`AliveCounter`), `rt_utils.py`(A-3), `launch/a1_bridge.launch.py` |

| 토픽 | 타입 | 출처 |
|---|---|---|
| `/interface/can/read/raw` | `CanFrame` | 버스의 모든 프레임 |
| `/control/status/control_info` | `ControlInfo` | 0x200 — 조향 위치(×0.1°)·브레이크 위치, 축별 auto, live_counter 3개 + 축별 `alive_ok` |
| `/control/status/wheel_info` | `WheelInfo` | 0x201 — 좌우 바퀴 속도(12비트 ×0.1 km/h)·rpm, live_counter 2개 |
| `/control/status/command_on_bus` | `ControlCommand` | 0x210 을 버스에서 본 그대로(can_guard 든 원격조종 등 다른 송신자든) — 조향 ×1° |
| `/diagnostics` | `DiagnosticArray` | 1 Hz — 0x200·0x201 카운터 건너뜀, 0x210 송신자 유무 |

QoS 는 전부 BEST_EFFORT depth 100. 구독 쪽도 반드시 BEST_EFFORT 로(RELIABLE 구독은 연결돼도 조용히 0개 수신).
메시지마다 노드를 따로 두던 EAIT 시절과 달리 노드 **하나**가 raw 를 한 번만 구독한다 — §5.C 실측에서 이 경로의 지연은
DDS 구독·디스패치가 지배했으므로 구독 수를 줄이는 쪽이 낫다.

## 빌드·시험

```bash
cd ~/git/A1-BSW/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
colcon test && colcon test-result --all       # 2026-10-07: 28개, 실패 0 (수정본 DBC + 9/17 실차 프레임 대조 포함)
```

## 실행

```bash
# SIL — 가짜 실차(0x200/0x201 20 ms)
python3 ~/git/A1-BSW/sil/vcan/fake_a1_vehicle.py --channel vcan0 &
ros2 launch a1_can_bridge a1_bridge.launch.py channel:=vcan0

ros2 topic hz /control/status/control_info    # ≈ 50 Hz (0x200 주기 20 ms) — 2026-10-07 실측 50.0
ros2 topic hz /control/status/wheel_info      # ≈ 50 Hz — 실측 50.0
ros2 topic echo /diagnostics --once
```

- 실차(PEAK 카드): `channel:=can0`(또는 `can1`).
- 실차(Kvaser Leaf v3): Kvaser 는 이 PC 에서 SocketCAN 인터페이스가 안 생긴다 → `tools/kvaser/kvaser_mirror.py` 로
  `kv0` 에 복사한 뒤 `channel:=kv0`(tools/kvaser/README.md).

⚠️ `cpu_affinity`·`rt_priority` 는 반드시 `ros2 launch` 인자로 — `ros2 run ... -p cpu_affinity:=8` 은 YAML 이 정수로
해석해 `InvalidParameterTypeException`(2026-09-18 실제로 겪음). launch 파일은 `ParameterValue` 로 타입을 고정해 뒀다.

## 지연 실측 (2026-09-18, EAIT 시절 — 결론은 구조에 관한 것이라 지금도 유효)

| 조건 | 지연(평균, ms) |
|---|---|
| 격리 없음 | 2 |
| `cpu_affinity=8` | 1~2 |
| `cpu_affinity=8` + 진짜 `SCHED_FIFO 80`(`chrt -p` 로 확인) | 1~2 |

격리 코어·SCHED_FIFO 를 걸어도 줄지 않았다 → 지배 요인은 DDS 퍼블리시/구독 경로(직렬화·rclpy 콜백 디스패치).
그래서 차량 명령(TX)은 ROS2 를 거치지 않고 can_guard 가 raw SocketCAN(또는 Kvaser CANlib)으로 직접 보낸다(§7.2).

## 다음

- [ ] 실 버스로 `channel:=can0`(또는 `kv0`) 확인 — 실차 인수일 녹화 데이터로도 재생 가능
- [ ] 제어 노드가 can_guard `CommandChannel` 에 명령을 쓰는 쪽(진짜 MPC/판단 노드 — 아직 없음)
