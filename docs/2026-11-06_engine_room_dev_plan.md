# 엔진룸(모델 ↔ CAN 인터페이스) + 경기 신호 전달 — BSW 개발 지시서

작성 2026-11-06 · 상태: **설계 확정, 구현 전** · 이 문서는 같은 날 작성된 `2026-11-06_race_flag_dev_plan.md` 를 **대체**한다(그 문서는 `docs/archive/` 로 이동, 설계 대부분 폐기).

> 표기: **잠정** = 실측으로 바뀔 값 / ★ = 계산값 / `[ ]` 미착수 · `[x]` 완료(날짜)
> 실측 숫자는 반드시 **출처 파일 + 측정 방식**(GNSS 지속 / 이미지 순간 / 저속 실차)을 함께 적는다.

근거 자료(저장소 안):
- 규정집 `docs/reference/2026_race/2026_A1_CHALLENGE_RACE_rulebook.pdf` · 대회 DBC `DBC/A1_dbc_2.dbc`, `DBC/A1_dbc_2_CAN.xlsx`(원본, 수정 금지)
- 대회 질의서 `docs/2026-11-06_race_flag_questions.md` · 이아우토반 답변: 이 문서 1.3절
- 모델·로컬라이제이션 팀 전달 데이터시트 `docs/A1_BSW_토픽_데이터시트_v0.4.xlsx`(→ WP0 에서 v0.5)
- 차량 실측 `docs/a1_vehicle_characteristics.md`, `measurements/2026-11-04_signal_audit/quick_lon.md`
- **11-04 트랙 주행 분석** `docs/reference/2026-11-04_track_drive_analysis.pdf`, 원본 `reports(log)/20261104_track/`(README·그래프·CSV, 사용자가 직접 반입)

---

## 0. Claude Code 에게

1. **루트 `CLAUDE.md` 1절(안전 규칙·권한)이 이 문서보다 우선**한다. 실차·CAN 에 닿는 명령(`bsw_up.sh`, `can_guard.py` 실차, `cansend`, can0/Kvaser)은 실행하지 않는다. SIL(vcan)·pytest·colcon 은 자유.
2. **이번 호출의 범위는 사용자가 지정한 WP 만**이다(예: "WP-1·WP0·WP1 진행"). 지정되지 않은 WP 는 시작하지 않는다. **첫 호출은 반드시 WP-1 부터**(저장소에 폐기 설계가 이미 구현돼 있다).
3. 각 WP 끝의 **관문**을 통과하지 못하면 다음으로 넘어가지 말고 원인을 고친다. 같은 관문 3회 실패 시 멈추고 보고.
4. 커밋은 WP 단위, 첫 줄 `[engine-room WPn]`. **`git push` 금지.**
5. 판단이 필요하면 **차가 덜 움직이는 쪽**으로 구현하고 `docs/engine_room_open_items.md` 에 적는다.
6. 끝나면 체크박스를 `[x] (날짜)` 로, 12절 작업 기록에 한 줄.
7. 저장소는 공개다. 개인 이름·연락처·대회 비공개 자료 원문을 넣지 않는다.
8. **조향 변환 코드(`conversion.SteerMap`, `SlewLimiter`, `steer_sign` 처리)는 건드리지 않는다**(2.1절). 예외: 2.6절에서 `track.yaml` `steer_map` **숫자**만 실측 근거로 바꾼다. 종방향 `LongMap` 은 2.6.2(b) 히스테리시스만 추가한다. `raw/` 수정 금지, 합격 기준 숫자 임의 변경 금지.

---

## 1. 배경

### 1.1 역할 분담 (2026-11-06 팀 회의 결정)

| BSW 가 하는 것 | BSW 가 하지 않는 것 (모델·플래닝 담당) |
|---|---|
| ① 모델 가감속 명령 → 페달 % 변환을 **속도 구간별로 정확하게** | 속도 제어, 램프, 필터, 예측 보정 |
| ② 모델 입력 끊김 → 정지 (기존 HOLDING → STOPPED 그대로) | 짧은 끊김 복구, Go 잠금, 서행 강제, RES 반응, 정차 유지 |
| ③ 깃발·RES·0x202·앱 상태 → `/race/state` 로 **즉시 전달** | 깃발 해석(phase), 0x203 송신 |
| ④ 휠 엔코더 → `/control/status/wheel_info` (기존 그대로) | 위치·속도 추정(로컬라이제이션 담당) |
| ⑤ 차량 물리 한계를 **측정해 숫자로 전달** | 조향 변환(조향비·불감대·고속 보정) — **BSW 코드에서 완전히 제외** |

### 1.2 규정 요약 (쪽수) — 반응 주체는 모두 모델

