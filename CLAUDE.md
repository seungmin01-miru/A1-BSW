# A1-BSW — Claude 작업 지침 (실차 주행 테스트)

작성 2026-11-04. 기준 계획서: [`bsw_test/docs/test_plan.md`](bsw_test/docs/test_plan.md). 진행 현황: [`bsw_test/docs/test_summary.md`](bsw_test/docs/test_summary.md).
**지금 할 일(대회 PC 세션은 먼저 읽기): [`bsw_test/docs/NEXT_competition_pc.md`](bsw_test/docs/NEXT_competition_pc.md)** — 2026-11-04 v2(50 m·자율 U턴·속도 제어기) 넘김.
이 파일은 **규칙과 확정된 사실**만 둔다. 숫자 근거는 아래 문서에 있고, 바뀌면 이 파일도 같이 고친다.

| 근거 문서 | 내용 |
|---|---|
| [`bsw_test/FIELD_MANUAL.md`](bsw_test/FIELD_MANUAL.md) | **현장 매뉴얼** — 역할·안전·띄우기/끝내기·테스트별 입력 순서 |
| [`bsw_test/FIELD_CHEATSHEET.txt`](bsw_test/FIELD_CHEATSHEET.txt) | 현장 메모장 — 입력 순서만(명령 + 한 줄 설명), `bsw_test/field_env.sh` 단축 명령 |
| [`docs/a1_vehicle_characteristics.md`](docs/a1_vehicle_characteristics.md) | 지연·가감속·조향 범위, 어댑터 클램프 근거 |
| [`docs/a1_todo.md`](docs/a1_todo.md) 0-1절 | 현장 검증 순위(관문 1~4) |
| [`measurements/2026-11-03_signal_audit/`](measurements/2026-11-03_signal_audit/) | 11-03 실차 신호 검증(`results.md`, `limits.md`, `vendor_answers.md`) |
| [`safety/can_guard/README.md`](safety/can_guard/README.md) | 0x210 송신, 상태기계, 클램프 CLI |
| [`ros2_ws/src/a1_control_adapter/config/track.yaml`](ros2_ws/src/a1_control_adapter/config/track.yaml) | 변환표·부호·클램프 (테스트 결과로 갱신) |

---

**기준 PC = 대회 차량 PC**(Ubuntu 22.04·ROS2 Humble·`~/git/A1-BSW`·시스템 `python3`·Kvaser Leaf 채널 0·격리 코어 8-15). 스크립트·매뉴얼 명령은 이 PC 에서 그대로 돌아야 하고,
다른 PC(개발 PC) 결과는 "참고"로만 적고 판정에 쓰지 않는다.

## 1. 안전 규칙과 Claude 권한

### 1.1 Claude 가 하지 않는 것
- `raw/` 안의 파일 수정·삭제·이름 변경 (원본 로그는 읽기 전용)
- 사용자 승인 없이 **차량·CAN 에 닿는 명령** 실행: `bringup.sh`, `stop.sh`, `record.sh`, `can_guard.py`, `lift_cmd.py`,
  `kvaser_mirror.py`, `cansend`, `ip link set can*` 등 — `.claude/settings.json` 의 `ask` 규칙으로 매번 확인 창이 뜬다
- `archive/dbc_2026-09/`(EAIT) DBC 사용, 0x156/0x157 을 원격 경로로 가정
- 합격 기준 숫자를 임의로 바꾸기 (기준은 각 테스트의 `docs/goal.md` 에서만 가져온다)

### 1.2 승인 받고 하는 것
- 테스트 코드·현장 스크립트 실행, `track.yaml`·can_guard 한계값 변경, 기존 도구 파일 이동(통합 작업)

### 1.3 자유롭게 하는 것
- 저장된 로그의 변환(`converted/`)·분석(`analysis.py`)·그래프(`results/`), 문서 작성, SIL(vcan) 단위 시험 코드 작성

