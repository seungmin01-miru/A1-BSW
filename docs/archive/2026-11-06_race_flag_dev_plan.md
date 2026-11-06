# 경기 깃발(Flag) CAN 대응 — BSW 개발 지시서

> **폐기(2026-11-06).** 회의 결정으로 대체됨 → [`../2026-11-06_engine_room_dev_plan.md`](../2026-11-06_engine_room_dev_plan.md). 구현은 태그 `race-flag-full-3309cd1` 에 있다.

작성 2026-11-06 · 대상: Claude Code(개발 실행) + BSW 팀 · 상태: **설계 확정, 구현 전**
근거 자료(저장소 안 위치):
- 규정집: `docs/reference/2026_race/2026_A1_CHALLENGE_RACE_rulebook.pdf` (이하 규정집)
- 대회 배포 DBC·설명: `DBC/A1_dbc_2.dbc`, `DBC/A1_dbc_2_CAN.xlsx` (원본, 수정 금지)
- 대회 측 질의서: `docs/2026-11-06_race_flag_questions.md` / 이아우토반 답변(2026-11-06): 이 문서 1.3절
- 모델 팀 전달 데이터시트: `docs/A1_BSW_토픽_데이터시트_v0.2.xlsx`
- 자료 목록·sha256: `docs/reference/2026_race/README.md`

> 표기: **잠정** = 실측·답변으로 바뀔 값 / ★ = 계산값 / `[ ]` 미착수 · `[~]` 진행 · `[x]` 완료(날짜)

---

## 0. 이 문서를 읽는 Claude Code 에게

1. **먼저 루트 `CLAUDE.md` 를 읽고 1절(안전 규칙과 권한)을 그대로 지킨다.** 이 문서가 그보다 우선하지 않는다.
   - 차량·CAN 에 닿는 명령(`bsw_up.sh`, `can_guard.py` 실차 실행, `cansend` 등)은 실행하지 않는다. SIL(vcan)·pytest 만 자유.
   - `raw/` 수정 금지, 합격 기준 숫자를 임의로 바꾸지 않는다.
2. 작업은 **11절 순서(WP0 → WP8)** 로 한다. 각 WP 끝의 "관문"을 통과하지 못하면 다음 WP 로 넘어가지 말고 멈춰서 보고한다.
3. **커밋은 WP 단위**로 하고, 메시지 첫 줄에 `[race-flag WPn]` 을 붙인다.
4. 이 문서에 없는 판단이 필요하면 **보수적인 쪽(차가 덜 움직이는 쪽)** 으로 구현하고, 그 판단을 `docs/race_flag_open_items.md` 에 적는다.
5. 끝나면 이 문서의 체크박스를 `[x] (날짜)` 로 바꾸고, 각 WP 결과(테스트 수, 실패, 남은 일)를 14절 "작업 기록"에 한 줄씩 남긴다.
6. 이 저장소는 **공개**다. 개인 이름·연락처·대회 비공개 자료 원문을 넣지 않는다.

---

## 1. 배경과 목표

### 1.1 규정 요약 (규정집 쪽수)

| 깃발 | CAN 신호 | 차가 해야 할 일 | 어기면 | 쪽 |
|---|---|---|---|---|
| 대회기 / 녹기(재시작) | **Go** | "일정 시간" 안에 출발. **Go 전에는 조금도 움직이면 안 됨** | 늦은 출발: 원격비상제동 후 재정비 / 먼저 움직임: 자동 원격비상제동·실격 가능 | 14, 15, 19 |
| 황기 | **Slow On** | "일정 시간" 안에 **10 km/h 이하** | 페널티·실격 가능 | 14, 15, 19 |
| 황기 해제 | **Slow Off** | 정상 속도 복귀. **이 신호 전 10 km/h 초과 금지** | 페널티·실격 가능 | 15, 16, 19 |
| 체커기 | **Finish** | 서행으로 **2번~5번 코너 사이 정차 구역**에 완전 정차. 차량 중심이 기준선을 넘어야 인정 | 페널티 | 14, 16 |
| 흑기 | (없음) | 원격비상제동이 작동한 차 표시 | — | 14 |
| 경기 시간 초과 / 중단 | 원격비상제동 | "일정 시간" 안에 정지. 중단 시 팀장이 원격으로 피트 존 이동 → 점검 → 녹기 + Go 로 재개 | — | 16, 17 |

그 밖에 설계에 직접 걸리는 조항:
- **"일정 시간"은 평가위원이 경기 중 판단으로 정하며 바뀔 수 있다**(19쪽) → 고정 숫자가 없다. 설정값으로 만든다.
- **"주행 중 일정 시간 이상 제자리에 멈춰 있을 시" 실격 가능**(19쪽) → 우리 안전 정지(STOPPED)가 실격으로 이어질 수 있다.
- **규정에 적히지 않은 내용은 허가되지 않는 것이 원칙**(1쪽) → 0x203 송신, 점검 중 재시작은 허락 전까지 하지 않는다.
- **대회 당일 브리핑 내용이 규정보다 우선**(10쪽) → 브리핑 확인 항목을 현장 매뉴얼에 넣는다.
- **이의제기는 팀이 증거를 직접 제시**(20쪽) → 깃발·속도·상태 전이를 이벤트 로그로 남긴다.

### 1.2 새 DBC 의 대회 신호 (Classical CAN 500 kbps, Intel, 엑셀 기준 20 ms)

| ID | 이름 | 송신 | 신호 (start bit / 길이) | 의미 |
|---|---|---|---|---|
| **0x124** | KIAPI_Race_Signal | 대회 서버 | `SIG_GO` 7/1, `SIG_STOP` 6/1, `SIG_FINISH` 5/1, `SIG_SLOW_ON` 4/1, `SIG_SLOW_OFF` 3/1 | byte0 예: GO=`0x80`, STOP=`0x40`, FINISH=`0x20`, SLOW_ON=`0x10`, SLOW_OFF=`0x08`. **alive counter 없음** |
| 0x125 | KIAPI_PIT_ZONE | 대회 서버 | `PITZONE_LAT` 0/32, `PITZONE_LONG` 32/32 (×1e-7 °, unsigned) | 피트존 좌표(용도 확인 중) |
| 0x126 | KIAPI_REPAIR | 대회 서버 | `SIG_REPAIR` 0/8 | 뜻 미정의 → 기록만 |
| **0x127** | KIAPI_RES | 대회 서버 | `RES_START` 7/1, `alive_count` 56/8 | 원격비상제동 작동 |
| 0x128 | KIAPI_RES_FB | 차량(이아우토반) | `RES_END` 7/1, `RES_ERROR` 0/3 | 제동 완료 / 실패 — **우리는 기록만** |
| 0x202 | USER_service_info | 차량 | `speed_limit_info` 0/10 (×0.1 km/h) | 현재 차량 속도 제한 |
| **0x203** | USER_service_command | **우리** | `speed_limit` 0/10 (×0.1 km/h, 0~100) | 차량 속도 제한 설정 |
| 0x156/0x157 | KIAPI_Control_01/02 | 대회 원격 앱 | (기존 RemoteEstopDetector 와 같음) | 이제 DBC 에 정식 정의 |
| 0x710~0x712 | KIAPI_FB_* | 차량 | 축 모드, `EPS/ACC_USER_CAN_ERR`, `VS`, 휠속 | 관제용. `*_USER_CAN_ERR` 은 TODO-2 확인에 활용 |