| 신호 | 차가 해야 할 일 | 어기면 | 쪽 |
|---|---|---|---|
| Go | 일정 시간 안에 출발. **Go 전 움직임 금지** | 원격비상제동·실격 가능 | 14·15·19 |
| Slow On / Off | 일정 시간 안에 10 km/h 이하 / Slow Off 전 10 초과 금지 | 페널티·실격 가능 | 15·16·19 |
| Finish | 서행 → 2~5번 코너 사이 정차 구역, 차량 중심이 기준선 넘어 완전 정차 | 페널티 | 14·16 |
| 원격비상제동 | 일정 시간 안에 정지. 중단 시 팀장 원격으로 피트 이동 → 점검 → 녹기 + Go | — | 16·17 |
| "일정 시간" | 평가위원이 경기 중 판단(숫자 없음) | — | 19 |
| 제자리 정지 | 주행 중 일정 시간 이상 멈추면 실격 가능 | — | 19 |
| 미기재 사항 | 허가되지 않는 것이 원칙(0x203 송신 등) | — | 1 |
| 브리핑 | 당일 브리핑이 규정보다 우선 | — | 10 |

### 1.3 대회 신호 DBC 와 이아우토반 답변 (2026-11-06)

| ID | 이름 | 신호 | 비고 |
|---|---|---|---|
| 0x124 | KIAPI_Race_Signal | `SIG_GO` bit7, `SIG_STOP` bit6, `SIG_FINISH` bit5, `SIG_SLOW_ON` bit4, `SIG_SLOW_OFF` bit3 | byte0: GO=`0x80` STOP=`0x40` FINISH=`0x20` SLOW_ON=`0x10` SLOW_OFF=`0x08`. alive 없음. 엑셀 주기 20 ms(개요 시트는 TBD) |
| 0x125 | KIAPI_PIT_ZONE | 위도 0/32, 경도 32/32 (×1e-7 °) | 용도 미정 |
| 0x126 | KIAPI_REPAIR | `SIG_REPAIR` 0/8 | 뜻 미정 → 기록만 |
| 0x127 | KIAPI_RES | `RES_START` bit7, `alive_count` byte7 | **차량이 스스로 제동, 그동안 0x210 무시. RES=0 이면 즉시 우리 명령 따름.** alive 끊김 시 정지 권장(정의 없음) |
| 0x128 | KIAPI_RES_FB | `RES_END` bit7, `RES_ERROR` 0/3 | KIAPI ↔ 이아우토반. 우리는 기록만 |
| 0x202 | USER_service_info | `speed_limit_info` 0/10 ×0.1 km/h | 차량 속도 제한 현재값 |
| 0x203 | USER_service_command | `speed_limit` | 실제 제한됨, 송신 끊어도 유지. **BSW 는 보내지 않음** |
| 0x156/157 | KIAPI_Control_01/02 | 대회 원격 앱 | 기존 RemoteEstopDetector |
| — | 신호 버스 | **차량 제어 CAN 과 같은 버스** | 채널 추가 없음 |

답변 대기(KIAPI): 신호 유지 방식(계속/한 번), 일정 시간, 우선순위·STOP 의미, 0x124 끊김 처리, 점검 중 재시작 허용, 속도 판정 센서, 정차 구역 좌표. → 전부 **모델 팀 설계에 영향**, BSW 는 전달만.

---

## 2. 엔진룸 규격

### 2.1 범위

- **종방향만**: 목표 가감속(m/s²) → 가속 % / 브레이크 %. 입력에 **현재 속도**(0x201 후륜 평균)를 더해 2차원 표로 변환.
- **조향은 제외**: `cmd_adapter.py` 의 조향 변환(`steer_map` 7.5 선형, 120 °/s, `steer_sign`)은 **그대로 둔다.** 조향비·불감대·고속 보정은 모델 담당. BSW 는 2.5절 물리 한계만 측정해 전달.
- 모델 명령을 바꾸지 않는다: 필터·램프·예측 보정 없음. 표 변환, 범위 클램프, 출발 불감대 반영만.

### 2.2 2차원 종방향 표 (`track.yaml` 형식 확장)

```yaml
long_map:
  version: 1
  source: measurements/2026-11-14_engine_room/      # 이 표를 만든 로그 (필수)
  speed_bins_kph:  [0, 5, 10, 15, 20, 30, 40, 50, 60, 80, 100]
  accel_bins_mps2: [-4, -3, -2, -1.5, -1, -0.5, -0.2, 0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.3]
  # 행 = 속도, 열 = 가속도. 양수 = 가속 %, 음수 = 브레이크 %, null = 미측정
  pedal_pct:
    0:   [-100, -90, -70, -55, -40, -20, -5, 0, 78, 85, 100, 100, 100, 100]
    5:   [null, ...]
    ...
  deadband_mps2: 0.1          # |요청| < 0.1 → 페달 0 (기존 long_deadband_mps2)
  launch_min_acc_pct: 75      # 차속 < launch_speed_kph 이고 요청 > deadband 이면 가속 % 하한 (11-04 실측 불감대)
  launch_speed_kph: 3.0
```

규칙:
- **이중선형 보간**(속도 사이, 가속도 사이). 표 밖은 끝값 고정 + 이벤트 `long_map_clamped`.
- `null` 칸: 가장 가까운 측정 칸 값을 쓰고 `AdapterStatus.extrapolated = true`. 어디가 실측인지 항상 보이게.
- 가속·브레이크 동시 금지(기존). 0 행의 양수 쪽은 `launch_min_acc_pct` 이상이 들어간다(출발 불감대).
- 기존 1차원 `accel_map`/`brake_map` 이 있고 `long_map` 이 없으면 **지금 동작 그대로**(하위 호환). 둘 다 있으면 `long_map` 우선.
- 속도 원천: `/interface/can/read/raw` 0x201 좌우 평균 → 어댑터가 구독(지금은 안 받음). 나이 > 0.1 s 이면 마지막 값 유지 + `speed_stale`. 슬립으로 ±2 km/h 틀려도 보간 결과 차이가 작아 허용.

