# 논문용 수치 자료 — A1 by-wire CAN 인터페이스 검증·특성화

> 작성 2026-11-03. 실차 녹화(10-07 리프트, 11-03 원격 주행·자율 명령 정지 시험)를 논문 표·그림 형식으로 수치화한 것.
> 모든 표·그림은 아래 스크립트로 다시 만들 수 있다(손으로 고친 숫자 없음, 04 데이터시트만 출처 표를 옮겨 적음).

## 산출물

| # | 파일 | 내용 | 상태 |
|---|---|---|---|
| 표 1 | [`01_dbc_syntax_audit.md`](01_dbc_syntax_audit.md) | DBC 줄 단위 문법 대조(86줄 전부 O/X) + 의미 점검(오류·경고·참고) + 비트 사용표 + 업체 원본 대비 수정 차이 | ✅ |
| 그림 1 | `fig/fig1_bit_layout_user.png` | 0x210 / 0x200 / 0x201 비트 배치 | ✅ |
| 표 2 | [`02_signal_range_table.md`](02_signal_range_table.md) | 신호 53개: DBC 최소·최대 vs 실측 최소·최대(10-07 직접, 11-03 인용)·평균·p1/p99·활용률·범위 위반, 정의 밖 비트·DLC, live_counter | ✅ (11-03 평균·분위수 없음) |
| 표 3 | [`03_bus_timing.md`](03_bus_timing.md) | **송신 / 수신 / 제3자 분리** 주기 통계(평균·표준편차·p1~p99.9·최대·±5 % 비율), 실제 스터핑 반영 버스 부하 | ✅ (11-03 은 요약값) |
| 그림 2a~e | `fig/fig2*.png` | 주기 히스토그램(송신·수신·원격 3칸), 지터 CCDF, 주기 시계열, 세션 비교, 버스 부하 | ✅ |
| 표 4 | [`04_control_datasheet.md`](04_control_datasheet.md) | 제어 데이터시트: 조향·브레이크·구동·센서·통신·페일세이프·제어기 설정 제안·빈칸 | ✅ (중간 % 맵은 11-04) |
| 그림 3a~d | `fig/fig3*.png` | 조향 계단 응답, 브레이크 맵·비대칭 지연, 종방향 가감속, 종단 지연 예산 | ✅ |
| 데이터 | `data/*.csv` | 그림 3 의 원자료(논문 부록·다른 도구용) | ✅ |

## 다시 만들기

```bash
python3 tools/paper_data/dbc_lint.py DBC/A1_dbc.dbc DBC/A1_dbc_fixed.dbc --out reports/2026-11-03_paper_data
python3 tools/paper_data/bus_stats.py \
    --session 10-07=measurements/2026-10-07_vehicle_lift/capture \
    --summary 11-03=measurements/2026-11-03_signal_audit/results.md \
    --exclude 10-07:0x210:11:03:30-11:09:05 --out reports/2026-11-03_paper_data          # 약 20 s
python3 tools/paper_data/datasheet_figs.py --out reports/2026-11-03_paper_data
```
의존성: python3, numpy, matplotlib, Noto Sans CJK 글꼴(`tools/paper_data/_style.py`). cantools 불필요.

## 핵심 숫자 (본문 초안용)

- DBC: 문법 오류 0 (원본·수정본 모두). 의미 오류 1 — 원본 `0x200.steer_is_auto` 배율 0.1 (1비트 플래그) → 수정본에서 해결. 경고 6(단위 없음 4, 왼쪽 메시지에 `wheel_right_size`, SYS/USER `steer_command` 배율 불일치).
- 실측 89만 프레임(10-07): DLC 불일치 0, 정의 밖 비트 0, DBC 범위 위반 0 (원격 0x157 +1.17 m/s² 112건 제외).
- 주기: 송신 0x210 20.01 ms (p99.9 20.69), 수신 0x200/0x201 20.99 ms (최대 21.76), 원격 9.99 ms. 버스 부하 평균 6.4 %.
- 액추에이터: 조향 무반응 0.20 s·불감대 ±2°·오버슛 ≈ 3.5°·150° 90 % 0.73~0.87 s; 브레이크 위치 = %/10, 82 % 포화; 구동 출발 지연 0.62 s, 100 % 가속 +0.82 m/s²(전진).

## 추가로 만들면 좋은 것 (제안)

