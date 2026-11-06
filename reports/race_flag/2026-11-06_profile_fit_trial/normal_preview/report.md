# 가감속 프로파일 추천 — 2026-11-06

**미리보기(--can-only-preview) — 갱신 판정 안 함, 아래 값은 참고용**

커밋 `2f1fe3c` · 도구 `tools/race_flag/fit_long_profiles.py` · 기존 값 `safety/can_guard/race_flag.yaml` (읽기 전용)

## 입력

| 로그 | 프레임 | 길이 s | 시작 unix |
|---|---|---|---|
| auto_bsw3/kvaser_ch0.log.zst | 208861 | 366 | 1793778835.4 |
| remote_1140/kvaser_ch0_listen.log.zst | 1113179 | 1919 | 1793760047.7 |
| quick_ADAPTER-LEFT_s1/kvaser_ch0.log.zst | 18846 | 33 | 1793775578.1 |
| quick_ADAPTER-LON_s1/kvaser_ch0.log.zst | 29938 | 54 | 1793775467.0 |
| quick_ADAPTER-LON_s2/kvaser_ch0.log.zst | 14550 | 26 | 1793775677.8 |
| quick_ADAPTER-SCEN-LEFT_s1/kvaser_ch0.log.zst | 20680 | 36 | 1793775818.3 |
| quick_QUICK-LON_s4/kvaser_ch0.log.zst | 44493 | 78 | 1793774410.6 |
| quick_QUICK-LON_s5/kvaser_ch0.log.zst | 37324 | 66 | 1793774489.8 |
| quick_QUICK-LON_s6/kvaser_ch0.log.zst | 109302 | 189 | 1793774807.8 |
| quick_QUICK-STEER-LON_s3/kvaser_ch0.log.zst | 177294 | 317 | 1793774092.2 |

## 검사(계획서 7.2)

| 검사 | 결과 | CAN 만으로 판정 | 내용 |
|---|---|---|---|
| GNSS 품질 | 확인 불가 | 불가 | GNSS 입력 없음(--bag/--gnss-csv 미지정) |
| 다른 조작 섞임(앱·RES·0x210 송신자) | 통과 | 가능 | 앱 En=0, RES 없음, 0x210 초당 프레임 정상 |
| 다른 조작 섞임(ESTOP) | 확인 불가 | 불가 | can_guard 이벤트 기록(--events)이 없는 로그: remote_1140/kvaser_ch0_listen.log.zst, quick_ADAPTER-LEFT_s1/kvaser_ch0.log.zst, quick_ADAPTER-LON_s1/kvaser_ch0.log.zst, quick_ADAPTER-LON_s2/kvaser_ch0.log.zst, quick_ADAPTER-SCEN-LEFT_s1/kvaser_ch0.log.zst, quick_QUICK-LON_s4/kvaser_ch0.log.zst, quick_QUICK-LON_s5/kvaser_ch0.log.zst, quick_QUICK-LON_s6/kvaser_ch0.log.zst, quick_QUICK-STEER-LON_s3/kvaser_ch0.log.zst |
| 물리 한계 | 통과 | 가능 | 후륜 0x201(GNSS 없음) 최대 가속 1.35 m/s² (기준 ≤ 1.6), 최대 감속 3.59 m/s² (기준 ≤ 4.5) |
| 센서 불일치 | 확인 불가 | 불가 | GNSS 없음 — 휠속과 비교할 수 없음 |
| 시간 어긋남 | 확인 불가 | 불가 | GNSS 없음 — CAN 로그와 bag 의 시각 차이를 확인할 수 없음 |
| 표본 부족 | 걸림 | 가능 | 4개 구간이 3 이벤트 미만 |
| 경사 | 확인 불가 | 불가 | GNSS 입력 없음(--bag/--gnss-csv 미지정) |
| 급변 | 통과 | 가능 | 모든 추천값 변화 ≤ 20 % |

## 추천값 (참고)

| 항목 | 기존 | 추천 | 변화 | 근거 |
|---|---|---|---|---|
| a_slow (m/s²) | 2.00 | 2.36 | 18 % | 잠김 판정 불가(GNSS 없음): 표본이 충분한 구간의 최대 감속 기준 |
| slow_off_ramp_pct_s (%/s) | 50.0 | — | — | 슬립 10 % 이하가 표본 3회 이상 확인된 상승률 구간이 없음 |

잠김 시작: — % · 휠스핀 시작: — %

```yaml
# 추천 표 (참고) — 사람이 승인한 뒤에만 race_flag.yaml 에 옮긴다
brake_map: {"decel_mps2": [0.1, 2.95], "brake_pct": [0.0, 100.0]}
accel_map: {"accel_mps2": [0.02, 0.24, 0.64], "acc_pct": [81.0, 87.0, 100.0]}
```

## 브레이크 % → 감속도 (참고)

| 구간 % | 이벤트 | 중앙 % | 감속 m/s² p50 | p25 | p75 |
|---|---|---|---|---|---|
| 1~20 | 0 | — | — | — | — |
| 20~40 | 0 | — | — | — | — |
| 40~60 | 2 | 55 | 1.63 | 1.47 | 1.79 |
| 60~82 | 0 | — | — | — | — |
| 82~101 | 3 | 100 | 2.95 | 2.90 | 3.11 |

## 가속 % → 가속도 (참고)

| 구간 % | 이벤트 | 중앙 % | 가속 m/s² p50 | p25 | p75 |
|---|---|---|---|---|---|
| 75~85 | 7 | 81 | 0.02 | -0.03 | 0.06 |
| 85~95 | 11 | 87 | 0.24 | 0.08 | 0.28 |
| 95~101 | 79 | 100 | 0.64 | 0.51 | 0.80 |

## 가속 상승률 → 슬립

| 상승률 %/s | 이벤트 | 슬립을 잰 이벤트 | 최대 슬립 % |
|---|---|---|---|
| 0~25 | 0 | 0 | — |
| 25~50 | 2 | 0 | — |
| 50~100 | 10 | 0 | — |
| 100~200 | 28 | 0 | — |
| 200~계단 | 222 | 0 | — |

## 그 밖의 값

- 이벤트 수: 브레이크 5, 가속 104, 가속 상승 262 (`segments.csv`)
- 브레이크 지연(명령 계단 → 0x200 위치 +2 %): 중앙값 120 ms, p90 196 ms, 24회
- 0x201 ÷ GNSS 정속 비율: 측정 불가(GNSS 없음), 가감속 계산에 11-04 값 0.958 사용
- 시간 차(후륜 대 GNSS): —

## 서행 단계(계획서 6.3)

도구 판정 단계 **1** (승인 전까지 yaml·CLI 는 그대로) · 서행 구간 0회 · 흔들림 p99.9 최대 — km/h · 휠-GNSS 차이 측정 불가

## 근거 그래프

![브레이크](brake_map.png)

![가속](accel_map.png)

![타임라인](timeline.png)