### 1.3 이아우토반 답변 (2026-11-06)

| 질문 | 답변 | 반영 |
|---|---|---|
| RES 중 제동 주체 | **차량이 스스로 제동** | 우리는 감지 + 래치(풀리는 순간 대비) |
| RES 중 0x210 | **무시됨** | 위와 같음 |
| 0x128 송신 | KIAPI ↔ 이아우토반끼리. 학교는 수신만 하거나 무시 | 기록만 |
| RES_ERROR | 제동 실패 신호. 학교 측 조치 없음 | 기록만 |
| RES 해제 | KIAPI 가 RES_START 를 0으로 내리면 풀림 | 풀린 뒤에도 Go 전까지 출발 대기 |
| 0x127 alive 끊김 | 정의 없음. 멈추는 게 좋겠다 | 한 번 본 뒤 끊기면 정지 |
| 신호 버스 | **차량 제어 CAN 과 같은 버스** | 수신 채널 추가 없음 |
| 0x203 효과 | **실제로 제한됨**, 0x202 로 확인 가능 | 서행 보조 수단 |
| 0x203 주기 | 크게 중요치 않음. **0x202 에 반영되면 송신을 끊어도 유지** | 확인 기반 송신 + 시작 점검 |

### 1.4 목표

- 규정의 깃발 신호에 **규정 위반 없이** 반응한다(실격 사유 0).
- 모델(제어) 팀 코드에 기대지 않고 **시작·서행은 BSW 가 강제**한다. Finish 뒤 정차는 모델이 맡는다.
- 기존에 검증된 안전 로직(ESTOP·HOLDING·STOPPED)을 깨지 않는다.
- 서행 진입·복귀 프로파일은 **실차 로그로 계속 개선**한다(성적 직결).

---

## 2. 확정된 설계 결정

| # | 항목 | 결정 | 이유 |
|---|---|---|---|
| D1 | 서행 보장 | **can_guard 가 서행 중 종방향(가속·브레이크)을 넘겨받는다.** 조향은 모델 값. 0x203 은 허용 시 보조 | 모델 코드를 바꿀 수 없음. 안전 바닥값은 BSW 가 보장 |
| D2 | 출발 | **arm 은 자동(기존), 실제 움직임은 can_guard 의 Go 잠금이 막는다.** 모델은 Go 전에도 아무 값이나 계속 발행 | 모델 수정 불필요, Go 후 20 ms 안에 전달 → 출발 지연 최소 |
| D3 | 짧은 끊김 복구 | **"이탈 예산" 0.30 m 안에서만 복구 허용**(고속에서 약 0.2 s). 복구 구간은 관성 주행. 넘으면 정지 확정. 저속 재개는 조건부·기본 꺼짐 | 0.15 s(완전 안전)와 0.30 s(복구 한계)의 중간을 보수적으로 |
| D4 | RES | **ESTOP 원천으로 추가: 브레이크 + 래치**. 조향은 실제 핸들각을 따라감 | 차량이 제동하는 동안은 영향 없고, 풀리는 순간 모델 가속으로 튀어 나가는 것을 막음 |
| D5 | 경기 상태 기계 위치 | **can_guard 안**(격리 코어). 위치 정보는 BSW 로 받지 않음 | CAN 만으로 판단 가능. BSW 가 판단 계층으로 올라가지 않음 |
| D6 | 모델 인터페이스 | 모델 → BSW(`/control/target`)는 **그대로**. **BSW → 모델 `/race/state` 를 새로 제공(필수)**. Finish 정차는 모델 담당 | 데이터시트 v0.2 로 전달함 |
| D7 | 입력 한계 ±15 m/s² | **can_guard 가 명령을 무시하는 상태(출발 대기·서행·중단·정차 완료)와 Go·Slow Off 직후 1 s 에는 래치하지 않고 잘라서 쓴다.** NaN·inf 는 항상 래치 | 모델 적분 누적으로 Go 순간 큰 값 → 출발 즉시 정지되는 것을 막음 |
| D8 | 서행 속도 | **1단계 목표 8.0 / 가속 차단 8.8 / 브레이크 9.3 km/h.** 데이터 조건을 넘으면 단계 상향(6.3절) | 10 km/h 초과는 실격 가능 — 비대칭 위험 |
| D9 | 가감속 프로파일 | **로그 기반 추천 도구 + 오염 검사. 검사 실패 시 갱신 중단·알림. 반영은 사람 승인** | 성적 직결 + 잘못된 데이터 반영 방지 |
| D10 | 0x203 | **can_guard 단독 송신, 확인 기반 송신, 시작 점검. 송신 기본 꺼짐(KIAPI 허용 전), 읽기 점검은 항상** | 차량에 값이 남는 특성 대응 |
| D11 | 출발 대기 중 모델 끊김 | **래치를 자동 해제하고 다시 arm**(정상 입력 1 s 뒤). 상태판에 모델 정상 여부 표시 | 차가 서 있고 Go 전이면 위험 없음. 대기 중 모델을 계속 점검 |
| D12 | 신호 확인 | 깃발 비트는 **2프레임 연속**이어야 인정(Slow Off 포함) | 오신호로 서행 중 가속 → 실격 위험 > 20 ms 이득 |
| D13 | 브레이크 단위 | **BSW 내부(can_guard·문서)는 % 로 통일.** ROS `ControlInfo.brake_position` 은 **0~10 그대로 유지**(모델 팀 영향 없음) | 같은 raw 값의 표기 차이일 뿐(실측 82 % ↔ 8.2 일치) |
| D14 | Slow Off 해제 지연 | 1 s 는 **기록용 표시**(경보 아님). 재시도는 계속, 동작에 영향 없음 | 경기 중엔 사람이 개입할 수 없음 |

---

## 3. 전체 구조

### 3.1 두 축 상태 — "더 엄격한 쪽"을 따른다

| 축 | 상태 | 담당 | 파일 |
|---|---|---|---|
| 안전 상태(기존) | INIT / ACTIVE / HOLDING / DEGRADED / STOPPED / ESTOP | 고장 대응 | `state_machine.py` (수정 최소화) |
| 경기 상태(신규) | PRE_GO / RACING / FINISHED / PARKED / SUSPENDED + `slow_active` 플래그 | 규정 대응 | `race_state.py` (신규) |

최종 0x210 = `command_for_state()`(기존) 결과에 `apply_race_envelope()`(신규)를 씌운 값. 두 축이 각각 정한 제한 중 **더 엄격한 쪽**이 이긴다.
(예: 안전 = ACTIVE 여도 경기 = SLOW 면 서행 제어가 이김. 경기 = RACING 이어도 안전 = ESTOP 이면 ESTOP 이 이김.)

`/race/state.phase` 표시값: `PRE_GO=0, RACING=1, SLOW=2(= RACING + slow_active), FINISHED=3, PARKED=4, SUSPENDED=5`.

### 3.2 데이터 흐름