### 1.4 현장 안전 사실 (테스트 절차에 반드시 반영)
- **원격은 0x210 을 직접 보낸다** → 원격을 켠 채 자율(can_guard) 불가, 자율 중 원격으로 개입 불가.
- **auto 000 을 보내면 페일세이프 브레이크가 풀린다** → 종료는 항상 **brake 100 유지한 채 `stop.sh`** (auto off 하지 않음).
- 비상 정지는 **can_guard `ESTOP` 상태**로 구현(2026-11-04, `safety/can_guard/README.md` "비상 정지"). 원천: **핸드폰 앱 = 원격 0x156/0x157**,
  `estop.py`, SIGUSR1. 브레이크는 휠락 방지로 **서서히**(30 % → 82 %). 앱 버튼의 실제 프레임은 현장 확인 전(잠정). 00 SIL·실차 통과 전까지는 물리 E-stop 병행.
- can_guard INIT 은 기본 **silent**(첫 명령 전 0x210 안 보냄). 단 제어 노드가 auto 000 명령을 쓰면 그대로 나간다 → 어댑터 disarm·`lift_cmd auto off` 주의.
- 0x210 끊김 시 차량 동작: 문서끼리 충돌(“타임아웃 없음” vs “0.4 s 안 브레이크 8.2”) → **실무자 확인 대기(TODO-2)**. 확인 전엔 차량 자체 타임아웃에 기대지 않는다.
- 자율은 **전진만**. 후진은 사람이 직접 한다. 왕복의 방향 전환은 **자율 U턴**(전진, 핸들 ±150 저속, 늘 같은 방향 — 2026-11-04 결정, 첫 U턴 전 조향 부호 확인).
- **속도 상한 30 km/h 로 시작**, 결과를 보고 단계적으로 푼다. 각 테스트 `goal.md` 에 그날의 상한과 can_guard 클램프
  (`--steer-limit-deg`, `--brake-limit-pct`, `--acc-limit-pct`)를 적는다.

---

## 2. 차량·신호 사실

| 항목 | 값 | 비고 |
|---|---|---|
| DBC | `DBC/A1_dbc_fixed.dbc` | 유일한 기준 |
| 명령 0x210 | `steer_command`(핸들각, 0.1 °/raw, ±150°), `break_command` 0~100 %, `acc_command` 0~100 %, 축별 `*_is_auto_command` | 송신자는 can_guard 하나 |
| 상태 0x200 | `steer_postion`(핸들각 °), `break_postion`(0~10, = 명령 % ÷ 10) | 브레이크는 **82 % 이상에서 8.2 포화** |
| 바퀴 0x201 | `left/right_speed`(km/h), `rpm` | **후륜 엔코더만**, 전륜은 프리롤링(엔코더 없음). **부호 없음** |
| 주기 | 0x200/0x201 21.0 ms(47.6 Hz), can_guard 20 ms | — |
| DBC 밖 ID | 0x128, 0x202, 0x710~0x712 | 로그 확보됨, 정리 예정(TODO-1) |
| 휠베이스 L | **2.360 m** | 확정 |
| 조향비 | 약 7.5 (핸들 150° ≈ 타이어 20°) | **잠정** — 변환표 작성 대상(TODO-4) |
| 조향 불감대 | 핸들각 0 근처 ±1.5° | 10-07 리프트 |
| 지연 | 조향 무반응 약 175 ms, 브레이크 56~112 ms | 리프트 값, 주행으로 재확인 |
| 차량 heartbeat | 없음(축별 `live_counter` 만) | heartbeat 는 우리 쪽(제어기→can_guard `HeartbeatChannel`) 기준 |

### 2.1 단위·부호 규약
- **CAN 입출력은 핸들각, 분석·보정·합격 기준은 타이어각.** 변환은 반드시 하나의 변환표(TODO-4)를 거친다. 그래프·표에는 단위를 `핸들 °` / `타이어 °` 로 명시.
- 01 의 조향 오프셋도 **타이어각으로 산출**하고, CAN 에 넣을 핸들각은 변환표로 계산해 함께 적는다.
- 부호: 좌회전 = + (`track.yaml` `steer_sign` 기준). 실차 부호 확인 전까지 결과에 “부호 미확인” 표시.
- 브레이크: 명령 %와 위치(÷10)를 섞지 않는다. 차량에 **브레이크 압력 신호는 없다** → 04 는 “브레이크 위치”로 기록.
- 가속·브레이크 % ↔ m/s² 는 **변환표를 만들어야 한다**(03·04 개루프 단계 산출물, TODO-3).

