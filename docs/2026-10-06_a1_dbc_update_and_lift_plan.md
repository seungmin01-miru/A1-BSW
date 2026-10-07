# 실차 DBC(A1_dbc.dbc) 반영 + 리프트 송신 시험(C단계) 계획 — 2026-10-06

목표: 오늘 밤 받은 `DBC/A1_dbc.dbc` 기준으로 **can_guard(송신)와 ROS2 브리지(수신)를 실차 프로토콜로 전환**하고,
내일 차량을 **리프트에 올린 상태(바퀴 공중)**에서 우리 PC → 차량으로 기본 명령이 실제로 전달되는지 단계별로
확인한다.

> "C단계" = 리프트 위 송신 시험으로 해석했다. 앞 단계는 A(듣기만, `tools/race_day/VEHICLE_MANUAL.md`),
> B(오늘 밤 vcan0 SIL 리허설)로 둔다.

## 진행 결과 (2026-10-07 06:55)

| 항목 | 상태 | 검증 |
|---|---|---|
| 수정본 DBC `DBC/A1_dbc_fixed.dbc` | ✅ | 원본과 차이: 0x210 조향 배율 0.1→1, 0x200 steer_is_auto 배율 0.1→1 + 근거 주석 |
| can_guard 실차 전환(§1) | ✅ | 유닛테스트 67/67, P-1·P-2 PASS(vcan0, 20 ms) |
| `lift_cmd.py`, `sil/vcan/fake_a1_vehicle.py`(§3) | ✅ | `tools/race_day/rehearse_lift_guard.sh` 31/31 PASS (C0~C7 재현) |
| 비상용 `tools/race_day/lift_tx.py`(가드 없는 단독 송신) | ✅ | `rehearse_lift_tx.sh` 23/23, 유닛테스트 25/25 |
| 매뉴얼 갱신(§5-5) | ✅ | `VEHICLE_MANUAL.md` v3 — "아침 확인", "C. 리프트 송신 시험" |
| 브리지(§2) | ✅ | `a1_status_decoder` + `a1_bridge.launch.py`, colcon test 34(실패 0), 실동작 3토픽 50 Hz |
| A-3 지연 재측정(격리 + FIFO) | ⏸ | sudo 필요 — 20 ms 주기라 여유 큼, 대회 전 재측정 |
| 장시간 SIL soak(새 코드) | ⏸ | 밤사이 API 오류로 세션이 멈춰 못 함 |

**2026-10-07 추가**: 9월 EAIT DBC 와 그에 의존하던 코드(옛 브리지 디코더 4종·메시지·launch, `sil/vcan/eait_tx|rx.py`)를
삭제하고 문서 전체를 A1 기준으로 갱신. Kvaser Leaf v3 지원 추가(`tools/kvaser/` — 설치 스크립트, kv0 미러, 리허설;
can_guard·lift_cmd·lift_tx·가짜 실차에 `--interface kvaser`). 매뉴얼 v4.

리허설에서 잡아 고친 버그 2건:
1. **정지 상태에서 명령이 끊기면 조향이 오래된 값(0°)으로 튐** — 상태머신이 ACTIVE→STOPPED 로 직행할 때 조향
   고정값을 캡처하지 않았다(EAIT 시절엔 STOPPED 가 조향을 놓아서 안 드러남). `command_policy.held_steer_on_transition`
   으로 수정 + 유닛테스트.
2. **여러 줄 입력(붙여넣기)이 첫 줄만 실행** — `select` + `sys.stdin.readline()` 조합의 버퍼 문제. fd 직접 읽기
   (`lift_tx.LineReader`)로 수정 + 회귀 테스트.

결정 필요(§6) 항목은 제안값을 기본값으로 넣고 전부 CLI 로 바꿀 수 있게 했다: `--hold-brake-pct 30`,
`--stopped-mode hold`, `--period 0.020`, 리프트 한계 `--steer-limit-deg 30 --brake-limit-pct 60 --acc-limit-pct 10`.

---

## 0. DBC 검토 결과 (9/17 KF-1600 로그 전체로 대조함)