`AdapterStatus` 추가 필드: `long_map_version`, `extrapolated`, `speed_used_kph`, `speed_stale`.

### 2.3 갱신 도구 `tools/engine_room/fit_long_map.py`

- 입력: CAN 로그(Kvaser `--log` 또는 `candump -l`) + GNSS bag(`/gsof_client/odom`, 속도·가속도). 0x210 명령과 0x201 속도로 셀을 고르고, GNSS 가속도(0.3 s 이동평균)를 실제값으로.
- 출력(`reports/engine_room/<날짜>_fit/`): 측정 표(셀별 평균·표본수), 기존 표와 차이(%), **추천 `long_map` yaml**, 그래프, 사용 구간 목록.
- **현장 목표: 1분 안에 실행**(서킷 한 바퀴 뒤 바로 갱신).
- **자동 반영 금지.** 사람이 보고 `track.yaml` 을 바꾸고 커밋.
- 오염 검사 — 하나라도 걸리면 추천을 내지 않고 `UPDATE_BLOCKED.md` + 종료 코드 3:

| 검사 | 기준(잠정) |
|---|---|
| GNSS 품질 | RTX fix 비율 < 90 % |
| 다른 조작 | 앱 0x156 En=1, ESTOP, RES, 다른 송신자 0x210 구간 포함 |
| 물리 한계 | 감속 > 4.5 m/s², 가속 > 1.6 m/s² |
| 시간 어긋남 | CAN ↔ bag 시각 차 확인 불가 또는 > 0.05 s |
| 표본 부족 | 셀당 < 3 샘플 구간 |
| 경사 | INS 피치 > 2° |
| 급변 | 추천값이 기존보다 > 20 % → 자동 추천 금지, 사람 확인 |

### 2.4 정확도 합격 기준

"요청 가속도 vs GNSS 실측 가속도" 정상 상태 오차 **±20 % 이내**(각 속도 구간). 응답 지연은 실측 특성값(브레이크 무반응 87 ms, 가속 출발 0.64 s)을 넘지 않음. 이 기준을 넘지 못한 속도 구간으로는 **속도를 올리지 않는다.**

### 2.5 차량 물리 한계 측정 (모델 팀 전달용, BSW 코드 아님)

| 항목 | 방법 | 전달 값 |
|---|---|---|
| 조향 최대각·좌우 대칭 | 정지 상태 핸들 ±150° 명령 → 0x200 실제 포화점 | 최대 핸들각 좌/우 |
| 조향 0점·좌우 비대칭 | **원 돌기**: 타이어각 ±5/10/15/20°(핸들 ±37/75/112/150°) 각 2바퀴, ≤ 8 km/h, GNSS 궤적을 원에 맞춤 → 반경 → `타이어각 = atan(2.36 / R)` | 0점 오프셋, 각도별 실효 조향비(좌/우). **정렬이 틀어져 있으면 기계 정렬 또는 모델 오프셋**으로 잡는다 |
| 조향 각속도 | 기존 실측 126 °/s 재확인 | 최대 °/s |
| 가속 최대 | 직선 가속 100 %, 속도 구간별 GNSS | 속도별 최대 가속도 곡선 |
| 감속 최대·잠김 | 브레이크 100 %, 속도 구간별. 휠속이 GNSS 보다 급히 떨어지는 % | 속도별 최대 감속도, 잠김 시작 % |
| 횡가속 한계 | 원 돌기 속도를 올리며 미끄러지기 시작하는 점(시간 여유 시) | 최대 횡가속 |
| 휠 오돔 보정 | 정속 10/20/30 km/h 각 20 s, 휠속 ↔ GNSS | 바퀴 지름 보정 계수(로컬라이제이션용) |

### 2.6 엔진룸 1차 업데이트 — 2026-11-04 트랙 주행 분석 반영 (WP0 에서 실행)

근거: `docs/reference/2026-11-04_track_drive_analysis.pdf` + 원본 `reports(log)/20261104_track/`(README, `1_조향/P1_steer_ratio.png`, `2_가속/P2_accel_onoff.png`, `데이터/bsw5_stats.csv`). bsw5 = 11-04 17:02 출발, 420 s, 2 km, 평균 17.4 km/h, GNSS RTK 기준.

#### 2.6.1 발견된 엔진룸 결함 2가지

| # | 현상 | 원인(코드 확인) | 영향 |
|---|---|---|---|
| F1 | **조향: 핸들 ±150° 에서 타이어가 ±10~11° 만 돎.** 실효 조향비 9~13(핸들각별), 보고서 평균 10.7. 영점: 핸들 0° 에서 타이어 **+1.2°(좌)** | `track.yaml` `steer_map` 이 7.5 선형 3점(잠정값) | 직선에서 늘 왼쪽 +0.33 m, 코너 바깥으로 밀림, **bsw4 연석 직전 비상정지의 원인** |
| F2 | **가속: 0 % ↔ 75~85 % 를 분당 161회 전환**(on/off 제어). 속도 19.4~20.4 톱니, 곡선 안 가속 펄스 | `conversion.LongMap.to_pedals()`: `accel > 0.1 → 75 %`, 아니면 0. **히스테리시스 없음**. 20 km/h 순항 목표가 0.03~0.18 로 경계를 매 주기 넘나듦 | 승차감·경로 추종 악화, 브레이크 데드타임 비대칭(밟기 0.15 s / 떼기 0.4 s)과 겹침 |

