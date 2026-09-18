# ros2_ws — Phase C (ROS2 브리지·디코더)

`can_stack_development.md` §5.C / §6 걷기골격. `EAIT_INFO_SPD`(0x712) 한 메시지로 `vcan0 → can_raw_bridge →
spd_decoder → /control/status/wheel` 을 끝까지 관통시킨 첫 슬라이스(2026-09-18)에서 시작해, 같은 날 EAIT 가 보내는
**상태 메시지 4종 전부**(0x710 EPS / 0x711 ACC / 0x712 SPD / 0x713 IMU) 디코더까지 확장했다.

## 패키지

| 패키지 | 종류 | 내용 |
|---|---|---|
| `a1_can_msgs` | ament_cmake (msg) | `CanFrame`(원시 프레임), `WheelSpeeds`(0x712), `EpsStatus`(0x710), `AccStatus`(0x711), `ImuStatus`(0x713) |
| `a1_can_bridge` | ament_python | `can_raw_bridge`(SocketCAN → `/interface/can/read/raw`), `spd_decoder`/`eps_decoder`/`acc_decoder`/`imu_decoder`(각 메시지 필터·디코드), `dbc_bits.py`(비트필드 공용 추출), `e2e.py`(`AliveCounter`), `launch/spd_slice.launch.py`(최소 슬라이스), `launch/status_bridge.launch.py`(전체) |

## 빌드

```bash
cd ~/git/A1-BSW/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
```

## 실행 (SIL, vcan0)

최소 슬라이스(0x712 하나)만 보려면 `spd_slice.launch.py`, EAIT 수신 메시지 4종 전부 보려면 `status_bridge.launch.py`.

```bash
# 터미널 1~4 — 송신기(기존 sil/vcan 도구, 그대로 재사용). EAIT 보드가 네 메시지를 다 보내는 상황을 흉내낸다.
python3 ~/git/A1-BSW/sil/vcan/eait_tx.py --range 0 60           # EAIT_INFO_SPD, 10 ms
python3 ~/git/A1-BSW/sil/vcan/eait_tx.py --msg EAIT_INFO_EPS    # 20 ms
python3 ~/git/A1-BSW/sil/vcan/eait_tx.py --msg EAIT_INFO_ACC    # 10 ms
python3 ~/git/A1-BSW/sil/vcan/eait_tx.py --msg EAIT_INFO_IMU    # 10 ms

# 터미널 5 — 브리지+디코더 전체
source /opt/ros/humble/setup.bash && source ~/git/A1-BSW/ros2_ws/install/setup.bash
ros2 launch a1_can_bridge status_bridge.launch.py channel:=vcan0

# 터미널 6 — 확인
ros2 topic list                              # .../wheel, .../eps, .../acc, .../imu, /diagnostics
ros2 topic hz /control/status/eps            # ≈ 50 Hz (0x710 주기 20 ms)
ros2 topic hz /control/status/acc            # ≈ 100 Hz (0x711 주기 10 ms)
ros2 topic hz /control/status/imu            # ≈ 100 Hz (0x713 주기 10 ms)
ros2 topic echo /control/status/eps --once
ros2 topic echo /diagnostics --once          # level=OK, total_skips=0 이면 Alive_Cnt 연속
```

## 실차 전환

`channel:=can0`(또는 `can1`)만 바꾸면 된다 — SocketCAN이 가상/물리 인터페이스에 같은 API를 주므로 노드 코드는 무수정.

## 실측 (2026-09-18, SIL, RT 우선순위 없이 — 순정 rt-poll 부팅, 일반 우선순위)

| 항목 | 값 |
|---|---|
| `/interface/can/read/raw` hz | 99.97~100.02 Hz (목표 100) |
| `/control/status/wheel` hz | 99.98 Hz |
| `/control/status/eps` hz | 49.99 Hz (목표 50, 0x710 주기 20 ms) |
| `/control/status/acc` hz | 99.98 Hz (목표 100, 0x711 주기 10 ms) |
| `/control/status/imu` hz | 99.97 Hz (목표 100, 0x713 주기 10 ms) |
| 종단 지연 (`ros2 topic delay`) | 평균 2 ms, 최대 2~4 ms |
| 디코드 정확성 | 실시간 프레임 대조 + `colcon test` 28개(경계값·`cantools` 대조 포함) — **전부 통과** |
| `/diagnostics` (정상 vcan0) | 0x710/0x711 둘 다 level=OK, total_skips=0 |