### 맞는 것
- 0x200 `USER_control_info` / 0x201 `USER_right_wheel_info` / 0x210 `USER_control_command`의 비트 배치가
  우리가 역추적한 것과 **일치**. 로그 223,784프레임 전부 디코드 오류 0, DBC 범위 밖 값 0.
- 0x201은 12비트 필드 2쌍(속도 ×0.1 km/h, rpm) — 9월 말에 정정한 배치 그대로.
- 명령 메시지에 alive counter **없음**(DBC로 확정). AEB·방향지시등 신호 **없음**.

### 🔴 DBC 오류 — 그대로 쓰면 위험
| 위치 | DBC | 실제 | 근거 |
|---|---|---|---|
| 0x210 `steer_command` 배율 | **×0.1** deg | **×1** deg | 명령이 1초 이상 일정했던 1,354개 구간에서 "조향 위치 / 명령 raw" 비율 중앙값 **1.000**(10–90 % 구간 0.97–1.03). 예: raw −148 → 위치 −149.0° |

DBC대로 인코딩하면 **의도한 각도의 10배**로 핸들이 돈다(15°를 보내려다 150°).
→ 코드는 ×1로 직접 인코딩하고, 저장소에는 **수정본 DBC**(`DBC/A1_dbc_fixed.dbc`)를 따로 둔다(원본 보존).
업체에 확인 질문으로도 남긴다.

### 🟡 작은 오타·주의
| 위치 | 내용 | 처리 |
|---|---|---|
| 0x200 `steer_is_auto` | 1비트 신호인데 배율 0.1 → 디코드하면 1이 0.1로 나옴 | 수정본에서 배율 1 |
| 0x200 `break_postion` | DBC는 signed, 범위 0–300 | 영향 없음(값이 양수) |
| 0x113 `wheel_right_size` | 왼쪽 메시지인데 이름이 right | 무시(우리는 SYS 계열 안 씀) |

### 🟡 SYS 계열(0x100, 0x110–0x114, 0x301)
- 9/17 로그에는 **하나도 없다** → 차량 내부 버스(제어기 ↔ 액추에이터) 또는 PC의 다른 단자 쪽으로 추정.
- 우리 송신 대상은 **0x210(USER)** 하나. SYS 계열은 송신하지 않는다.
- ⚠️ **0x301 `SYS_ENCODER_SETTINGS`는 엔코더의 바퀴 크기·좌우 설정을 바꾸는 명령** — 절대 송신 금지.

### 🟡 DBC에 없는 것
- 0x004(약 400 ms 주기, 상수), 0x204(약 12 ms, 값 17종) — 업체 질문 유지.
- 비트레이트 — 현장 listen-only 스캔으로 확인.

### 주기 (9/17 로그 실측)
| ID | 주기 중앙값 | 비고 |
|---|---|---|
| 0x200 | 20.8 ms | |
| 0x201 | 20.8 ms | |
| 0x210 | 21.5 ms(p99 22.3) | 그날 송신자가 **50 Hz**로 안정적으로 보냄 |

→ can_guard 송신 주기를 기존 10 ms에서 **20 ms(관측값)**로 바꾼다(CLI로 조정 가능).

---

## 1. can_guard 변경 (`safety/can_guard/`)

상태머신(`state_machine.py`)·seqlock 구조·A-3 실시간 설정은 **그대로**. 바뀌는 건 메시지 형식과 숫자.

| 파일 | 변경 |
|---|---|
| `protocol.py` | `CommandChannel` 필드를 실차 명령으로 교체: `steer_cmd_deg`(f32), `brake_cmd_pct`(f32), `acc_cmd_pct`(f32), `steer_auto`·`brake_auto`·`acc_auto`(u8). `seq`·`timestamp_ns` 유지 |
| `tx_encode.py` | `encode_0x210(cmd)` — 조향 int16 **×1 deg**, 브레이크 uint16 %, 가속 uint8 %, auto 비트 40·41·42. EAIT 인코더·Aliv_Cnt 제거(명령에 카운터 없음) |
| `rx_decode.py` | 0x201 → 좌우 평균 속도(km/h, 차속 조기 정지용). 0x200 → 조향 위치·auto 비트(로그·진단용) |
| `plausibility.py` | DBC 범위(조향 ±150°, 브레이크·가속 0–100 %) + **리프트 시험용 좁은 한계** CLI: `--steer-limit-deg`, `--brake-limit-pct`, `--acc-limit-pct` |
| `command_policy.py` | INIT: auto 비트 0(수동), 명령 0. HOLDING/DEGRADED: 조향 고정, 가속 → 0 램프, **브레이크 → 유지값 램프**(결정 D-a). STOPPED: 결정 D-a |
| `can_guard.py` | 주기당 0x210 한 번 송신, 기본 주기 20 ms, RX 드레인 대상 0x200·0x201 |
| `test/` | 인코더·디코더를 **수정본 DBC + 9/17 실제 프레임**으로 대조하는 회귀 테스트로 교체 |
| `sil_tests/` | 가짜 제어 노드 필드 갱신, P-1·P-2 재실행 |