```
                        ┌──────────────────────── can_guard (격리 코어 8, FIFO 90) ────────────────────────┐
모델 ─/control/target─▶ 어댑터 ─CommandChannel(shm)─▶ │ 안전 상태기계 ─┐                                         │
                                  ▲               │ 경기 상태기계 ─┴▶ 명령 + 경기 제한 ─▶ 0x210 (20 ms)          │─▶ 차량
                                  │               │ 0x203 관리자 ───────────────────────▶ 0x203 (확인 기반)    │
                                  │               │ RX: 0x124 0x127 0x202 0x200 0x201 0x156/157 0x128(기록)   │
                                  │               └───────────────┬──────────────────────────────────────┘
                                  │                     StatusChannel(shm, 신규)
                                  └── 경기 상태 읽기 ──────────────┤
                                                                  ▼
                                               race_state_publisher(ROS, 신규) ─▶ /race/state ─▶ 모델
```

- **경기 상태를 판단하는 곳은 can_guard 하나뿐**이다. 어댑터·ROS 는 `StatusChannel` 을 읽기만 한다(해석이 둘로 갈리지 않게).
- can_guard 는 지금처럼 ROS 에 의존하지 않는다.

### 3.3 파일 목록

| 구분 | 경로 | 내용 |
|---|---|---|
| 신규 | `DBC/A1_dbc_2_fixed.dbc` | 새 기준 DBC (WP0) |
| 신규 | `safety/can_guard/race_signal.py` | 0x124·0x127·0x125·0x126·0x128·0x202 해석, `RaceSignalDecoder`, `ResMonitor` |
| 신규 | `safety/can_guard/race_state.py` | 경기 상태 기계(순수 함수) |
| 신규 | `safety/can_guard/race_policy.py` | `apply_race_envelope()` — 상태별 명령 제한(순수 함수) |
| 신규 | `safety/can_guard/speed_ctrl.py` | 서행용 PI 속도 제어기(`bsw_test/02_LON_profile` 의 SpeedController 를 의존성 없이 옮김) |
| 신규 | `safety/can_guard/speed_limit.py` | 0x203 관리자(순수 상태기계) |
| 신규 | `safety/can_guard/recovery.py` | 이탈 예산 계산, 복구·정지 확정 판정 |
| 신규 | `safety/can_guard/race_flag.yaml` | 학습되는 가감속 프로파일 값(주석에 갱신 절차) |
| 신규 | `safety/can_guard/test/test_race_*.py` 등 | 단위 시험 (WP별) |
| 신규 | `sil/vcan/fake_race_server.py`, `sil/vcan/race_scenarios/*.yaml` | 가짜 대회 서버 |
| 신규 | `safety/can_guard/sil_tests/r_race.py` | 경기 시나리오 SIL 실행기 |
| 신규 | `ros2_ws/src/a1_can_msgs/msg/RaceState.msg` | `/race/state` 메시지 |
| 신규 | `ros2_ws/src/a1_can_bridge/a1_can_bridge/race_state_publisher.py` | StatusChannel → `/race/state` |
| 신규 | `tools/race_flag/fit_long_profiles.py`, `tools/race_flag/README.md` | 프로파일 추천 도구 |
| 신규 | `docs/race_flag_requirements.md`, `docs/race_flag_profile_history.md`, `docs/race_flag_open_items.md` | 요구사항·이력·미결 |
| 수정 | `safety/can_guard/rx_decode.py`, `tx_encode.py`, `protocol.py`, `can_guard.py`, `state_machine.py`, `command_policy.py` | 연결 |
| 수정 | `ros2_ws/src/a1_control_adapter/a1_control_adapter/cmd_adapter.py`, `config/track.yaml` | D3·D7·D11 |
| 수정 | `sil/vcan/fake_a1_vehicle.py` | 0x202/0x203·RES 동작 흉내 |
| 수정 | `tools/bsw/bsw_up.sh`, `bsw_monitor.py`, `README.md` | `--race`, 상태판 |
| 수정 | `CLAUDE.md`, `README.md`, `safety/can_guard/README.md`, `bsw_test/FIELD_MANUAL.md` | 문서 |

---

## 4. 경기 상태 기계 상세 (`race_state.py`)

### 4.1 입력

| 입력 | 출처 | 비고 |
|---|---|---|
| 깃발 이벤트 `go / slow_on / slow_off / finish / stop` | `RaceSignalDecoder` | **2프레임 연속 확인 뒤 올라가는 순간(edge)** 을 이벤트로 냄 |
| 깃발 현재값(level) | 같음 | 기록·표시용. 판단은 이벤트 기준 |
| `res_active`, `res_alive_lost` | `ResMonitor` | |
| `race_signal_age_s` | 디코더 | 0x124 마지막 수신 후 경과 |
| `vehicle_speed_kph` | 0x201 좌우 평균 | 나이 0.1 s 초과면 None |
| `now` | 호출자 | 순수 함수라 시각을 받음 |

### 4.2 전이 규칙

**신호 방식 미확정(KIAPI 질문 2)에 대한 원칙:** 깃발 비트가 계속 켜져 있든 한 번만 켜지든 같은 결과가 나오도록 **올라가는 순간을 이벤트로 받고, 상태는 래치**한다. 비트가 내려가는 것만으로는 상태를 되돌리지 않는다(보수적).

| 현재 | 이벤트 | 다음 | 비고 |
|---|---|---|---|
| PRE_GO | go | RACING | `slow_active` 가 이미 참이면 RACING + 서행 |
| PRE_GO | slow_on | PRE_GO (`slow_active=True`) | Go 전에 온 Slow On 은 기억해 둠 |
| RACING | slow_on | RACING (`slow_active=True`) | |
| RACING (`slow_active`) | slow_off | RACING (`slow_active=False`) + **복귀 램프 시작** | |
| RACING | finish | FINISHED | `slow_active` 는 유지(서행 중 Finish 면 둘 다 적용) |
| FINISHED | 차속 ≤ `parked_speed_kph` 가 `parked_after_s` 동안 이어짐 | PARKED | 래치 |
| (모든 상태) | `res_active` 또는 `res_alive_lost` | SUSPENDED | 래치. 안전 축은 ESTOP(원천 `race_res`) |
| (모든 상태) | stop (`race_stop_action=estop`, 기본) | SUSPENDED | KIAPI 질문 5 답에 따라 바뀔 수 있음 |
| SUSPENDED | — | SUSPENDED | `resume_mode=restart`(기본): **can_guard 재시작으로만** 빠져나가고 재시작 후 PRE_GO |
| SUSPENDED | RES=0, STOP=0, 차속 ≤ 정지 문턱, 그 뒤 **새 go 이벤트** | RACING | `resume_mode=go-reset` 일 때만(KIAPI 질문 7 답 대기, 기본 꺼짐) |

**같은 주기에 이벤트 여러 개**(KIAPI 질문 5 답 전 기본 우선순위, 위가 먼저):
`stop` > `finish` > `slow_on` > `slow_off` > `go`.
`slow_on` 과 `slow_off` 가 동시에 오면 **slow_on**(보수적). 정의에 없는 조합은 이벤트 로그에 `race_signal_unknown` 으로 남긴다.

**0x124 끊김**(KIAPI 질문 6 답 전): `race_signal_timeout_s = 0`(끔) — 기록만 한다(`race_signal_lost`). 값을 주면 그 시간 뒤 SUSPENDED.

### 4.3 출력 — 경기 제한(`RaceLimits`)