보고서의 "속도별 실효 조향비 15.8(5 km/h) → 8.7(20 km/h)" 는 **언더스티어가 아니다**(언더스티어면 반대 방향). 저속 구간 = 헤어핀 = 핸들 −150° 포화 구간이라 **큰 핸들각에서 타이어각이 비선형으로 포화**하는 것이 주원인(산점도가 큰 각에서 꺾임). 따라서 보정은 **핸들각별 비선형 표**로 하고 속도 축은 넣지 않는다. 고속 슬립·언더스티어는 모델 담당(1.1절) — 그 몫까지 BSW 표에 넣지 않는다.

#### 2.6.2 1차 업데이트 내용 (`track.yaml` 숫자 + `LongMap` 히스테리시스)

**(a) `steer_map` 비선형 7점 + 영점** — 산점도(P1_steer_ratio, bsw5 전 구간) 판독값 ★, 11-14 원 돌기에서 확정:

```yaml
steer_map:                      # 2026-11-04 bsw5 GNSS 요레이트 역산 (★ 그래프 판독, 11-14 원 돌기로 교체)
  tire_deg:  [-11.5, -8.5, -5.0,  0.0,  5.5,  9.0, 11.5]
  wheel_deg: [-150, -100,  -50, -9.0,  50,  100,  150]   # 타이어 0 ↔ 핸들 −9° (영점 +1.2° 타이어 보정, 비 7.5 기준 ★)
```
- 영점: 핸들 0° 에서 타이어 +1.2°(좌) → 타이어 0 을 내려면 핸들 **약 −9 ~ −13°**. 1차는 −9°(보고서 범위의 작은 쪽, 과보정 방지). 양쪽 점은 영점만큼 평행 이동하지 않고 끝점 유지(단조 증가 검사 통과).
- **기존 3점 표는 `steer_map_v0_20261104.yaml` 로 보관**(되돌리기용).
- 핸들 ±150° 가 타이어 ±11.5° 에 대응하므로 **모델이 요청할 수 있는 최대 타이어각은 ±11.5°** 가 된다(이전 ±20°). 어댑터 `input_steer_tire_abs_deg: 45` 는 유지(고장 판정용), 표 밖은 끝값 포화. **모델 팀에 반드시 통지**(데이터시트 02·04 시트).
- 영점 −9° 를 핸들 불감대 ±1.5° 와 함께 보면 직진 시 핸들이 −9° 근처에서 ±1.5° 흔들릴 수 있음 → 1차에서는 그대로 두고 11-14 에서 확인.

**(b) `LongMap` 가속 히스테리시스** — 코드 변경 1곳(종방향, 조향 아님). `to_pedals()` 에 상태를 둔다:

```
진입: accel >  deadband_on  (0.10)  → 가속 모드
이탈: accel <  deadband_off (0.03)  → 가속 0 (그 사이는 이전 모드 유지)
브레이크 쪽도 같은 방식: 진입 < −0.10, 이탈 > −0.03
가속 모드 안: 표 보간은 그대로(0.1 → 75 %)
```
- 목표가 0.03~0.18 에서 흔들려도 한 번 켜지면 0.03 아래로 떨어질 때까지 75 % 유지 → on/off 사라짐.
- `deadband_off` 는 `track.yaml` `long_deadband_off_mps2: 0.03`(신규). 기존 `long_deadband_mps2` = 진입값으로 의미 유지.
- 보고서 제안 1("주행 중 75 % 바닥값 유지")은 **채택하지 않는다** — 가속 바닥값을 어댑터가 깔면 모델이 0 을 요청해도 차가 밀리는 "가공"이 된다. 히스테리시스만으로 전환은 분당 161회 → 수 회로 줄어야 한다(SIL 로 확인). 그래도 남으면 모델 팀에 속도 PI 재조정(보고서 제안 3) 요청.
- 단위 시험: 0.05 ↔ 0.15 를 20 ms 마다 번갈아 넣었을 때 출력 전환 ≤ 1회. 기존 계단 입력 회귀 통과.

**(c) 브레이크 표** — 11-04 `−1.5 m/s² (55 %) → 실제 1.92` 와 보고서 "데드타임 뗄 때 0.4 s" 는 기록만 하고 표는 바꾸지 않는다(11-14 계단 측정 후).