**A-3 재측정 완료 (2026-09-18) — 결론 확정: 격리 코어·SCHED_FIFO 모두 이 지연을 줄이지 않는다.**

| 조건 | wheel / eps / acc 지연(평균, ms) |
|---|---|
| 격리 없음 | 2 / 2 / 2 |
| `cpu_affinity=8` (FIFO 없음) | 1 / 2 / 2 |
| `cpu_affinity=8` + `rt_priority=80`(권한 없어 FIFO 미적용, `mlockall`만 성공) | 1 / 2 / (미측정) |
| **`cpu_affinity=8` + 진짜 `SCHED_FIFO 80`**(`sudo chrt -f 80 sudo -u ailab ros2 launch ...`, `chrt -p`로 확인) | **1 / 2 / 1** |

네 조건이 사실상 같다 — `chrt -p <pid>`로 실제 `SCHED_FIFO`·우선순위 80을 확인한 뒤 잰 값까지 같으니, 추정이 아니라
**확정**이다. `eait_tx.py`/`eait_rx.py` 가 같은 레시피로 5~9 µs 를 낸 것과는 자릿수가 다르다 → 지배 요인은 DDS
퍼블리시/구독 경로(RMW 직렬화·rclpy 콜백 디스패치)다 — 코어 배치·우선순위로 줄어드는 종류가 아니다.
상세: `can_stack_development.md` §5.C.

```bash
ros2 launch a1_can_bridge status_bridge.launch.py channel:=vcan0 cpu_affinity:=8 rt_priority:=80
# 또는 계정에 rtprio 영구 부여 없이 우선순위만 상속(A-3 재측정에 실제로 쓴 방식):
sudo chrt -f 80 sudo -u ailab bash -c 'source /opt/ros/humble/setup.bash && source ~/git/A1-BSW/ros2_ws/install/setup.bash && \
  ros2 launch a1_can_bridge status_bridge.launch.py channel:=vcan0 cpu_affinity:=8'
```

⚠️ **`ros2 run ... -p cpu_affinity:=8` 처럼 `--ros-args -p` 로 직접 주면 안 된다** — 숫자로 보이는 문자열이 YAML 상
정수로 해석돼 `InvalidParameterTypeException` 이 난다(2026-09-18 재측정에서 실제로 겪음). 반드시 `ros2 launch` 로,
이 프로젝트 launch 파일들은 `ParameterValue` 로 타입을 고정해 뒀다.

## 아직 안 한 것 (다음 단계)

- [x] A-3 레시피 재측정(격리 코어 + 진짜 SCHED_FIFO) — 완료·확정, 위 결과
- [x] `EAIT_INFO_EPS`(0x710)·`EAIT_INFO_ACC`(0x711) 디코더 — E2E(카운터 연속성) 체크 첫 적용, `/diagnostics` 연동 (2026-09-18)
- [x] `EAIT_INFO_IMU`(0x713) 디코더 — Alive_Cnt 없음(E2E 대상 아님), 구조는 `spd_decoder`와 동일 (2026-09-18)
- [ ] 실 `can0` 물리 루프백(체크리스트 5-1)으로 `channel:=can0` 확인 — 이걸로 EAIT 수신 방향은 완결
- [ ] TX 방향: ROS2 제어 명령(MPC 출력) → `EAIT_Control_01/02`(0x156/0x157) 인코더 — **단, §7.2 설계 규칙대로 이 경로는 `can_guard`(Phase D)가 raw SocketCAN 으로 직접 가져가며 ROS2/DDS 를 hot loop 에 넣지 않는다.** 이 워크스페이스의 노드는 상태 퍼블리시(모니터링·로깅·인지 융합용)까지만. `can_guard` 는 A-3 레시피(격리+FIFO)가 실제로 효과 있는 raw SocketCAN 경로로 별도 구현.