| 필드 | 뜻 |
|---|---|
| `allow_motion` | False 면 가속 0 + 브레이크 유지 |
| `long_override` | True 면 can_guard 가 종방향을 직접 계산(모델 가감속 무시) |
| `speed_target_kph` | override 때 목표 속도 |
| `speed_cap_kph` | 넘으면 가속 차단(+ 0.5 넘으면 브레이크). None = 없음 |
| `acc_ramp_active`, `acc_ramp_start_pct` | Slow Off 복귀 램프 |
| `hold_brake_pct` | 정지 유지 브레이크 |
| `input_grace` | D7: 입력 한계 래치 유예 중인지 |

---

## 5. 상태별 BSW 동작 (`race_policy.py` · `can_guard.py`)

| 경기 상태 | 조향 | 가속 | 브레이크 | auto 비트 | 기타 |
|---|---|---|---|---|---|
| **PRE_GO** | 0x200 실제 핸들각 유지 | **0** | `pre_go_brake_pct`(잠정 30 %) | 어댑터가 arm 했으면 111 유지 | arm 전에는 기존 INIT silent(차량 페일세이프 브레이크) |
| **RACING** | 모델 | 모델 | 모델 | 모델(arm) | 그날 클램프만. **Go 직후 1 s 입력 유예(D7)** |
| **RACING + slow_active** | 모델 | **서행 제어** | **서행 제어** | 111 | 6절 |
| **Slow Off 직후** | 모델 | 모델, 단 **가속 상한을 램프로 올림** | 모델 | 111 | 램프 값은 `race_flag.yaml`. 입력 유예 1 s |
| **FINISHED** | 모델 | 모델, 단 `finish_cap_kph`(잠정 20) 넘으면 차단 | 상한 + 0.5 넘으면 브레이크 | 111 | 정차 구역 이동·정지는 모델 |
| **PARKED** | 유지 | 0 | `parked_brake_pct`(잠정 60) | 111 유지(**auto 000 금지**) | 래치 |
| **SUSPENDED** | RES 중: 0x200 실제 핸들각 따라감(`follow`) | 0 | ESTOP 프로파일(30 → 40 %/s → 82 %) | 111 유지 | 안전 축 ESTOP 과 같음 |

**"Go 전 움직임" 감시:** PRE_GO 에서 0x201 차속 > `stop_speed_kph` 이면 이벤트 `pre_go_motion` + 상태판 경고(동작은 이미 정지 유지라 추가 조치 없음).

---

## 6. 서행 제어 상세

### 6.1 동작

```
서행 시작(slow_on 이벤트)
  ├─ 차속 > slow_brake_kph(9.3)   → 진입 감속: 목표 감속도 a_slow 로 브레이크 (% 는 race_flag.yaml 의 감속표)
  │                                   차속 ≤ slow_target + 0.5 가 되면 아래로
  ├─ 정상 서행                     → PI 속도 제어(목표 slow_target_kph 8.0), 출력은 가속 % 또는 브레이크 %
  └─ 안전 한계(항상 위에 덮어씀)
       차속 ≥ slow_acc_cut_kph(8.8)  → 가속 0
       차속 ≥ slow_brake_kph(9.3)    → 브레이크를 hold_brake_ramp 로 올림, slow_acc_cut 아래로 내려오면 해제(히스테리시스)
```

- 속도 원천: 0x201 좌우 평균(후륜). 가속 중 슬립으로 **실제보다 크게** 읽히므로 안전한 쪽 오차다.
- PI 초기값: 11-04 실차 6 km/h 추종에 쓴 kp 0.8, ki 0.2, ff 0.2 m/s²(잠정) → % 변환은 `track.yaml` accel/brake 표와 같은 값을 `race_flag.yaml` 에 복사해 쓴다(can_guard 는 track.yaml 을 읽지 않는다).
- 적분 누적 방지(anti-windup)와 출력 상한을 **반드시** 넣는다.
- 0x203(허용 시) 값 = `slow_brake_kph`.

### 6.2 진입 감속 시간 참고 (★)

| 진입 속도 | a_slow 2.0 m/s² | a_slow 3.0 m/s² |
|---|---|---|
| 30 → 9 km/h | 2.9 s | 1.9 s |
| 50 → 9 km/h | 5.7 s | 3.8 s |

"일정 시간"(KIAPI 질문 4)이 오면 `a_slow` 하한을 정한다. 초기 `a_slow = 2.0`(잠정).

### 6.3 서행 속도 단계 (D8)

| 단계 | 목표 / 가속 차단 / 브레이크 (km/h) | 올라가는 조건 (전부 만족, `fit_long_profiles.py` 가 판정) |
|---|---|---|
| **1 (시작)** | 8.0 / 8.8 / 9.3 | — |
| 2 | 8.5 / 9.2 / 9.5 | 서행 로그 3회 이상 · 정속 흔들림 p99.9 ≤ 0.5 km/h · 휠속-GNSS 차이 ≤ 2 % |
| 3 | 9.0 / 9.4 / 9.6 | 위 + 흔들림 ≤ 0.4 · 차이 ≤ 1 % · **KIAPI 질문 8(판정 기준·허용 오차) 답변** |

단계 변경은 **사람 승인 후** `race_flag.yaml` 을 고친다. 참고: 8 → 9 km/h 는 서행 300 m 당 약 15 s 이득(★).

### 6.4 Slow Off 복귀 램프

- Slow Off 이벤트 순간의 가속 % 에서 시작해 **`slow_off_ramp_pct_s` %/s 로 가속 상한을 100 % 까지** 올린다. 초기 50 %/s(잠정, 2 s 에 100 %).
- 이 차는 가속 상한 자체가 약해서(최대 약 1.3 m/s²) 휠스핀이 거의 없을 수 있다 → 데이터가 확인되면 램프를 짧게(또는 없애게) 만드는 것이 목표다(7절).
- 측정 지표: "Slow Off → 레이싱 속도 도달 시간", "최대 슬립률".

---

## 7. 가감속 프로파일 갱신 장치 (D9) — `tools/race_flag/fit_long_profiles.py`

### 7.1 하는 일

| 대상 | 배우는 값 | 추천 규칙 |
|---|---|---|
| 감속(서행 진입) | 브레이크 % → 실제 감속도 표, 바퀴 잠김 시작 %, 브레이크 지연 | `a_slow` = 잠김 직전 감속도 × 0.8 안에서 가장 센 값 |
| 가속(Slow Off 복귀) | 가속 % → 실제 가속도, 휠스핀 시작 % | `slow_off_ramp_pct_s` = 슬립 ≤ 10 % 를 지키는 가장 빠른 값(무한 = 램프 없음 가능) |
| 서행 단계 | 정속 흔들림, 휠속-GNSS 차이 | 6.3절 조건 판정 |

- 입력: CAN 로그(Kvaser `--log` / `candump -l` 형식) + GNSS bag(기본 토픽 `/gsof_client/odom`, TODO-5 확정 시 변경).
- 슬립률 = (후륜 속도 − GNSS 속도) ÷ GNSS 속도.
- 출력: `reports/race_flag/YYYY-MM-DD_profile_fit/`(추천값, 근거 그래프, 사용 구간 목록) + `docs/race_flag_profile_history.md` 에 한 줄 추가.
- **자동 반영하지 않는다.** 사람이 승인하면 `race_flag.yaml` 을 고친다.

