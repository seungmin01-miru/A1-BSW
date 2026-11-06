# tools/race_flag — 가감속 프로파일 추천 (계획서 7절, D9)

주행 로그에서 `safety/can_guard/race_flag.yaml` 값을 **추천만** 한다. yaml 과 이력 문서는 고치지 않는다.
읽기 전용 분석이고 CAN 에 닿지 않는다. 판정은 대회 PC 결과로 한다(개발 PC 결과는 참고).

```bash
# 정식: CAN + GNSS bag (bag 읽기는 ROS·메시지 정의가 있는 대회 PC 에서)
python3 tools/race_flag/fit_long_profiles.py --can <로그 또는 세션 폴더>... --bag <bag> --out reports/race_flag/<날짜>_profile_fit
# bag 대신 GNSS CSV (tools/drive_log/extract.py 의 INS 출력 또는 t,speed_mps,fix,pitch_deg)
python3 tools/race_flag/fit_long_profiles.py --can <로그> --gnss-csv <csv> --out <폴더>
# GNSS 없이 CAN 통계만: 갱신 판정 없음, 종료 코드 0, 값은 전부 '참고'
python3 tools/race_flag/fit_long_profiles.py --can <로그> --can-only-preview --out <폴더>
# 시험
python3 -m pytest -q tools/race_flag/test_fit_long_profiles.py
```

| 종료 코드 | 뜻 | 다음 행동 |
|---|---|---|
| 0 | 전 검사 통과(또는 미리보기) | 통과면 `report.md`·그래프를 사람이 확인·승인 → yaml 을 고침 → `docs/race_flag_profile_history.md` 에 기록 |
| 2 | 입력·사용법 오류 | 경로·형식 확인 |
| 3 | ⛔ 갱신 중단 | `UPDATE_BLOCKED.md` 를 보고 원인 해결. 그때까지 값을 바꾸지 않음 |

입력: CAN 로그는 평문 또는 `.zst`(`(ts) ch ID#DATA`). 세션 폴더를 주면 로그 하나만 고른다(미러 기록 우선, `cannpz.py` 와 같음 — candump 와 같은 프레임이라 합치지 않는다).
`--events` 는 can_guard `--event-log` CSV 이고, 안 주면 CAN 로그 옆의 `can_guard_events*.csv` 를 찾는다.
재사용: CAN 해석 `tools/signal_audit/dbc_lite.py`, bag 읽기 `tools/track_day/fit_maps.py` 의 `read_bag`(헤더 stamp 기준).

출력(`--out`): `report.md`(검사표·추천값·표), `summary.json`, `segments.csv`(쓴 구간 전부), `brake_map.png`, `accel_map.png`, `timeline.png`, `slip.png`(GNSS 있을 때), 막히면 `UPDATE_BLOCKED.md`.

## 오염 검사 (7.2) — 하나라도 '통과'가 아니면 종료 코드 3

**확인할 수 없는 것은 통과가 아니다.** 계획서가 시간 어긋남에 쓴 "확인할 수 없거나"를 모든 검사에 적용한다.

