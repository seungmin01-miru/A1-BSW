# 2026-09-16 1-6e 이분 탐색 — B(rt-poll, 43 µs)를 두 성분으로 나눠 어느 쪽이 효과인지

B = RT 튜닝 + `idle=poll` + `intel_pstate=disable` + `max_cstate=0` → 30분 최악 43 µs, 50 µs 초과 0 (9/15).

| 항목 | B1 `a1-bsw-rt-nohwp` | B2 `a1-bsw-rt-idlepoll` |
|---|---|---|
| B에서 가져온 것 | `intel_pstate=disable cpufreq.default_governor=performance` (HWP 끔, 유휴 경로는 기본 = intel_idle POLL) | `idle=poll` + `max_cstate=0` (HWP 켜짐) |
| 상태 | **완료 (11:50~12:20)** | 대기 |

## B1 결과 — `soak_20260916_115016.*`
- 조건 확인: cpufreq=**acpi-cpufreq/performance**(HWP 없음), cpuidle=[POLL], 격리 클럭 MSR 2,107~2,470 MHz(acpi-cpufreq `boost=1` 이라 기준 2.1 GHz 위로 소폭 변동), SMI 0, 스로틀 0, GPU 79 %, 패키지 40~46 °C.
- **최악 640 µs, 평균 4.0 µs, 400 µs 초과 6회(에피소드 2개: 사이클 205,3xx~205,4xx 에 4건 동시, 929,65x 에 2건 동시), 200 µs 초과 21회, 50 µs 초과 94회.**

| | A 기본 | **B 전역** | C3 전코어 고정(런타임) | **B1 HWP 끔** |
|---|---|---|---|---|
| 최악 | 467 | **43** | 837 | **640 µs** |
| 50 µs 초과 | 102 | **0** | 216 | **94** |
| 200 µs 초과 | 26 | 0 | 164 | **21** |
| 400 µs 초과 | 2 | 0 | 119 | **6** |

## 판정
- **B1 ≈ A.** HWP 를 끄는 것만으로는 효과가 거의 없다(A 와 같은 수준). → **B 의 효과는 `intel_pstate=disable` 에서 온 것이 아니다.**
- 남은 후보 = **`idle=poll`(+`max_cstate=0`)**, 즉 B2. 기본 튜닝의 유휴 상태도 "POLL 하나"였지만, 그것은 **cpuidle 프레임워크가 매번 POLL 을 선택해 진입/탈출하는 경로**이고, `idle=poll` 은 cpuidle 을 거치지 않고 커널이 직접 스핀한다. 두 경로의 차이(cpuidle 진입·탈출, governor 판단, `poll_idle` 의 시간 제한 후 재진입, RCU/tick 처리 등)가 수백 µs 사건과 관련 있을 가능성.
- B1 의 400 µs 초과 6건도 두 순간에 여러 코어가 동시에 겹치는 패키지형 사건 → 원인 메커니즘은 A 와 같음.

## 다음
- **B2 실행**: `sudo grub-reboot a1-bsw-rt-idlepoll && sudo reboot` → `verify` → `soak 30`. ≈43 µs 이면 원인 = 유휴 경로 → 운용 설정 = RT 튜닝 + `idle=poll` (HWP 는 켠 채로 두어 하우스키핑 코어의 절전·터보 유지 가능 → 인지 처리량 손실 없음).
- B2 도 아니면 조합 효과 → B 그대로.