### 7.2 데이터 오염 검사 — 하나라도 걸리면 갱신 중단

| 검사 | 기준(잠정) |
|---|---|
| GNSS 품질 | 분석 구간의 RTX fix 비율 < 90 % |
| 다른 조작 섞임 | 앱 0x156 En=1, ESTOP, RES, 0x210 다른 송신자(`foreign_0x210`) 구간 포함 |
| 물리 한계 | 감속 > 4.5 m/s², 가속 > 1.6 m/s²(실측 최대 4.1 / 1.43) |
| 센서 불일치 | 정속 구간 휠속-GNSS 차이 > 5 % |
| 시간 어긋남 | CAN 로그와 bag 의 시각 차이를 확인할 수 없거나 0.05 s 초과 |
| 표본 부족 | % 구간별 표본 < 3 이벤트 |
| 경사 | INS 피치 절대값 > 2° 구간 |
| 급변 | 새 추천값이 기존 값보다 20 % 넘게 바뀜 → **자동 판정 금지, 사람 확인 요청** |

검사 실패 시:
1. 기존 값 그대로 둔다.
2. `reports/race_flag/.../UPDATE_BLOCKED.md` 에 실패한 검사·구간·숫자를 적는다.
3. **종료 코드 3** 으로 끝나고 화면에 `⛔ 갱신 중단: <이유>` 를 출력한다.

### 7.3 `race_flag.yaml` 머리 주석 (그대로 넣을 것)

```yaml
# 경기 깃발 대응 가감속 프로파일 — can_guard 가 시작할 때 읽는다.
# ⚠ 이 값들은 대회 성적에 직결된다. 손으로 고치지 말고 아래 절차를 따른다.
# 갱신 절차:
#   1) 주행 로그(CAN + GNSS bag)가 생길 때마다 실행:
#        python3 tools/race_flag/fit_long_profiles.py --can <로그> --bag <bag> --out reports/race_flag/<날짜>_profile_fit
#   2) 종료 코드 3(⛔ 갱신 중단)이면 UPDATE_BLOCKED.md 를 보고 원인을 해결하기 전까지 값을 바꾸지 않는다.
#   3) 추천값과 근거 그래프를 사람이 확인·승인한 뒤에만 이 파일을 고친다.
#   4) 고친 뒤 docs/race_flag_profile_history.md 에 날짜·로그·이전값→새값·승인자를 적는다.
```

---

## 8. 짧은 끊김 복구 (D3) — `recovery.py`, `state_machine.py`, `cmd_adapter.py`

### 8.1 이탈 예산

조향이 고정된 동안 옆으로 벗어날 수 있는 최악 거리:

```
d(t_gap) = ½ · a_err · (t_gap + 0.25)²          0.25 s = 조향 무반응 0.175 s + 재보정 여유
a_err    = min(3.0, v² / R_min)                  3.0 m/s² = 관측 최대 횡가속(10-08) 근처
예산      = 0.30 m
```

| 조건 | 복구 허용 끊김 시간(★) |
|---|---|
| `R_min` 모름(기본) → a_err = 3.0 | **약 0.20 s** (속도와 무관) |
| `R_min = 20 m` 가정, 9 km/h | 약 1.1 s |

`R_min` 은 모델 팀 질문 5-1 답으로 `--track-min-radius-m` 에 넣는다. 그 전에는 None(= 0.2 s 고정).

### 8.2 단계

| 구간 | 조건 | 조향 | 가속 | 브레이크 | 빠져나가는 길 |
|---|---|---|---|---|---|
| 정상 | 모델 입력 신선 | 모델 | 모델 | 모델 | — |
| **복구 구간**(HOLDING) | 끊김, 이탈 예산 이내 | 고정 | 0 | **0 (관성 주행)** | 신선한 입력 3주기(60 ms) 연속 → ACTIVE |
| **정지 확정**(HOLDING, committed) | 예산 초과 | 고정 | 0 | 30 % → 20 %/s → 60 % | (저속 재개 옵션만, 8.3) |
| STOPPED | 정지 확정 후 차속 ≤ 3 km/h | 고정 | 0 | 유지 | 재시작만 |

바꿀 곳:
- `state_machine.py`: HOLDING 에 `committed` 개념 추가. **"차속 ≤ 3 km/h → STOPPED" 는 committed 뒤에만** 적용(출발 직후 저속 끊김에 영구 정지되는 문제 수정). 기존 `--stopped-after` 는 committed 이후 시간 상한으로 의미를 바꾼다.
- `command_policy.py`: 복구 구간은 브레이크 0, committed 부터 기존 hold-brake 램프.
- `cmd_adapter.py`:
  - 입력이 `hold_after_s`(기본 0.10 s, **모델 주기의 2배 이상**이어야 함 — 모델 팀 질문 1-1) 넘게 끊기면 **기록만 멈춘다**(래치하지 않음) → can_guard 가 판정.
  - 다시 신선한 입력이 오면 기록 재개. can_guard 가 committed 면 재개해도 ACTIVE 로 가지 않는다(can_guard 가 결정권).
  - 끊김만으로 래치하는 기존 `stale_timeout_s` 동작은 **경기 모드에서만 끈다**(래치는 NaN·inf 와 D7 범위 밖일 때만).
  - **적용 조건:** StatusChannel 이 있고 `race_enabled = true` 일 때만 위 규칙을 쓴다. `--race` off 이거나 StatusChannel 이 없거나 오래되면 **기존 150 ms 래치 그대로**.
  - can_guard 는 끊긴 시간을 `cmd_age + hold_after_s` 로 계산한다(어댑터가 기록을 멈춘 시점이 모델 마지막 입력보다 `hold_after_s` 늦기 때문). `hold_after_s` 는 can_guard CLI `--adapter-hold-after-s` 로도 같은 값을 받는다.
- `state_machine.py`·`command_policy.py` 의 committed·관성 주행·"≤ 3 km/h 는 committed 뒤에만" 변경도 **`--race` 일 때만** 적용한다.

### 8.3 저속 재개 옵션 (`--resume-low-speed`, 기본 off)

정지 확정 구간에서도 아래를 **모두** 만족하면 ACTIVE 로 돌아갈 수 있다:
1. 끊김 시작부터 지금까지의 최악 이탈 `d` ≤ 0.30 m (끊김 동안 최고 속도로 a_err 계산)
2. 모델 입력이 0.5 s 연속 정상
3. ESTOP·RES·SUSPENDED 아님
4. 이번 실행에서 재개 횟수 < 2 (세 번째 끊김은 무조건 정지)
5. 재개 뒤 3 s 동안 속도 상한 20 km/h

SIL 과 저속 실차(`kill -STOP` 시험) 통과 후 사람이 켠다.

### 8.4 출발 대기 중 끊김 (D11)

- PRE_GO 에서는 안전 축의 HOLDING → STOPPED 전이를 막는다(차가 서 있고 PRE_GO 가 이미 정지 유지).
- 어댑터가 래치돼 있어도 PRE_GO 이고 차속 0 이면 **정상 입력 1 s 뒤 자동 해제 + 다시 arm**.
- `StatusChannel` 에 `model_healthy_s`(연속 정상 시간)를 넣어 상태판·`/race/state` 에 표시한다.

---