---

## 3. 폴더 구조와 통합 계획

`bsw_test/docs/test_plan.md` 1절 구조를 따른다(골격 생성 2026-11-04, 각 테스트 `docs/goal.md` 작성됨). **기존 검증 도구를 `bsw_test/` 로 합치는 방향**이지만, 현장 매뉴얼
(`TRACK_MANUAL.md`, `AUDIT_MANUAL.md`, `bringup.sh` 출력)이 경로를 참조하므로 **이동은 사용자 승인 후 한 번에** 하고 매뉴얼 경로도 같이 고친다.

| 기존 | 통합 후(안) | 상태 |
|---|---|---|
| `tools/signal_audit/`(judge, limits, plot, dbc_lite) | `bsw_test/common/scripts/` | 미착수 |
| `tools/track_day/`(bringup, stop, record, fit_maps) | `bsw_test/common/field/` | 미착수 |
| `measurements/2026-11-03_signal_audit/` 등 과거 측정 | 그대로 둠(참고 자료), 필요한 결과만 각 테스트 `results.md` 에 링크 | — |

새 공통 코드는 `bsw_test/common/scripts/` 에 두고 각 `analysis.py` 는 그것을 import 한다. CAN 디코딩은 기존 `dbc_lite.py` 를 재사용한다(새로 만들지 않는다).

---

## 4. 데이터 규칙

- **원본 형식 = CAN 로그**(Kvaser 미러 `--log` / `candump -l` 형식)로 통일.
- 파일 이름: `날짜_테스트ID_회차.log` — 예) `20261104_LON-ACC-01_r1.log`
- GNSS **RTX fix** 일 때는 같은 이름의 rosbag 을 함께 둔다: `20261104_LON-ACC-01_r1_bag/`
- `raw/` 는 git 에 넣지 않는다(용량). `raw/README.md` 에 원본 보관 위치·크기·sha256 만 기록.
- `converted/` CSV 는 스크립트로 언제든 다시 만들 수 있어야 한다(손으로 고치지 않음).

## 5. 분석 규칙

- **속도·위치 원천 우선순위:** ① GNSS RTX fix 구간의 속도·pose(rosbag) → ② 후륜 좌우 평균(0x201). fix 아닌 구간은 GNSS 에서 제외하고 그래프에 표시.
- 후륜은 구동륜이라 **가속 중 슬립**이 섞인다 → 가속 성능은 GNSS 와 비교해 슬립 구간을 표시한다. 0x201 은 부호가 없으므로 전진 구간만 쓴다.
- 공회전·리프트 데이터로 m/s² 를 확정하지 않는다(실차 주행 데이터만).
- 06(에커먼)·회전반경은 **저속 데이터만**: `R = v / 요레이트`, `δ_tire = atan(L / R)`, L = 2.360 m. 오차는 타이어각 기준.
- 시간 동기: CAN 로그와 rosbag 의 시각 기준을 결과에 적는다(TODO-5).
- 판정은 `goal.md` 의 합격 기준표로만 한다. 기준이 “초기 가정치”이면 결과에 그렇게 표시하고, 기준 조정 제안은 `results.md` “다음 조치”에 쓴다.

## 6. 테스트별 확정 사항