**(d) 모델 팀 전달 (데이터시트 v0.5 에 추가)**
- 최대 타이어각 ±11.5°(핸들 ±150°). 헤어핀에서 요레이트 필요값의 70 % 만 나옴 → **경로 계획 최소 반경 재검토** 필요.
- 조향 스틱슬립: 5~8° 계단 추종, 정지 시 1.5 Hz 진동, 직선 요레이트 ±3~4 °/s 위빙(보고서 A). 조향 명령 대비 실제 RMS 3.5°.
- 브레이크 데드타임 비대칭 0.15 / 0.4 s(보고서 B).
- 경로추종기 제안(보고서): 횡오차 적분항, 위치 점프 방어, 자동 정지 한계 반응 시간 — 모델 담당.
- 위치추정 종방향 점프 21회/최대 1.1 m — 로컬라이제이션 담당(보고서 3번).

#### 2.6.3 검증
- SIL: 히스테리시스 단위 시험 + `adapter_long_check.py` 회귀. 조향 표는 `SteerMap` 단조 검사 통과 + 역변환(`to_tire`) 왕복 오차 < 0.1°.
- 실차(11-14 ①②): 직선 50 m 횡편차(목표 ≤ 0.125 m, CLAUDE.md 01 기준)로 영점 확인, 원 돌기로 7점 표 교체.
- **되돌리기 조건:** 11-14 직선 주행에서 횡편차가 이전(+0.33 m)보다 나빠지면 `steer_map_v0` 로 복귀.

현재 기록값(출처): 가속 최대 **1.29 / 1.43 m/s²**(10-08 GNSS 지속), 감속 최대 **1.88 / 3.03**(10-08 GNSS 지속, 사람 주행) · 순간 3.97 / 4.11(이미지, 브레이크 100 %), 횡가속 3.4 / 2.7(10-08 GNSS), 가속 100 % 저속 0.57~0.88(11-04), 출발 불감대 70~80 %(11-04), 브레이크 풀림 100→0 % 약 1.4 s(11-04).

---

## 3. 경기 신호 전달 계층

- 메시지 `a1_can_msgs/msg/RaceState` — 필드는 데이터시트 05 시트 그대로: `sig_go/slow_on/slow_off/finish/stop`(2프레임 확인 현재값), `seen_*`(한 번이라도 봤는지, BSW 재시작 전까지 래치), `last_signal`, `last_signal_stamp`, `race_signal_age_s`(−1 = 없음), `res_active`(**1프레임 즉시**), `res_alive_ok`, `res_age_s`, `res_end`, `res_error`, `remote_app_active`, `vehicle_speed_limit_kph`(−1 = 없음), `guard_state`, `pit_lat_deg/pit_lon_deg`(NaN = 없음).
- 발행: 값이 바뀌면 즉시 + 50 Hz. QoS **RELIABLE + TRANSIENT_LOCAL, depth 1**.
- 위치: `ros2_ws/src/a1_can_bridge/a1_can_bridge/race_state_publisher.py`. 해석은 `safety/can_guard/race_signal.py` 를 can_guard 와 **공유**(can_guard 는 이벤트 로그만).
- 브리지 커널 필터 `ids` 에 0x124~0x128, 0x202, 0x156, 0x157 추가. `--race` 없이도 항상 켠다(전달만이라 부작용 없음).
- `phase` 같은 해석 필드는 **넣지 않는다.** 해석은 모델.
- 합격: SIL 에서 0x127 수신 → `/race/state` 발행 지연 **≤ 5 ms**(중앙), 신호 3방식(계속 켜짐 / 2프레임만 / 1프레임 깜빡임) 시나리오 통과. 1프레임 깜빡임은 깃발은 무시·RES 는 반영.

휠 오돔: `/control/status/wheel_info` 변경 없음. `nav_msgs/Odometry` 변환은 로컬라이제이션 팀 요청 시 별도 WP.

---

## 4. 실차 측정 계획 (6일)

속도 상향 관문: **직전 구간 정확도 ±20 % 통과** 후에만 다음 속도. 60 km/h 이상은 표 갱신 뒤 저속 한 바퀴 확인 후 상향. 모델 팀이 서킷 주행 준비가 안 되면 BSW 단독 항목(원 돌기·직선 계단)으로 채운다.

| 날 | 속도 | 순서 | 산출 |
|---|---|---|---|
| **11-14(토)** | 0~20 | ① 점검·RTX 확인 ② 원 돌기 ±5/10/15/20° 각 2바퀴 ≤ 8 km/h ③ 조향 최대각(정지) ④ 직선 계단: 가속 75/85/100 %, 브레이크 50/80/100 %, 각 2회, 0→20 km/h ⑤ **현장 1차 표 생성·적용** ⑥ 서킷 15 → 20(모델), 바퀴마다 갱신 | 0점·대칭·최대각, 표 0~20 행 v1~v3 |
| **11-15(일)** | 20~40 | ① 서킷 20 → 30 → 40, 갱신 ② 30 km/h 브레이크 계단 50/80/100 ③ 휠 오돔 보정(정속 10/20/30) ④ 깃발 전달 확인(0x124 흉내 — **현장 허락 시만**) ⑤ 재측정 | 표 20~40, 오돔 계수 |
| **11-21(토)** | 40~60 | 서킷 40 → 50 → 60, 50 km/h 브레이크 계단, 긴 직선 가속 100 %(고속 가속 포화) | 표 40~60 |
| **11-22(일)** | 60~80 | 서킷 60 → 70 → 80, 80 km/h 감속 계단, 횡가속 한계(여유 시) | 표 60~80 |
| **11-23** | 80~100 | 6차 테스트: 서킷 80 → 90 → 100, **대회 서버 신호 실제 확인** | 표 80~100 |
| **11-24** | 검증 | 전 구간 ±20 % 확인, 재측정, **코드·표 동결** | 최종 표 + 보고서 |