## 9. 0x203 속도 제한 관리자 (D10, D14) — `speed_limit.py`

### 9.1 두 스위치

| 스위치 | 기본 | 켜졌을 때 | 꺼졌을 때 |
|---|---|---|---|
| 읽기 점검 | **항상 켬** | 0x202 를 읽어 기대값과 비교 | — |
| `--speed-limit-tx` | **off**(KIAPI 10-1 허용 전) | 9.2~9.4 의 전체 흐름 | 아무것도 보내지 않음. 시작 점검에서 남은 제한이 보이면 **고치지 않고 시작 거부** |

### 9.2 시작 점검 (can_guard 시작 전 점검 단계에 넣는다)

```
0x202 읽기 → 기대값(경기 모드 = 해제값, 연습 = practice 값)과 같으면 통과
다르면:  tx on  → 0x203 으로 고치고 최대 2 s 대기 → 맞으면 통과 / 아니면 시작 거부
         tx off → 시작 거부 + "차량에 X km/h 제한이 남아 있음 (--speed-limit-tx on 으로 해제 필요)"
0x202 가 아예 안 보이면 → 경기 모드에서는 시작 거부(--speed-limit-allow-missing 으로만 통과)
시작 거부 종료 코드: EXIT_SPEED_LIMIT = 5
```

### 9.3 값 바꾸기 (Slow On / Slow Off / 시작)

```
목표값 정함 → 0x203 을 20 ms 마다 송신 → 0x202 에 목표값이 3프레임 연속 → "반영 완료"
반영 완료 뒤 → 100 ms 마다 같은 값 송신(보험) + 0x202 매 프레임 감시
0x202 가 목표와 0.3 s 넘게 다름 → 다시 20 ms 송신 + 이벤트 speed_limit_mismatch
Slow Off(해제) 1 s 안에 반영 안 됨 → 이벤트 speed_limit_release_slow(기록용 표시). 재시도는 계속
```

| 숫자 | 역할 |
|---|---|
| 20 ms | 바꾸는 중 송신 주기(차량 신호 주기와 같은 빠르기) |
| 3프레임 | 반영 완료 판정(약 60 ms) |
| 100 ms | 반영 뒤 보험 송신(차량 제어기가 값을 잊어도 0.1 s 안에 다시 걸림, 버스 부하 약 0.3 % ★) |
| 0.3 s | 어긋남 판정(약 15프레임 연속 불일치) |
| 1 s | 해제 지연 기록 기준(정상이면 닿지 않음) |
| 2 s | 시작 점검 대기 상한 |
| 비교 | raw 값(0.1 km/h 단위)이 같으면 일치 |

- **송신자는 can_guard 하나뿐.** 실행 중 다른 송신자의 0x203 이 보이면 이벤트 `foreign_0x203`.
- 해제가 실패해도 can_guard 속도 제어는 정상 동작한다(차량 제한 때문에 속도만 못 올림).
- 업체 확인 대기: **해제값**(100 인지 0 인지), 제한보다 빠를 때 차량이 브레이크를 거는지, 차량이 쓰는 속도 기준.

---

## 10. 파라미터 총표

can_guard CLI(→ `bsw_up.sh --race` 가 묶어서 넘김). `race_flag.yaml` 표시 항목은 학습값.

| 옵션 | 기본 | 상태 | 근거 / 바뀌는 조건 |
|---|---|---|---|
| `--race` | off | 확정 | 경기 상태 기계 켜기. 끄면 지금과 완전히 같게 동작(하위 호환) |
| `--race-confirm-frames` | 2 | 확정 | D12 |
| `--race-stop-action` | estop | 잠정 | KIAPI 5 |
| `--race-signal-timeout-s` | 0 (끔) | 잠정 | KIAPI 6 |
| `--resume-mode` | restart | 잠정 | KIAPI 7 |
| `--res-alive-timeout-s` | 0.5 | 잠정 | 업체 1-6 |
| `--pre-go-brake-pct` | 30 | 잠정 | 10·20·30 % 풀림 시간 실측 후 최소값 |
| `--slow-target-kph` / `--slow-acc-cut-kph` / `--slow-brake-kph` | 8.0 / 8.8 / 9.3 | 1단계 | 6.3절 |
| `--finish-cap-kph` | 20 | 잠정 | KIAPI 8-3 |
| `--parked-speed-kph` / `--parked-after-s` / `--parked-brake-pct` | 0.5 / 5 / 60 | 잠정 | |
| `--input-grace-s` | 1.0 | 확정 | D7 |
| `--recover-budget-m` | 0.30 | 확정 | D3 |
| `--recover-delay-s` | 0.25 | 잠정 | 조향 무반응 실측 |
| `--recover-alat-max` | 3.0 | 잠정 | 10-08 최대 횡가속 |
| `--track-min-radius-m` | None | 잠정 | 모델 팀 5-1 |
| `--resume-low-speed` | off | 확정 | 8.3 |
| `--speed-limit-tx` | off | 잠정 | KIAPI 10-1 |
| `--speed-limit-release-kph` | **미정** | 대기 | 업체 추가 질문 |
| `--speed-limit-practice-kph` | 30 | 확정 | 시험 상한 규칙 |
| `--speed-limit-fast-period` / `--speed-limit-keep-period` | 0.02 / 0.1 | 확정 | 9.3 |
| `--speed-limit-confirm-frames` | 3 | 확정 | 9.3 |
| `--speed-limit-mismatch-s` / `--speed-limit-release-note-s` / `--speed-limit-startup-timeout` | 0.3 / 1.0 / 2.0 | 확정 | 9.3 |
| `a_slow` (yaml) | 2.0 m/s² | 학습 | 7절 |
| `slow_off_ramp_pct_s` (yaml) | 50 %/s | 학습 | 7절 |
| 감속표·가속표 (yaml) | track.yaml 값 복사 | 학습 | 7절 |
| 서행 PI kp / ki / ff (yaml) | 0.8 / 0.2 / 0.2 | 잠정 | 11-04 |

어댑터(`track.yaml`): `hold_after_s: 0.10`(신규, 경기 모드), `stale_timeout_s: 0.15`(유지, `--race` off 용), `pre_go_relatch_s: 1.0`(신규), `input_accel_abs_mps2: 15.0`(유지, D7 예외 적용).

---

## 11. 작업 순서 (WP)

### WP0. 기준 정리
- [x] (2026-11-06) `DBC/A1_dbc_2_fixed.dbc` 만들기 — `A1_dbc_2.dbc` 에서 아래만 고친다. 고친 줄마다 `CM_` 주석으로 이유를 남긴다.
  - 0x200 `steer_is_auto`: 배율 0.1 → 1, 범위 [0|0.1] → [0|1] (기존 수정본과 같은 오타)
  - 0x710 `EPS_Alive_Cnt`, 0x711 `ACC_Alive_Cnt`, 0x120 `live_counter`: 범위 [0|1] → [0|255]
  - 0x124 `SIG_GO` 에 `VAL_` 추가, 0x100 `steer_command` 단위 깨짐("��") → "deg", 0x125 `PITZONE_LONG` 단위 → "deg"
