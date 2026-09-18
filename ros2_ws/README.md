# ros2_ws — Phase C (ROS2 브리지·디코더)

`can_stack_development.md` §5.C / §6 걷기골격 4단계의 첫 수직 슬라이스. `EAIT_INFO_SPD`(0x712, 바퀴 4개 속도) **한 메시지**를
`vcan0 → can_raw_bridge → spd_decoder → /control/status/wheel` 까지 끝까지 관통시킨 것 — "완성"이 아니라 "파이프라인이 실제로 도는가"의 확인.

## 패키지

| 패키지 | 종류 | 내용 |
|---|---|---|
| `a1_can_msgs` | ament_cmake (msg) | `CanFrame`(원시 프레임), `WheelSpeeds`(0x712 디코드 결과) |
| `a1_can_bridge` | ament_python | `can_raw_bridge`(SocketCAN → `/interface/can/read/raw`), `spd_decoder`(0x712 필터·디코드 → `/control/status/wheel`), 둘 다 띄우는 launch |

## 빌드

```bash
cd ~/git/A1-BSW/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 실행 (SIL, vcan0)

```bash
# 터미널 1 — 송신기(기존 sil/vcan 도구, 그대로 재사용)
python3 ~/git/A1-BSW/sil/vcan/eait_tx.py --range 0 60

# 터미널 2 — 브리지+디코더
source /opt/ros/humble/setup.bash && source ~/git/A1-BSW/ros2_ws/install/setup.bash
ros2 launch a1_can_bridge spd_slice.launch.py channel:=vcan0

# 터미널 3 — 확인
ros2 topic hz /interface/can/read/raw      # ≈ 100 Hz (0x712 주기 10 ms)
ros2 topic hz /control/status/wheel
ros2 topic delay /control/status/wheel     # header.stamp(수신 시각) 기준 종단 지연
ros2 topic echo /control/status/wheel --once
```

## 실차 전환

`channel:=can0`(또는 `can1`)만 바꾸면 된다 — SocketCAN이 가상/물리 인터페이스에 같은 API를 주므로 노드 코드는 무수정.

## 실측 (2026-09-18, SIL, RT 우선순위 없이 — 순정 rt-poll 부팅, 일반 우선순위)

| 항목 | 값 |
|---|---|
| `/interface/can/read/raw` hz | 99.97 Hz (목표 100) |
| `/control/status/wheel` hz | 99.98 Hz |
| 종단 지연 (`ros2 topic delay`) | 평균 2 ms, 최대 2 ms |
| 디코드 정확성 | 같은 프레임 50개를 `cantools`(DBC 정식 디코드)와 대조 — **불일치 0건** |

지연 2 ms 는 일반 우선순위(FIFO 아님) + 격리 코어 미배치 상태의 값이다. A-3 레시피(`--cpu-affinity`, `--rt-priority` 파라미터, §5.A 468행)를
적용한 재측정은 아래 "A-3 레시피 적용" 참고 — 아직 실행 안 함.

## A-3 레시피 적용 (격리 코어 + SCHED_FIFO, 재측정 필요)

```bash
ros2 launch a1_can_bridge spd_slice.launch.py channel:=vcan0 cpu_affinity:=8 rt_priority:=80
# 또는 계정에 rtprio 영구 부여 없이 우선순위만 상속:
sudo chrt -f 80 sudo -u ailab bash -c 'source /opt/ros/humble/setup.bash && source ~/git/A1-BSW/ros2_ws/install/setup.bash && \
  ros2 launch a1_can_bridge spd_slice.launch.py channel:=vcan0 cpu_affinity:=8'
```

## 아직 안 한 것 (다음 단계)

- [ ] 위 A-3 레시피로 재측정 (격리 코어 + FIFO 상태의 hz/지연)
- [ ] `EAIT_INFO_EPS`(0x710)·`EAIT_INFO_ACC`(0x711) 디코더 추가 — 둘 다 `Alive_Cnt` 있음 → E2E(카운터 연속성) 체크 첫 적용
- [ ] `EAIT_INFO_IMU`(0x713) 디코더
- [ ] TX 방향: ROS2 제어 명령(MPC 출력) → `EAIT_Control_01/02`(0x156/0x157) 인코더 — **단, §7.2 설계 규칙대로 이 경로는 `can_guard`(Phase D)가 raw SocketCAN 으로 직접 가져가며 ROS2/DDS 를 hot loop 에 넣지 않는다.** 이 워크스페이스의 노드는 상태 퍼블리시(모니터링·로깅·인지 융합용)까지만.
- [ ] 실 `can0` 물리 루프백(체크리스트 5-1)으로 `channel:=can0` 확인
