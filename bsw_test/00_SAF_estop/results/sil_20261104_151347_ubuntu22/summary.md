# 00 SAF-ESTOP SIL 확인 — 2026-11-04 15:13:49

| 항목 | 값 |
|---|---|
| 호스트 | ubuntu22 |
| 커밋 | de5f161 |
| 커널 | 6.8.1-1059-realtime PREEMPT_RT |
| isolcpus | 8-15 |
| python / python-can | 3.10.12 4.6.1 |
| can_guard 실행 형태 | --cpu 8 --rt-priority 90 |
| 반복 | E1~E3·E5 각 20 회 |

| 시험 | 결과 | 로그 |
|---|---|---|
| unit_pytest | ✅ PASS | `unit_pytest.log` |
| sil_e_estop | ❌ FAIL | `sil_e_estop.log` |
| sil_p1 | ✅ PASS | `sil_p1.log` |
| sil_p2 | ✅ PASS | `sil_p2.log` |
| unit_profile_lib | ✅ PASS | `unit_profile_lib.log` |
| sil_02_profile | ✅ PASS | `sil_02_profile.log` |
| sil_02_session | ✅ PASS | `sil_02_session.log` |
| rehearse_track | ✅ PASS | `rehearse_track.log` |

## 비상 정지 시나리오 상세
```
[E1] PASS  공유메모리 누름: n=20 중앙 19.9 / 최대 20.0 ms (기준 ≤ 40)
[E2] FAIL  SIGUSR1: 비상 정지 프레임이 1 s 안에 안 나옴
[E3] PASS  원격 0x156 En=1: n=20 중앙 30.9 / 최대 31.3 ms (기준 ≤ 40)
[E4] PASS  래치·서서히 제동: 126 프레임, 브레이크 31→82 % 단조=True, 가속 최대 0, auto [(1, 1, 1)]
[E5] PASS  명령 끊김 → HOLDING: n=20 kill→HOLDING 첫 프레임 중앙 60.0 / 최대 60.2 ms, 마지막 명령 기준 ≤ 80.2 ms (기준 ≤ 100)
[E6] PASS  silent INIT: 명령 없이 1 s: 0x210 0 프레임
전체: FAIL 1건
```

## 02 세션 SIL 상세
```
## A. 폐루프 계단 0→10 km/h ×2 + U턴 폐루프 5 km/h, 오프셋 +1.0°
| 세션 완주 | ✅ PASS | rc 0, 시험 끝 2/2, U턴 1/1 |
| can_guard 계속 ACTIVE(0x210 ≤ 60 ms, auto 111) | ✅ PASS | 프레임 2553, 최대 간격 21 ms, auto [7] |
| 가속·브레이크 동시 0건 | ✅ PASS | 0 건 |
| 0x210 = 명령 로그(1주기 안) | ✅ PASS | 프레임 2559: 직전 2559, 한 주기 전 0, 불일치 0 |
| 조향 오프셋 적용(시험 중 0x210 조향) | ✅ PASS | +1° → raw 10, 표본 33, 다름 0 |
| 회차 사이 브레이크 유지(≥ 30 %, 가속 0) | ✅ PASS | 대기 4 주기, 위반 0 |
| 속도 추종(가짜 차량, 로직 확인) | ✅ PASS | silA_r1: 오차 0.80 km/h, 최고 9.9; silA_r2: 오차 0.80 km/h, 최고 9.9 |
| U턴 방위 180 ± 15°(가짜 차량 자세) | ✅ PASS | silA_r1_turn: 178° |
## B. 개루프 직진 acc 10 % 코스 50 m ×2 + U턴 개루프 acc 10 %, 오프셋 −1.0°
| 세션 완주 | ✅ PASS | rc 0, 시험 끝 2/2, U턴 1/1 |
| can_guard 계속 ACTIVE(0x210 ≤ 60 ms, auto 111) | ✅ PASS | 프레임 4829, 최대 간격 21 ms, auto [7] |
| 가속·브레이크 동시 0건 | ✅ PASS | 0 건 |
| 0x210 = 명령 로그(1주기 안) | ✅ PASS | 프레임 4836: 직전 4836, 한 주기 전 0, 불일치 0 |
| 조향 오프셋 적용(시험 중 0x210 조향) | ✅ PASS | -1° → raw -10, 표본 119, 다름 0 |
| 회차 사이 브레이크 유지(≥ 30 %, 가속 0) | ✅ PASS | 대기 4 주기, 위반 0 |
| 코스 50 m 안에서 정지(후륜 적분) | ✅ PASS | 47.3 m, 47.3 m |
| U턴 방위 180 ± 15°(가짜 차량 자세) | ✅ PASS | silB_r1_turn: 174° |
## C. 출발 안 함 감지: 직진 acc 3 %(가짜 차량 불감대 5 % 아래) ×2, U턴 없음
| 출발 안 함 → 5 s 안에 회차 끝, 다음 회차 진행 | ✅ PASS | rc 0, 감지 2/2, 실행 단계 최대 5.3 s |
전체: PASS  (결과 폴더 /home/ailab/git/A1-BSW/bsw_test/00_SAF_estop/results/sil_20261104_151347_ubuntu22/sil_02_session)
```

## 판정 메모
- E1~E3 기준 ≤ 40 ms, E5 ≤ 100 ms. 실차 제동 시작(≤ 200 ms) = 여기 값 + 차량 수락(≤ 22 ms) + 브레이크 무반응(56~112 ms).
- 결과: FAIL 1 건