| 검사 | 기준(잠정) | 어떻게 재나 | 확인 불가가 되는 경우 |
|---|---|---|---|
| GNSS 품질 | 분석 구간 fix ≥ 90 % | INS `status.gnss` ∈ `--fix-values`(기본 4 = RTK Fixed) | GNSS 없음, fix 정보 없음(odom 만 있는 bag) |
| 다른 조작 섞임(CAN) | 없을 것 | 0x156 En(EPS·ACC·AEB), 0x127 RES_START, 0x210 초당 > 70 프레임(송신자 둘), 이벤트 `foreign_0x210` | — |
| 다른 조작 섞임(ESTOP) | 없을 것 | can_guard 이벤트 `estop_request`, `->ESTOP` | 이벤트 기록 없음(`estop.py` 원천은 CAN 에서 구별 안 됨) |
| 물리 한계 | 감속 ≤ 4.5, 가속 ≤ 1.6 m/s² | GNSS(없으면 후륜) 미분, 평활은 `drive_log/remote.py` 와 같음 | — |
| 센서 불일치 | 정속 구간 휠-GNSS ≤ 5 % | 3 s 이상, 5 km/h 이상, \|a\| ≤ 0.1. 어느 한 구간이라도 넘으면 걸림 | GNSS 없음, 정속 구간 없음 |
| 시간 어긋남 | ≤ 0.05 s | 후륜·GNSS 가속도 상호상관(±2 s, 슬립 구간 제외) | GNSS 없음, 겹침 < 20 s, 상관 < 0.7, 최대점이 탐색 범위 끝 |
| 표본 부족 | % 구간별 ≥ 3 이벤트 | 브레이크 1~20·20~40·40~60·60~82·82~100, 가속 75~85·85~95·95~100 | — |
| 경사 | 분석 구간 \|피치\| ≤ 2° | INS 피치 | 피치 없음, 평면 odom(피치 전부 0 은 측정으로 치지 않음) |
| 급변 | 추천 변화 ≤ 20 % | a_slow, 램프, 표의 각 점 | 걸리면 **사람 확인 요청**, 도구가 판정하지 않음 |

CAN 만으로 판정되는 것은 다른 조작(CAN)·물리 한계·표본 부족·급변이다. `--can-only-preview` 는 이 구분을 보여 주고 나머지는 판정하지 않는다.

## 추천 규칙 (7.1)

- **이벤트:** 명령이 ±3 % 안에서 유지되고 다른 축이 0 인 구간(브레이크 1 s, 가속 1.5 s 이상). 앞부분(0.4 / 0.5 s)을 버리고 속도 기울기를 구한다(최대 4 s). 브레이크 3 km/h 미만은 뺀다.
- **a_slow** = 잠기기 직전 구간 감속도 × 0.8. 표본 3회 미만 구간은 근거로 쓰지 않는다.
- **slow_off_ramp_pct_s** = 슬립 ≤ 10 % 가 3회 이상 확인된 가장 빠른 상승률. 계단(한두 프레임에 20 %p 이상 상승)까지 안전하면 무한(램프 없음). 슬립 초과가 나온 구간부터 위는 쓰지 않는다.
- **서행 단계(6.3):** 후륜 속도 기준 정속 구간 수, 흔들림 p99.9, 휠-GNSS 차이. 3단계 수치를 채워도 KIAPI 질의서 7 답변 전에는 올리지 않는다. 단계 변경은 사람이 한다.

### 이 도구에서 정한 값(계획서에 숫자 없음, 잠정)

| 값 | 근거 |
|---|---|
| 잠김 슬립 20 % | 7.1 은 "잠김 시작"만 정의. 실측 잠김 사례가 아직 없다 → 첫 실데이터로 재확인 |
| 슬립률을 정속 비율로 보정 | 계획서 식은 (후륜 − GNSS) ÷ GNSS. 0x201 이 4.2 % 낮게 읽으므로 그대로 쓰면 휠스핀이 작아 보여 위험한 쪽으로 틀린다. 안전한 쪽(후륜 ÷ 비율)으로 계산한다 |
| 0x210 초당 70 프레임 | 11-04: 혼자 47~51, bsw5 겹침 최대 97 |
| fix 값 4 | 11-04 전 구간 RTK Fixed(4). RTX 코드는 TODO-5 확정 시 `--fix-values` |

## 한계

- GNSS 토픽 이름은 TODO-5 미확정이다(`--gnss-topic`, `--ins-topic`). INS 메시지 필드(`total_speed`, `status.gnss`, `pitch`)는 대회 PC 메시지 정의로 **아직 시험하지 않았다**. odom 경로만 합성 bag 으로 시험했다.
- 저장소에 GNSS bag 이 없어 GNSS 경로는 합성 로그로만 시험했다. 합성 차량 값은 실측이 아니다.
- 시간 차 값에는 기록 시각 차이와 센서 필터 지연이 같이 들어 있어 구분되지 않는다.
- 경사 미보정. 표는 코너·속도대가 섞인 값이다(`segments.csv` 의 `speed_kph` 로 나눠 볼 수 있음).