EAIT 경로는 옵션으로 남기지 않고 교체한다(이전 코드는 git 기록에 남음) — 분기가 늘면 시험할 경로도 는다.

## 2. ROS2 브리지 변경 (`ros2_ws/`)

| 항목 | 변경 |
|---|---|
| `a1_can_msgs` | 신규 `ControlInfo.msg`(조향 위치, 브레이크 위치, 축별 auto, 축별 alive_ok), `WheelInfo.msg`(좌우 속도·rpm, alive_ok), `ControlCommand.msg`(버스 위 0x210 그대로 — 원격·다른 송신자 감시용) |
| 디코더 | `control_info_decoder`(0x200, 카운터 3개 축별 E2E), `wheel_info_decoder`(0x201, 카운터 2개), `command_monitor`(0x210) |
| 제거 | `eps/acc/imu_decoder`, 기존 msg는 남겨도 무방(미사용) |
| launch | `a1_bridge.launch.py` 신규 |
| `can_raw_bridge` | 변경 없음 |

**리프트 시험에 브리지는 필수가 아니다** — 감시는 `candump | cantools decode`(수정본 DBC)로도 된다. 시간이
부족하면 브리지는 내일 이후로 미룬다.

## 3. 오늘 밤 SIL 도구 (`sil/vcan/`, `safety/can_guard/sil_tests/`)

| 도구 | 역할 |
|---|---|
| `fake_a1_vehicle.py` | vcan0에서 0x200·0x201을 20 ms로 송신(카운터 포함). 0x210을 받으면 auto 비트가 켜진 축만 반응 — 조향 위치는 명령을 1차 지연으로 추종, 브레이크 위치 추종, 바퀴 속도는 가속−브레이크로 단순 적분. **명령이 끊기면 아무것도 안 함**(실차 타임아웃 동작은 모르므로 흉내 내지 않음) |
| `lift_cmd.py` | 리프트 시험용 수동 명령 도구(= 가짜 제어 노드). `auto on`, `steer 5`, `brake 20`, `acc 5`, `zero`, `quit` 같은 한 줄 명령을 받아 `CommandChannel`에 50 Hz로 씀. 입력 한계는 can_guard 한계와 별도로 한 번 더 |

## 4. 내일 리프트 시험(C단계) 절차

### 전제 조건 (하나라도 아니면 송신하지 않는다)
- [ ] 구동륜이 공중에 떠 있다(리프트), 차량 주변에 사람 없음
- [ ] 업체 담당자 입회, **E-stop 위치와 차단 방법 확인**
- [ ] 버스에 0x210을 보내는 다른 장치가 없다(원격 시스템 꺼짐) — C0에서 확인
- [ ] 비트레이트 확정(listen-only 스캔)
- [ ] 녹화 중(`candump -l`), 메모 함수 `m` 준비