실차 날 커밋: **표 버전마다**(어느 바퀴에 어느 표를 썼는지 추적). 원본 로그는 `measurements/<날짜>_engine_room/`, 분석은 `reports/engine_room/`.

---

## 5. 작업 순서 (WP) — **사용자가 지정한 WP 만 진행**

### WP-1. 폐기 설계 코드 정리 (11-07, WP0 보다 먼저) — **반드시 이 WP 부터**

**배경:** 2026-11-06 오후, 폐기된 첫 지시서(`race_flag_dev_plan`)대로 WP0~WP7 이 이미 구현·커밋됐다(`913e2bf` WP0 → 에이전트 merge 여러 개 → HEAD `3309cd1`, SIL 86/86). 회의 결정(1.1절)으로 그중 **WP0 의 기준 정리만 유지**하고 나머지(경기 상태 기계·서행 제어·0x203·복구·StatusChannel)는 걷어낸다.

- [ ] **보관:** `git tag race-flag-full-3309cd1 HEAD` (삭제 전 상태. 모델 팀이 서행 제어·0x203 을 BSW 에 다시 요청하면 여기서 꺼낸다). `reports/race_flag/` → `reports/archive/race_flag_2026-11-06/` 로 이동, README 첫 줄에 "폐기 설계(첫 지시서) SIL 결과, 참고용".
- [ ] **되돌리기(913e2bf 기준으로 checkout):** `safety/can_guard/{can_guard.py, state_machine.py, command_policy.py, protocol.py, tx_encode.py, plausibility.py}`, `ros2_ws/src/a1_control_adapter/{a1_control_adapter/cmd_adapter.py, config/track.yaml, README.md}`, `ros2_ws/src/a1_can_bridge/launch/a1_bridge.launch.py`, `ros2_ws/src/a1_can_bridge/setup.py`, `ros2_ws/src/a1_can_msgs/CMakeLists.txt`, `sil/vcan/fake_a1_vehicle.py`, `tools/bsw/{bsw_up.sh, bsw_monitor.py, README.md}`, `safety/can_guard/README.md`, `bsw_test/FIELD_MANUAL.md`. **되돌린 뒤 913e2bf 이후의 유지 대상(아래)이 빠지지 않았는지 diff 로 확인.**
- [ ] **삭제:** `safety/can_guard/{race_state.py, race_runtime.py, race_policy.py, speed_ctrl.py, speed_limit.py, recovery.py, race_flag.yaml}`, `safety/can_guard/test/` 의 해당 시험, `safety/can_guard/sil_tests/r_race.py` 의 상태기계·서행·0x203·복구 시나리오(S01·S04~S08·S10~S17·W5r·S14), `sil/vcan/race_scenarios/` 중 같은 것, `tools/race_flag/`, `ros2_ws/src/a1_control_adapter/{a1_control_adapter/race_rules.py, test/test_race_rules.py, test/e2e_race_nodes.py}`, `ros2_ws/src/a1_can_bridge/{a1_can_bridge/race_status.py, test/test_race_status.py}`, `docs/race_flag_profile_history.md`, `docs/race_flag_open_items.md`(내용은 `docs/engine_room_open_items.md` 로 옮김).
- [ ] **유지·재사용(새 지시서 3절에 맞게 고침):**
  - `safety/can_guard/race_signal.py` — 해석 함수·`RaceSignalDecoder`·`ResMonitor` 만 남기고 상태기계 의존 제거. **브리지와 공유.**
  - `ros2_ws/src/a1_can_msgs/msg/RaceState.msg` — 3절 필드로 교체(`phase`·`long_override`·`speed_cap_kph` 등 삭제, `seen_*`·`last_signal`·`res_end/error`·`remote_app_active` 추가).
  - `ros2_ws/src/a1_can_bridge/a1_can_bridge/race_state_publisher.py` — StatusChannel 읽기 → **`/interface/can/read/raw` 구독 + `race_signal.py` 직접 해석**으로 교체. `guard_state` 는 can_guard 이벤트 로그 대신 기존 `/diagnostics` 또는 생략(미결 → `engine_room_open_items.md`).
  - `sil/vcan/fake_race_server.py`, `r_race.py` 의 전달 검증 시나리오(S02·S03·S03b·S09·S09b·S11a·X01) — WP1 관문용으로 이름만 유지.
  - 913e2bf 의 WP0 결과: `DBC/A1_dbc_2_fixed.dbc`, `rx_decode.py` 브레이크 %, `CLAUDE.md` 표, `docs/race_flag_requirements.md`(→ `docs/engine_room_requirements.md` 로 이름 변경, 담당 열을 "모델"로).
- [ ] can_guard 에 `--race` 옵션·경기 상태·StatusChannel 이 **남아 있지 않은지** grep 으로 확인(`race_`, `StatusChannel`, `speed_limit`, `recover`).

