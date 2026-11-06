# 00 SAF-ESTOP SIL 확인 — 2026-11-06 22:22:01

| 항목 | 값 |
|---|---|
| 호스트 | junhyeok-960QHA |
| 커밋 | 6c0826f |
| 커널 | 6.12.107-0612107-generic PREEMPT_DYNAMIC |
| isolcpus |  |
| python / python-can | 3.10.12 4.6.1 |
| can_guard 실행 형태 | 비격리 |
| 반복 | E1~E3·E5 각 20 회 |

| 시험 | 결과 | 로그 |
|---|---|---|
| unit_pytest | ✅ PASS | `unit_pytest.log` |
| sil_e_estop | ✅ PASS | `sil_e_estop.log` |
| sil_p1 | ✅ PASS | `sil_p1.log` |
| sil_p2 | ✅ PASS | `sil_p2.log` |
| unit_profile_lib | ✅ PASS | `unit_profile_lib.log` |
| sil_02_profile | ✅ PASS | `sil_02_profile.log` |
| sil_02_session | ✅ PASS | `sil_02_session.log` |
| rehearse_track | ✅ PASS | `rehearse_track.log` |

## 비상 정지 시나리오 상세
```
[E1] PASS  공유메모리 누름: n=20 중앙 19.9 / 최대 20.1 ms (기준 ≤ 40)
[E2] PASS  SIGUSR1: n=20 중앙 19.9 / 최대 20.4 ms (기준 ≤ 40)
[E3] PASS  원격 0x156 En=1: n=20 중앙 30.5 / 최대 31.2 ms (기준 ≤ 40)
[E4] PASS  래치·서서히 제동: 124 프레임, 브레이크 31→82 % 단조=True, 가속 최대 0, auto [(1, 1, 1)]
[E5] PASS  명령 끊김 → HOLDING: n=20 kill→HOLDING 첫 프레임 중앙 40.3 / 최대 60.2 ms, 마지막 명령 기준 ≤ 80.2 ms (기준 ≤ 100)
[E6] PASS  silent INIT: 명령 없이 1 s: 0x210 0 프레임
[E7] PASS  원격 우선: 앱 En 중 조향 추종·해제 튐 없음: En 중 조향 +5.0→+5.0° (0x200 +5.0), 해제 뒤 최대 차이 0.0°, ESTOP 출력 유지 True, 프레임 50/50
전체: PASS
```

## 02 세션 SIL 상세
```
## A. 폐루프 계단 0→10 km/h ×2, 오프셋 +1.0°
| 세션 완주 | ✅ PASS | rc 0, 시험 끝 2/2 |
| can_guard 계속 ACTIVE(0x210 ≤ 60 ms, auto 111) | ✅ PASS | 프레임 1159, 최대 간격 21 ms, auto [7] |
| 가속·브레이크 동시 0건 | ✅ PASS | 0 건 |
| 0x210 = 명령 로그(1주기 안) | ✅ PASS | 프레임 1166: 직전 1166, 한 주기 전 0, 불일치 0 |
| 조향 오프셋 적용(시험 중 0x210 조향) | ✅ PASS | +1° → raw 10, 표본 33, 다름 0 |
| 회차 사이 브레이크 유지(≥ 30 %, 가속 0) | ✅ PASS | 대기 2 주기, 위반 0 |
| 속도 추종(가짜 차량, 로직 확인) | ✅ PASS | silA_r1: 오차 0.80 km/h, 최고 9.9; silA_r2: 오차 0.80 km/h, 최고 9.9 |
## B. 개루프 직진 acc 10 % 코스 50 m ×2, 오프셋 −1.0°
| 세션 완주 | ✅ PASS | rc 0, 시험 끝 2/2 |
| can_guard 계속 ACTIVE(0x210 ≤ 60 ms, auto 111) | ✅ PASS | 프레임 3306, 최대 간격 21 ms, auto [7] |
| 가속·브레이크 동시 0건 | ✅ PASS | 0 건 |
| 0x210 = 명령 로그(1주기 안) | ✅ PASS | 프레임 3312: 직전 3312, 한 주기 전 0, 불일치 0 |
| 조향 오프셋 적용(시험 중 0x210 조향) | ✅ PASS | -1° → raw -10, 표본 119, 다름 0 |
| 회차 사이 브레이크 유지(≥ 30 %, 가속 0) | ✅ PASS | 대기 2 주기, 위반 0 |
| 코스 50 m 안에서 정지(후륜 적분) | ✅ PASS | 47.3 m, 47.3 m |
## C. 출발 안 함 감지: 직진 acc 3 %(가짜 차량 불감대 5 % 아래) ×2
| 출발 안 함 → 5 s 안에 회차 끝, 다음 회차 진행 | ✅ PASS | rc 0, 감지 2/2, 실행 단계 최대 5.3 s |
전체: PASS  (결과 폴더 /home/junhyeok/A1-BSW/bsw_test/00_SAF_estop/results/sil_20261106_222201_junhyeok-960QHA/sil_02_session)
```

## 판정 메모
- E1~E3 기준 ≤ 40 ms, E5 ≤ 100 ms. 실차 제동 시작(≤ 200 ms) = 여기 값 + 차량 수락(≤ 22 ms) + 브레이크 무반응(56~112 ms).
- 결과: PASS