| 우선 | 항목 | 왜 | 데이터 | 상태 |
|---|---|---|---|---|
| ★★★ | **조향 플랜트 모델 식별** (지연 + 2차/가속도 제한 서보 맞춤, 적합도 R²) | MPC 모델 근거 — 논문에서 "식별된 모델로 시뮬레이션" 문장이 가능 | 11-03 `raw/l4h` 원본 시계열 | 원본 필요 |
| ★★★ | **명령 → 응답 지연 분포표**(auto 수락, 조향·브레이크 dead time, n·min·med·max) | 데이터시트 지연 값의 통계 근거 | 10-07(있음) + 11-03 l4h | 10-07 분은 analysis_report §1-2 |
| ★★ | **페일세이프 상태 천이도** (auto 비트 × 0x210 유무 × 원격 → 브레이크 동작) | 안전 기여 — 10-07 vs 11-03 거동 변화가 그대로 결과가 됨 | 04 §7 | 그림만 그리면 됨 |
| ★★ | **시험 매트릭스 표** (날짜·조건·녹화 길이·프레임·검증 신호 수) | 논문 "실험 방법" 절 | 각 세션 README | 정리만 |
| ★★ | **신호 검증 커버리지** (DBC 신호 46 + 원격 7 중 확인/미확인/버스에 안 보임) | "전수 검증" 주장 근거 | `cards.md`, `results.md` | 정리만 |
| ★ | 측정 불확도 표 (타임스탬프 분해능, 신호 분해능, 차분 창 길이에 따른 가속도 오차) | 리뷰어 질문 대비 | 04 §5 | |
| ★ | DBC 수정 제안본 v2 (단위·이름·GenMsgCycleTime·노드 추가) + 차이표 | 업체 전달·재현성 | 01 | |
| ★ | 운용 설정(격리 코어·FIFO) 0x210 지터를 같은 그림 2b 에 겹치기 | 기존 RT 논문 축(C1~C4)과 연결 | 다음 실차 | 미측정 |

## HANDOFF — 이 세션에서 못 한 것 (다른 세션용)

1. **11-03 원본이 이 PC 에 없다** (`measurements/2026-11-03_signal_audit/raw/` 는 gitignore, 해시만 `raw/README.md`).
   원본(`kvaser_ch0_listen.log`, `l4g/`, `l4h/` 의 `kvaser_ch0.log`)을 같은 경로에 복사한 뒤:
   ```bash
   python3 tools/paper_data/bus_stats.py \
       --session 10-07=measurements/2026-10-07_vehicle_lift/capture \
       --session 11-03=measurements/2026-11-03_signal_audit/raw \
       --exclude 10-07:0x210:11:03:30-11:09:05 --out reports/2026-11-03_paper_data
   ```
   → 표 2·3 이 11-03 도 평균·분위수까지 채워지고 그림 2d 가 원본 기반이 된다. `--summary 11-03` 은 빼기.
   주의: 11-03 원격 구간(15:56~16:33)의 0x210 송신자는 원격 유닛, 21:41~ 은 can_guard → `ROLE` 이 ID 고정이라
   **두 구간을 세션 둘로 나눠** 넣을 것(`--session 11-03R=…/raw --session 11-03S=…/raw/l4h` 처럼 폴더 분리).
   0x128·0x202·0x710~0x712(DBC 밖)는 표 3 에 "기타"로 나온다.
2. **조향 모델 식별**: `raw/l4h` 에서 `steer_sweep.sh` 계단(명령 3 s 유지)마다 0x200 위치 시계열을 잘라
   `θ(t) = A·(1 − …)` 대신 지연 τd + 2차 시스템(ωn, ζ) + 속도 포화 모델을 `scipy.optimize.least_squares` 로 맞추고,
   크기별 파라미터 표 + 측정/모델 겹친 그림(fig4). 검증은 10-07 계단(+1/+3/+8/+10°)에.
3. **11-04 주행 후**: T3·T4 결과(브레이크 %/가속 % ↔ m/s²)를 `datasheet_figs.py` 의 LONG 에 추가하거나 원본에서 직접 계산해
   04 §3·§4 "미측정" 칸과 §8 클램프 갱신. 메모리 규칙: 공회전 값을 m/s² 로 확정하지 말 것.
4. 04 데이터시트의 등급 B 값은 출처 md 를 옮겨 적은 것 — 원본을 구하면 A 로 재계산해 차이를 확인.
5. 페일세이프 상태 천이도·시험 매트릭스·커버리지 표(위 제안 ★★)는 기존 md 만으로 만들 수 있다.