**관문:** `pytest safety/can_guard/test tools/race_day/test_lift_tx.py` 통과 수가 913e2bf 시점과 같음(그 시점 수를 먼저 기록), `rehearse_lift_guard.sh` 31/31, ros2_ws `colcon test` 통과, 11-05 보고서 8개 시나리오(`reports/2026-11-05_bsw_update_report/scenarios.py`) 결과 동일, `can_guard.py` 크기가 913e2bf 수준(약 31 KB). 커밋 `[engine-room WP-1] 폐기 설계 제거, 913e2bf 기준 복원`.

### WP0. 기준 정리 (11-07, WP-1 뒤)
- [x] (913e2bf 에서 완료, WP-1 뒤 유지 확인만) `DBC/A1_dbc_2_fixed.dbc`: `A1_dbc_2.dbc` 에서 0x200 `steer_is_auto` 배율 0.1→1·범위 [0|1], Alive_Cnt·live_counter 범위 [0|255], `SIG_GO` VAL_ 추가, 깨진 단위 "��"→"deg". 고친 줄마다 `CM_` 주석. 저장소의 `A1_dbc_fixed.dbc` 참조를 교체, 옛 파일은 `archive/dbc_2026-10/`.
- [x] (913e2bf 에서 완료, 유지 확인만) 브레이크 단위 % 통일: `rx_decode.decode_control_info()` ×0.1 → ×1(`brake_pos_pct`), can_guard·문서 "÷10"·"8.2 포화" → "82 % 포화". **ROS `ControlInfo.brake_position`(0~10)은 유지.**
- [ ] `CLAUDE.md` 2절 표: 0x124~0x128·0x202·0x203 추가, 0x128 = RES 응답(기록만), 0x710~0x712 = 관제용, 0x202 = 속도 제한(TODO-1 종료), 기준 DBC 경로. 1.1절 역할 분담 한 줄 추가.
- [ ] 데이터시트 v0.5: 04 시트 감속 "약 4.1" → "지속 최대 3.0(GNSS), 순간 4.1(이미지)", 06 M-10·02 `accel_mps2` 주의 "+1.3/−3.0(순간 −4.1)". 변경이력 추가.
- [ ] 기존 지시서 → `docs/archive/2026-11-06_race_flag_dev_plan.md`(머리에 "폐기, 대체 문서" 한 줄).
- [ ] **엔진룸 1차 업데이트(2.6절):** ① `track.yaml` `steer_map` 7점 + 영점 −9° 로 교체, 옛 표는 `config/steer_map_v0_20261104.yaml` 보관 ② `conversion.LongMap` 가속·브레이크 히스테리시스(`long_deadband_off_mps2: 0.03`) + 단위 시험(0.05↔0.15 교번 → 전환 ≤ 1회) ③ `AdapterStatus.note` 에 표 버전 ④ 분석 원본은 `reports(log)/20261104_track/` 에 이미 있음 — 옮기지 말고 `reports/README.md` 에 한 줄 추가(위치·내용·PDF 링크). 폴더 이름의 괄호는 그대로 둔다 ⑤ `docs/a1_vehicle_characteristics.md` 조향 절 갱신(최대 타이어각 ±11.5°, 영점, 스틱슬립) ⑥ 데이터시트 v0.5 에 2.6.2(d) 반영.

**관문:** `pytest safety/can_guard/test tools/race_day/test_lift_tx.py`, ros2_ws 시험(히스테리시스 포함), `rehearse_lift_guard.sh` 31/31 통과. 새 DBC cantools 대조 통과. `adapter_long_check.py` SIL 에서 20 km/h 순항 흉내(목표 0.03~0.18 잡음) 시 가속 전환 ≤ 5회/분.

### WP1. 경기 신호 전달 (11-07~09)
- [ ] `safety/can_guard/race_signal.py`: `decode_race_signal`(0x124), `RaceSignalDecoder`(2프레임 확인, seen 래치, last_signal, age), `ResMonitor`(RES_START 1프레임, alive 정지·건너뜀, age), `decode_pit_zone`, `decode_repair`, `decode_res_fb`, `decode_speed_limit_info`. 단위 시험: 1.3절 byte0 예시, cantools 대조, alive 255→0.
- [ ] `RaceState.msg`(3절) + `race_state_publisher.py` + launch + 브리지 `ids` 확장.
- [ ] can_guard: 새 프레임 수신 시 이벤트 로그만(`race_signal`, `res`, `res_alive_lost`, `speed_limit_info`, `race_signal_unknown`). 동작 변경 없음.
- [ ] `bsw_monitor.py`: 깃발 5비트·seen·RES·0x202·앱 표시.
- [ ] `sil/vcan/fake_race_server.py`(yaml 시나리오: 비트·방식 level/pulse2/pulse1·RES·alive 정지) + `sil_tests/r_race.py` 자동 판정 + 지연 측정.

**관문:** 지연 ≤ 5 ms, 3방식 시나리오 통과, 기존 시험 전부 통과, `--race` 옵션 없이 can_guard 동작 변화 없음(E1~E7·P1·P2 동일).