| ID | 확정 사항 |
|---|---|
| 00 SAF-ESTOP | can_guard 에 비상 정지 신호 구현 → SIL(vcan) 검증 → 저속(≤ 10 km/h) 실차. 감지 ≤ 100 ms, 제동 시작 ≤ 200 ms. 정지 후 auto 000 을 보내지 않는지 확인 |
| 01 LAT-ALIGN | 오프셋은 타이어각으로 산출. **직선 50 m**(앱 신호 거리, 11-04), 기준 50 m 횡편차 ≤ 0.125 m(100 m·0.5 m 제곱 환산 — 이탈 ∝ L², 사용자 결정. 불감대 ±0.2° 타이어면 못 지킬 수 있음 → 실제 조향 위치 같이 기록). 왕복은 자율 U턴(두 차선 약 13 m 간격). 보정 전·후 비교 |
| 02 LON-PROF | 계단·램프·사다리꼴. 1단계는 **가속/브레이크 % 프로파일**, 2단계는 목표속도 프로파일. 상한 내장 + 명령값 로그. 1단계 구현 `bsw_test/common/scripts/profile_gen.py`(CommandChannel 직접, yaml `02_LON_profile/profiles/`). 11-04: 한 세션 여러 회차(`--reps/--sweep`) + 회차 사이 자율 U턴(`--turn`) + 코스 길이 끝내기 + `--steer-offset` + 폐루프 속도 제어(`SpeedController`, 0x201 피드백, 파라미터 `02_LON_profile/ctrl/speed_ctrl.yaml` 잠정). SIL `02_LON_profile/scripts/sil_session.py` |
| 03 LON-ACC / 04 LON-DEC | **(a) 개루프 % 계단 → % ↔ m/s² 변환표(`fit_maps.py` → `track.yaml`) 먼저**, (b) 폐루프 속도 추종은 그다음. 지연·오버슈트 합격 기준은 (b)에 적용 |
| 05 LAT-MAXSTEER | 좌(+150, 반시계)·우(−150, 시계) 각각, 저속, GNSS 궤적 + 요레이트. 07 나선이 있어도 유지(11-04) |
| 06 LAT-ACK | 05 데이터로 1차 분석, 07(원) 이후 갱신. 오차 기준 타이어각 1~2° |
| 07 LAT-PROF | 02 생성기 재사용, 명령·실제 조향각 동시 기록. 결과로 핸들↔타이어 변환표 갱신 |

## 7. 문서 기록 규칙

- 양식은 `bsw_test/docs/test_plan.md` 4절(`goal.md` / `results.md` / `test_summary.md`).
- `results.md` 에 반드시: **소프트웨어 커밋 해시**, 그날 속도 상한·클램프, 원본 파일 이름, **시각화 자료 링크**
  - 그래프: 상대 경로 링크 `[목표 vs 실제 속도](../results/20261104_LON-ACC-01_r1_speed.png)`
  - 공유용 페이지(Artifact 등)를 만들었으면 그 URL 도 “그래프” 절에 적는다
- `test_summary.md` 에 “결과 링크” 열을 두고 각 테스트 `results.md` 와 대표 그래프로 연결한다.
- 그래프 파일 이름은 원본 이름 + `_내용.png`.

## 8. 미정 항목 (확인되면 이 표와 본문을 고친다)

| # | 항목 | 담당 / 방법 |
|---|---|---|
| TODO-1 | DBC 밖 ID(0x128, 0x202, 0x710~0x712) 의미 | 보유 로그 옮겨서 분석 |
| TODO-2 | 0x210 끊김 시 차량 타임아웃·브레이크 동작 | 실무자 확인 |
| TODO-3 | 가속·브레이크 % ↔ m/s² 변환표 | 03·04 (a) 단계 |
| TODO-4 | 핸들각 ↔ 타이어각 변환표(조향비, 좌우 비대칭, 부호) | 정지 측정 + 05·07 |
| TODO-5 | GNSS/IMU 토픽 이름·rosbag 과 CAN 로그 시간 동기 | **localization 팀 작업 후 전달 예정** → `common/signal_list.md`, 각 goal.md 측정 신호 갱신 |

## 9. 작성 스타일

- 한국어, 표 중심, 숫자에는 단위와 출처(파일·날짜)를 붙인다.
- 잠정값은 “잠정”, 계산한 값은 ★ 로 표시(기존 문서와 같은 규칙).
- 완료 항목은 지우지 않고 `[x]` 와 날짜를 붙인다.