### 단계 (각 단계 통과해야 다음으로)
| 단계 | 할 일 | 통과 조건 | 중단 조건 |
|---|---|---|---|
| **C0** 듣기 | listen-only, 0x200·0x201 수신, 0x210이 버스에 있는지 확인 | 0x210 **없음** | 0x210이 보임 → 누가 보내는지 확인·끌 때까지 대기 |
| **C1** 송신 시작(수동 모드) | listen-only 해제, can_guard INIT(auto 0, 명령 0) | 버스 에러 0(`ip -s -d link`), 우리 0x210이 20 ms 간격, 차량 반응 없음, 0x200 auto 비트 그대로 | 버스 에러 증가, bus-off, 차량 경고 |
| **C2** auto 진입, 명령 0 | `lift_cmd`: `auto on`, `zero` | 0x200 auto 비트가 축별로 1, 핸들 중앙 유지 | 핸들이 움직임 |
| **C3** 조향 | +5° → 0 → −5° → 0, 이어서 ±15°, ±30° | 조향 위치가 명령을 **같은 크기로** 추종(±1–2°) | 위치가 명령의 10배 등 배율 이상 → 즉시 `zero` |
| **C4** 브레이크 | 0 → 20 → 50 → 0 % | 브레이크 위치가 따라감 | |
| **C5** 가속(바퀴 공중) | 5 % 2초 → 0, 10 % 2초 → 0 → 브레이크 20 % | 바퀴 속도 상승·하강 | 예상 밖 회전수 |
| **C6** 안전 동작 | (a) `lift_cmd` 강제 종료 → HOLDING(조향 고정, 가속 0, 브레이크 램프) (b) 2초 뒤 STOPPED (c) **can_guard 강제 종료** → 차량 자체 타임아웃 동작 관측 (d) E-stop | (a)(b) 로그 전이 확인, (c) 반응 시간은 로그로 사후 측정 | |
| **C7** 복귀 | `auto off` → can_guard 종료 → listen-only로 되돌림 | 0x200 auto 비트 0 | |

C6 (c)가 체크리스트 §7.1(벤더 fail-safe)·§9.1 B5·P-6를 **실차에서 직접 재는** 첫 기회다.

### 리프트 시험 한계값 (제안)
`--steer-limit-deg 30 --acc-limit-pct 10 --brake-limit-pct 60` — 가드가 이 밖의 명령은 클램프.

---

## 5. 오늘 밤 작업 순서와 예상 시간

| 순서 | 작업 | 시간 | 리프트 시험에 필수? |
|---|---|---|---|
| 1 | 수정본 DBC + 9/17 로그에서 회귀 시험용 프레임 추출 | 20분 | ✅ |
| 2 | can_guard 변경 + 유닛테스트 | 2시간 | ✅ |
| 3 | `fake_a1_vehicle.py` + `lift_cmd.py` | 1시간 | ✅ |
| 4 | vcan0에서 C1–C6 전부 리허설(B단계), P-1·P-2 재실행 | 1시간 | ✅ |
| 5 | 매뉴얼 갱신 — "내일은 송신 금지" 규칙을 "C단계에서만, 전제 조건 통과 후"로 | 20분 | ✅ |
| 6 | 브리지 변경 | 1.5시간 | ❌ (시간 남으면) |
| 7 | A-3 지연 재측정(격리 코어 + FIFO, `sudo chrt`) | 15분 | ❌ (주기 20 ms라 여유 큼) |

필수 합계 약 4시간 40분.

---

## 6. 결정 필요

| # | 결정 | 제안 |
|---|---|---|
| D-a | HOLDING·STOPPED 때 브레이크 값, STOPPED에서 auto 유지 vs 수동 전환 | 브레이크 **30 %로 램프**, STOPPED는 **auto 유지 + 브레이크 유지**(수동으로 넘기면 무인 차량에서 아무도 제어하지 않게 됨). 값은 CLI로 조정 가능하게 |
| D-b | 송신 주기 | **20 ms**(그날 송신자 실측과 동일) |
| D-c | 리프트 시험 한계 | 조향 ±30°, 가속 10 %, 브레이크 60 % |
| D-d | 브리지를 오늘 할지 | 시간 남을 때만. 리프트 감시는 candump + cantools |

## 7. 업체 확인 질문 (추가)
- 0x210 `steer_command` 배율이 DBC(×0.1)와 실측(×1)이 다름 — 어느 쪽이 맞나?
- 0x210 송신이 끊기면 차량은 몇 ms 뒤 무엇을 하나?(카운터가 없으니 수신 간격으로만 판단하는지)
- auto 비트를 0으로 보내면 즉시 수동으로 넘어가나?
- SYS 계열(0x100 등)은 어느 버스에 있나? 0x301은 언제 쓰나?
- 0x004, 0x204는 무엇인가?