- [x] (2026-11-06) 저장소 전체에서 `A1_dbc_fixed.dbc` 참조를 찾아 새 파일로 바꾼다. 옛 파일은 `archive/dbc_2026-10/` 로 옮기고 README 를 쓴다.
- [x] (2026-11-06) 브레이크 단위 % 통일(D13): `rx_decode.decode_control_info()` 의 `brake_pos`(×0.1) → `brake_pos_pct`(×1). can_guard·문서의 "÷10", "8.2 포화" → "82 % 포화". **ROS `ControlInfo.brake_position` 은 바꾸지 않는다.**
- [x] (2026-11-06) `CLAUDE.md` 2절 표 갱신: 0x128 = RES 응답(기록만), 0x710~0x712 = 관제용 피드백, 0x202 = 속도 제한 현재값(TODO-1 종료), 0x124~0x127·0x203 추가, 기준 DBC 경로.
- [x] (2026-11-06) `docs/race_flag_requirements.md` — R-01~R-09 (1.1절 기반, 쪽수 포함). 이후 모든 시험이 이 번호를 쓴다.

| ID | 요구사항 |
|---|---|
| R-01 | Go 전에는 조금도 움직이지 않는다 |
| R-02 | Go 후 지체 없이 출발한다(BSW 지연 ≤ 1주기) |
| R-03 | Slow On 후 "일정 시간" 안에 10 km/h 이하 |
| R-04 | Slow Off 전에는 10 km/h 를 넘지 않는다 |
| R-05 | Finish 후 정차 구역에서 완전 정차(모델) + 정차 유지(BSW) |
| R-06 | 원격비상제동 후 풀려도 튀어 나가지 않는다 |
| R-07 | 짧은 끊김으로 불필요하게 서지 않는다 |
| R-08 | 중단 뒤 녹기 + Go 로 다시 출발할 수 있다 |
| R-09 | 깃발·속도·상태 전이를 증거로 남긴다 |

**관문:** 기존 `pytest safety/can_guard/test tools/race_day/test_lift_tx.py`, `ros2_ws` 테스트, `rehearse_lift_guard.sh`(31/31) 전부 통과. 새 DBC 로 cantools 대조 시험 통과.

### WP1. 수신·송신 해석
- [ ] `race_signal.py`: `decode_race_signal`, `RaceSignalDecoder`(2프레임 확인, edge 이벤트, level, age, 정의 밖 조합 기록), `ResMonitor`(RES_START, alive 정지·건너뜀, 처음 본 뒤 0.5 s 끊김), `decode_pit_zone`, `decode_repair`, `decode_res_fb`, `decode_speed_limit_info`.
- [ ] `tx_encode.py`: `encode_speed_limit_command(kph)` (10비트 ×0.1, 0~100 클램프).
- [ ] 0x710/0x711 의 `*_USER_CAN_ERR`, `VS` 해석(기록용).
- [ ] 단위 시험: 1.2절 byte0 예시값, cantools + `A1_dbc_2_fixed.dbc` 대조, alive 경계(255→0).

**관문:** 새 시험 전부 통과 + 기존 시험 그대로 통과.

### WP2. 경기 상태 기계
- [ ] `race_state.py` (4절). 순수 함수 `next_race_state(state, inputs, params) -> (state, limits, events)`.
- [ ] 시험: 4.2절 표의 **모든 줄** + 동시 이벤트 우선순위 + 신호 방식 3종(계속 켜짐 / 한 번만 / 깜빡임) + Go 전 Slow On + 서행 중 Finish + RES 래치.

**관문:** 4.2절 각 줄마다 시험이 최소 1개.

### WP3. can_guard 연결
- [ ] `race_policy.apply_race_envelope()` (5절), `speed_ctrl.py` (6절, anti-windup 포함), `recovery.py` + `state_machine.py`·`command_policy.py` 수정(8절).
- [ ] `protocol.py`: `StatusChannel`(can_guard → 다른 프로세스, 기존 채널과 같은 seq 방식 찢김 방지). 읽는 쪽은 stamp 가 0.1 s 넘게 오래되면 **"없음"** 으로 본다(can_guard 죽음). 필드: seq, stamp, **race_enabled**, guard_state, race_phase, slow_active, long_override, speed_cap_kph, speed_target_kph, sig 비트 5개, race_signal_age_s, res_active, res_alive_ok, vehicle_speed_limit_kph, speed_limit_target_kph, model_healthy_s, input_grace, pit_lat/lon.
- [ ] `can_guard.py`: 새 프레임 수신, 경기 상태 갱신, RES 를 ESTOP 원천(`race_res`)으로 추가, StatusChannel 기록, 새 CLI(10절), 이벤트 로그.
- [ ] 이벤트 이름: `race_signal`, `race_signal_unknown`, `race_signal_lost`, `race_phase`, `slow_on/off`, `pre_go_motion`, `res`, `res_alive_lost`, `override_on/off`, `recover`, `commit`, `resume`, `speed_limit_tx`, `speed_limit_confirm`, `speed_limit_mismatch`, `speed_limit_release_slow`, `foreign_0x203`, `input_clamped`.
- [ ] **`--race` 를 끄면 지금 동작과 완전히 같아야 한다**(기존 시험 전부 통과로 확인).

**관문:** 기존 시험 + 새 시험 통과, `--race` off 에서 리프트 리허설 31/31.

### WP4. 0x203 관리자
- [ ] `speed_limit.py`(9절) 순수 상태기계 + can_guard 연결 + 시작 점검 + `EXIT_SPEED_LIMIT=5`.
- [ ] 시험: 반영 3프레임, 어긋남 0.3 s, 해제 지연 기록, tx off 시작 거부, 0x202 없음, 다른 송신자.

**관문:** 시험 통과. tx 기본값이 off 인지 시험으로 고정.

### WP5. 어댑터·ROS
- [ ] `cmd_adapter.py`: D3(8.2), D7(StatusChannel 의 `input_grace`·`long_override` 로 판단), D11(8.4). StatusChannel 이 없으면(can_guard 구버전·미실행) **지금 규칙 그대로** 동작.
- [ ] `RaceState.msg` — 필드는 데이터시트 v0.2 의 05 시트를 따른다(`phase` 0~5, `phase_name`, `sig_*`, `race_signal_age_s`, `res_active`, `res_alive_ok`, `long_override`, `speed_cap_kph`(−1 = 없음), `vehicle_speed_limit_kph`, `guard_state`, `pit_lat_deg`/`pit_lon_deg`(NaN = 없음)).
- [ ] `race_state_publisher.py`: StatusChannel 50 Hz 읽기 + 바뀌면 즉시 발행. QoS RELIABLE + TRANSIENT_LOCAL, depth 1. protocol.py 는 어댑터처럼 `can_guard_dir` 파라미터로 불러온다.
- [ ] launch 갱신, `bsw_monitor.py` 에 경기 상태·깃발·RES·속도 상한·0x202 값·모델 정상 시간 표시.
- [ ] 데이터시트를 v0.3 으로 갱신(실제 구현과 다른 점 반영).

**관문:** `colcon build` + ros2_ws 시험 통과, SIL 에서 `/race/state` 가 phase 전이를 따라감.

### WP6. 가감속 프로파일 도구
- [ ] `tools/race_flag/fit_long_profiles.py` (7절) + README + `race_flag.yaml`(7.3 주석 포함) + `docs/race_flag_profile_history.md`(빈 표).
- [ ] 기존 로그로 시운전: `measurements/2026-11-04_*`(정상 처리 기대), 앱 조작이 섞인 11-03 로그(**갱신 중단 기대**).

