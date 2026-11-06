# ⛔ 갱신 중단 — race_flag.yaml 값을 바꾸지 않는다

작성 2026-11-06 18:00 · 커밋 2f1fe3c · `fit_long_profiles.py`, 계획서 7.2

입력: `auto_bsw3/kvaser_ch0.log.zst`, `remote_1140/kvaser_ch0_listen.log.zst`, `quick_ADAPTER-LEFT_s1/kvaser_ch0.log.zst`, `quick_ADAPTER-LON_s1/kvaser_ch0.log.zst`, `quick_ADAPTER-LON_s2/kvaser_ch0.log.zst`, `quick_ADAPTER-SCEN-LEFT_s1/kvaser_ch0.log.zst`, `quick_QUICK-LON_s4/kvaser_ch0.log.zst`, `quick_QUICK-LON_s5/kvaser_ch0.log.zst`, `quick_QUICK-LON_s6/kvaser_ch0.log.zst`, `quick_QUICK-STEER-LON_s3/kvaser_ch0.log.zst`

## 걸린 검사

### GNSS 품질 — 확인 불가

GNSS 입력 없음(--bag/--gnss-csv 미지정)

### 다른 조작 섞임(ESTOP) — 확인 불가

can_guard 이벤트 기록(--events)이 없는 로그: remote_1140/kvaser_ch0_listen.log.zst, quick_ADAPTER-LEFT_s1/kvaser_ch0.log.zst, quick_ADAPTER-LON_s1/kvaser_ch0.log.zst, quick_ADAPTER-LON_s2/kvaser_ch0.log.zst, quick_ADAPTER-SCEN-LEFT_s1/kvaser_ch0.log.zst, quick_QUICK-LON_s4/kvaser_ch0.log.zst, quick_QUICK-LON_s5/kvaser_ch0.log.zst, quick_QUICK-LON_s6/kvaser_ch0.log.zst, quick_QUICK-STEER-LON_s3/kvaser_ch0.log.zst

### 센서 불일치 — 확인 불가

GNSS 없음 — 휠속과 비교할 수 없음

### 시간 어긋남 — 확인 불가

GNSS 없음 — CAN 로그와 bag 의 시각 차이를 확인할 수 없음

### 표본 부족 — 걸림

4개 구간이 3 이벤트 미만

- 브레이크 1~20 %: 0회
- 브레이크 20~40 %: 0회
- 브레이크 40~60 %: 2회
- 브레이크 60~82 %: 0회

### 경사 — 확인 불가

GNSS 입력 없음(--bag/--gnss-csv 미지정)

## 전체 검사

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
