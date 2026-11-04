# [SAF-ESTOP-01] 비상 정지 fallback — 테스트 목표

## 기본 정보
- 테스트 ID: SAF-ESTOP-01
- 담당자:
- 작성일: 2026-11-04
- 예정 테스트일:

## 목적
- 통신 끊김(heartbeat 시간 초과) 또는 비상 정지 신호 입력 시 can_guard 가 자동 제동하는지 확인한다.
- 이후 모든 주행 테스트의 선행 조건.

## 전제 조건
- [x] can_guard 에 비상 정지 구현 — `ESTOP` 상태, 원천: 핸드폰 앱(0x156/0x157)·`estop.py`·SIGUSR1 (2026-11-04)
- [x] 이벤트 로그 `--event-log` (estop_request 감지 지연, state, estop_first_tx) (2026-11-04)
- [x] 단위 시험 91개 통과, virtual 버스 스모크(감지 → 첫 ESTOP 프레임 약 10 ms) (2026-11-04)
- [ ] SIL(vcan) `sil_tests/e_estop.py` E1~E6 통과 — vcan0 생성(sudo) 필요
- [ ] **앱 비상 정지 버튼이 실제로 보내는 0x156/0x157 값** 확인(정지 상태, 듣기만) → `--estop-acc-mps2`·판정 조건 확정
- [ ] 앱이 0x156/0x157 을 계속(10 ms) 보내는지 → 데드맨 `--estop-can-timeout` 사용 여부
- [ ] 0x210 끊김 시 차량 동작 확인 (TODO-2, 실무자)
- [ ] 평지, 바퀴 굄(정지 단계)

## 입력 (명령 조건)
| 단계 | 차량 상태 | 트리거 | 횟수 |
|---|---|---|---|
| S0 | 정지, 바퀴 굄 | 명령 끊김(제어 노드 kill) / **앱 버튼** / `estop.py` / can_guard kill | 각 3 |
| S1 | 5 km/h 정속 | 명령 끊김 / **앱 버튼** / `estop.py` | 각 3 |
| S2 | 10 km/h 정속 | 명령 끊김 / **앱 버튼** / `estop.py` | 각 3 |

- can_guard 실행 옵션: `--event-log <raw>/날짜_SAF-ESTOP-01_rN_events.csv --estop-can-required`
- 제동은 서서히: ESTOP 30 % → 40 %/s → 82 %, 명령 끊김 30 % → 20 %/s → 60 % (잠정, 결과 보고 조정)

- 정속은 가속 % 고정(개루프). S0 → S1 → S2 순서, 앞 단계 전부 합격해야 다음 단계.

## 측정 신호
- [ ] 0x210 `break_command`, `acc_command`, `*_is_auto_command`
- [ ] 0x200 `break_postion`, `*_is_auto`
- [ ] 0x201 후륜 좌/우 `speed`
- [ ] can_guard 로그: heartbeat 마지막 수신 시각, 트리거 감지 시각, 상태 전이(HOLDING/STOPPED)
- [ ] (선택) GNSS/IMU 위치·속도·요레이트·가속도 — **토픽·시간 동기 미정(TODO-5, localization 팀 전달 대기)**

## 합격 기준
> 숫자는 **초기 가정치**(`../../docs/test_plan.md`). 첫 주행 뒤 조정 제안은 results.md "다음 조치"에.

| 지표 | 기준값 | 계산 방법 |
|---|---|---|
| 끊김 감지 지연 | ≤ 100 ms | 마지막 명령 → can_guard 상태 전이 (이벤트 로그 `state`) |
| 비상 정지 감지 지연 | 기록 | 이벤트 로그 `estop_request detect_lag_ms` (앱: 첫 원격 프레임 기준) |
| 제동 시작 지연 | ≤ 200 ms | 트리거 → 0x200 `break_postion` 상승 시작 |
| 정지까지 시간·거리, 휠락 여부 | 기록 | 후륜 속도 / GNSS, 후륜 속도 급락 구간 |
| 작동률 | 100 % (n/n) | 트리거 횟수 대비 제동 성공 |
| auto 비트 유지 | 정지 후에도 auto 000 미송신 | 0x210 `*_is_auto_command` |
| 정지 거리 | 기록만 (기준 없음) | 후륜 속도 적분 / GNSS |

## 안전
- 최대 속도 제한: ( 10 ) km/h  ← 공통 상한 30 km/h 안에서 단계적으로
- can_guard 클램프: `--steer-limit-deg 15` `--brake-limit-pct 100` `--acc-limit-pct 10`
- 비상정지 담당자:            / 정지 수단: (00 완료 전) stop.sh·can_guard kill + 물리 E-stop
- 종료 절차: **brake 100 유지한 채 stop.sh** (auto 000 금지 — 브레이크 풀림)
- 방향 전환(후진): 사람이 직접, 자율은 전진만
- 반복 횟수: 단계별 트리거마다 3회