**관문:** 오염 로그에서 종료 코드 3 + UPDATE_BLOCKED.md 생성 확인.

### WP7. SIL 시나리오
- [ ] `fake_race_server.py`: yaml 시나리오(시각, 비트, 신호 방식 level/pulse/flicker, RES, alive 정지).
- [ ] `fake_a1_vehicle.py` 확장: 0x202/0x203(값 유지·제한 적용), RES 중 0x210 무시 + 자체 감속 + 0x128.
- [ ] `sil_tests/r_race.py` 로 아래 시나리오를 자동 판정.

| ID | 시나리오 | 합격 기준 |
|---|---|---|
| S1 | Go 전 모델이 최대 가속을 냄 | 0x210 가속 0, 차속 0, 래치 없음 |
| S2 | Go | Go 확인 후 다음 주기에 모델 명령 전달 |
| S3 | Go 가 1프레임만(pulse) | 무시(2프레임 확인), 이벤트 기록 |
| S4 | Go 가 2프레임만 | RACING 진입, 비트가 내려가도 유지 |
| S5 | 30 km/h 에서 Slow On | 9.3 이하 도달 시간 기록, 이후 10 초과 0회 |
| S6 | 서행 중 모델이 20 km/h 목표·큰 가속 | 속도 유지, 래치 없음(D7) |
| S7 | Slow Off | 램프대로 가속 상한 증가, 입력 유예 1 s |
| S8 | Finish → 모델 정지 | 5 s 뒤 PARKED, auto 000 없음 |
| S9 | 주행 중 RES → 해제 | ESTOP 래치, 해제 후에도 브레이크 유지 |
| S10 | 0x127 alive 정지 | 0.5 s 뒤 SUSPENDED |
| S11 | 0x124 끊김 | 기본: 기록만, timeout 설정 시 SUSPENDED |
| S12 | 비트 동시(slow_on + slow_off, go + slow_on) | 4.2절 우선순위 |
| S13 | 중단 → 재시작 → 녹기 + Go | PRE_GO → RACING |
| S14 | 모델 0.1 / 0.2 / 0.3 / 0.6 / 1.0 s 끊김(고속·저속) | 예산 안 = 복구, 밖 = 정지 확정 |
| S15 | PRE_GO 중 모델 끊김 → 복귀 | 래치 자동 해제, 재 arm |
| S16 | 0x203 tx on: Slow On/Off | 3프레임 반영, 해제, 어긋남 재송신 |
| S17 | 시작 시 0x202 에 9.3 이 남아 있음 | tx off: 시작 거부(종료 코드 5) / tx on: 해제 후 통과 |
| S18 | `--race` off | 기존 E1~E7·P1·P2 결과와 같음 |

**관문:** S1~S18 전부 통과. 결과는 `reports/race_flag/<날짜>_sil/` 에 표 + 그래프.

### WP8. 운영 절차
- [ ] `bsw_up.sh --race`: 10절 옵션 묶음, 시작 점검(0x202 · 0x124 수신 여부 표시 · 경기 상태 = PRE_GO · 모델 정상 10 s 이상).
- [ ] `bsw_test/FIELD_MANUAL.md`: 경기 날 절차, **대회 당일 브리핑 확인 항목**(일정 시간 값, 신호 방식, 0x203 허용, 점검 중 재시작 허용), 중단 → 재개 절차(`resume_mode` 별).
- [ ] 실차 시험 계획(사람 승인 후 실행): ① PRE_GO 브레이크 10/20/30 % 풀림 시간 ② 서행 진입·추종(30 → 8 km/h) ③ Slow Off 램프 ④ `kill -STOP` 0.2 / 0.4 s(10 km/h 이하) ⑤ 0x203 반영(허용 시). 대회 서버 신호가 없으면 0x124 흉내 송신은 **현장 허락 후에만**.

---

## 12. 일정과 미결 사항

### 12.1 일정 (대회 2026-11-25)

| 기간 | 목표 |
|---|---|
| 11-07 ~ 09 | WP0, WP1 |
| 11-10 ~ 13 | WP2, WP3, WP4 |
| 11-14 ~ 17 | WP5, WP7(S1~S18), 모델 팀과 `/race/state` 연동 |
| 11-18 ~ 21 | WP6, 답변 반영, 상시 테스트 실차(WP8 실차 계획) |
| 11-22 | 현장 매뉴얼, 리허설 |
| 11-23 ~ 24 | 6차·최종 테스트에서 실제 서버 신호 확인 → **11-24 코드 동결** |

### 12.2 답변 대기 → 답이 오면 바꿀 곳

| 질문 | 대상 | 바꿀 곳 |
|---|---|---|
| 2 신호 방식 | KIAPI | 4.2 원칙 유지 확인, S3·S4 기대값 |
| 4 일정 시간 값 | KIAPI | `a_slow` 하한, `--pre-go-*`, 정지 시간 검토 |
| 5 우선순위·STOP | KIAPI | 4.2 우선순위, `--race-stop-action` |
| 6 0x124 끊김 | KIAPI | `--race-signal-timeout-s` |
| 7 점검 중 재시작 | KIAPI | `--resume-mode` |
| 8 속도 판정·서행 기준 | KIAPI | 6.3 단계 3 조건, `--finish-cap-kph` |
| 9 좌표 | KIAPI | `/race/state` pit 필드 용도 |
| 10-1 0x203 허용 | KIAPI | `--speed-limit-tx` 기본값 |
| 해제값 / 초과 시 제동 / 속도 기준 | 이아우토반 | `--speed-limit-release-kph`, 6.1 설명 |
| 1-1 발행 주기 | 모델 팀 | `hold_after_s` |
| 2-x 적분·출력 상한 | 모델 팀 | D7 유예 시간 검토 |
| 4-x Finish·정차 구역 | 모델 팀 | R-05 시험 방법 |
| 5-1 최소 코너 반경 | 모델 팀 | `--track-min-radius-m` |

---

## 13. 하지 말 것

- 위치(로컬라이제이션) 정보를 can_guard 로 가져오지 않는다(D5).
- `--speed-limit-tx` 를 KIAPI 허용 전에 켜지 않는다.
- 학습 프로파일 값을 도구 판정·사람 승인 없이 바꾸지 않는다.
- ROS `ControlInfo.brake_position` 단위를 바꾸지 않는다(D13).
- ESTOP·STOPPED·PARKED·SUSPENDED 에서 auto 000 을 보내지 않는다.
- `--race` off 동작을 바꾸지 않는다.

---

## 14. 작업 기록

| 날짜 | WP | 결과 | 커밋 | 남은 일 |
|---|---|---|---|---|
| 2026-11-06 | WP0 | 결과는 커밋 메시지 참조 | 이 커밋 | `rehearse_lift_guard.sh` 대회 PC 실행 |

## 15. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-11-06 | 초안 — 규정·DBC·업체 답변·설계 결정 D1~D14 정리 |
| 2026-11-06 | 수정 — 끊김 복구·어댑터 래치 변경도 `--race` 일 때만 적용(off 하위 호환과 충돌 해소), StatusChannel 에 race_enabled·오래됨 판정 추가 |