### WP2. 엔진룸 코드 (11-08~11) — **WP0·1 결과 확인 후 별도 지시로 시작**
- [ ] `cmd_adapter.py`: `long_map` 2차원 지원(이중선형, null→extrapolated, 끝값 고정), 0x201 속도 구독, `launch_min_acc_pct`, `AdapterStatus` 새 필드. 조향 코드 **무변경**.
- [ ] `track.yaml`: `long_map` 추가(초기값 = 기존 1차원 표를 모든 속도 행에 복사 + null 표시), 기존 키 유지.
- [ ] 단위 시험: 보간 경계, null 처리, 회귀(기존 1차원 표와 같은 입력 → 같은 출력), 속도 stale.
- [ ] SIL: `fake_a1_vehicle.py` 에 속도별 가속 응답 흉내(간단 1차 모델) → 표 변환이 속도에 따라 바뀌는지 확인.

**관문:** 회귀 시험 통과(기존 동작과 동일), 보간 시험 통과, `adapter_long_check.py` SIL 통과.

### WP3. 측정·갱신 도구 (11-09~13) — WP2 와 함께 지시
- [ ] `bsw_test/common/scripts/circle_test.py`: 타이어각 단계 입력 + 저속 유지(≤ 8 km/h, 간단 PI — BSW 시험용) + 이벤트 기록. `tools/engine_room/fit_circle.py`: GNSS 궤적 → 원 맞춤 → 반경·0점·실효 조향비(좌/우), 보고서.
- [ ] `adapter_long_check.py` 확장: 목표 속도 구간 대기 → 계단 입력 → GNSS·0x201 기록, 여러 계단 연속.
- [ ] `tools/engine_room/fit_long_map.py`(2.3절) + README + `docs/engine_room_map_history.md`(표 버전 이력).
- [ ] 시운전: 11-04 로그(정상 처리), 11-03 앱 조작 섞인 로그(**차단, 종료 코드 3**).
- [ ] 현장 점검표 `bsw_test/09_ENGINE_ROOM/docs/checklist.md`: 날짜별 순서(4절), 각 항목 스크립트·합격 기준·결과 파일.

**관문:** 오염 로그 차단 확인, 정상 로그에서 표·그래프 생성 1분 이내, 점검표 완성.

### WP4~6. 실차 (11-14/15, 21/22, 23/24) — 사람이 실행, Claude Code 는 분석·표 갱신·커밋 보조
- [ ] 4절 계획. 각 날 끝에 `reports/engine_room/<날짜>_fit/` + 표 버전 커밋 + `engine_room_map_history.md` 갱신.

### WP7. 운영 (11-16~20)
- [ ] `bsw_up.sh`: 시작 점검에 `/race/state` 발행 확인, 0x202 현재값 표시(송신 없음), `long_map` 버전·source 출력.
- [ ] `bsw_test/FIELD_MANUAL.md`: 경기 날 절차, **브리핑 확인 항목**(일정 시간, 신호 방식, 점검 중 재시작, 속도 판정), 중단→재개(can_guard 재시작 절차), 데이터시트 최종본 모델 팀 전달.

**관문:** 리허설 통과, 데이터시트 v1.0.

---

## 6. 모델·로컬라이제이션 팀 의존 사항

| 필요한 것 | 누구 | 언제까지 |
|---|---|---|
| `/control/target` 발행 주기, anti-windup·출력 상한 여부 | 모델 | 11-13 (WP2 `speed_stale`·끊김 기준) |
| 11-14 서킷 주행 가능 여부(없으면 BSW 단독 항목만) | 모델 | 11-12 |
| `/race/state` 필드 확인, 2프레임 확인 여부(데이터시트 07 시트 4-5) | 모델 | 11-09 |
| M-01~M-11 책임 목록 이의 | 모델 | 11-09 |
| GNSS bag 에 요레이트·피치 포함, RTX 상태 토픽 | 로컬라이제이션 | 11-13 |
| `WheelInfo` 원본으로 충분한지 / Odometry 변환 필요한지, 후륜 트레드 값 | 로컬라이제이션 | 11-13 |

---

## 7. 하지 말 것
- 조향 변환 코드 수정. 속도 제어·램프·필터·Go 잠금·서행 강제·RES 반응. 0x203 송신. 위치 정보 수신. `ControlInfo.brake_position` 단위 변경. 표 자동 반영. 실차·can0 명령. `git push`.

## 8. 작업 기록
| 날짜 | WP | 결과 | 커밋 | 남은 일 |
|---|---|---|---|---|

## 9. 변경 이력
| 날짜 | 내용 |
|---|---|
| 2026-11-06 | 초안. 회의 결정(BSW = 변환·사망정지·전달·오돔·한계 측정, 반응은 모델) 반영. 조향 변환은 범위 밖, 원 돌기는 정렬·한계 측정용으로 유지. 실차 6일 계획 |
| 2026-11-06 | WP-1 추가: 폐기된 첫 지시서가 이미 WP0~7 구현·커밋된 것을 발견(HEAD 3309cd1) → 913e2bf 기준 복원 + 전달 계층 코드만 재사용 |
| 2026-11-06 | 2.6절을 "1차 업데이트 실행"으로 교체: 11-04 트랙 분석(bsw4/5) 반영 — 조향 7점 비선형 표 + 영점, 가속 히스테리시스(on/off 161회/분 해소). 분석 보고서 저장소 반입 |
