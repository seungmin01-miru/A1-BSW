# 2026-09-15 1-6b 전원관리 차단 실험 — 30분 soak (RT 튜닝 + idle=poll + intel_pstate=disable)

## 조건
- GRUB 항목 `a1-bsw-rt-poll` (`tools/rt/a1_rt.sh tune rt-poll`): 기존 RT 튜닝(격리 8-15 등)에
  `intel_idle.max_cstate=0 processor.max_cstate=0 idle=poll intel_pstate=disable cpufreq.default_governor=performance` 추가.
  부팅 후 확인: cpuidle 드라이버 **none**(C-state 0개), cpufreq 드라이버 **acpi-cpufreq / performance**, 격리 코어 클럭 2.2~2.5 GHz 유지.
- 부하: 30분 정식 측정(9/12)과 동일 — glmark2 4K refract(GPU 평균 78 %) + stress-ng CPU·메모리·I/O. 커널 경고 0, 스로틀 0, 측정 중 SMI 0회(MSR 0x34 누적 254→254).
- 파일: `soak_20260915_145155.log` / `.gpu_util` / `.thermal`

## 결과 — 스레드별 (9/12 정식 30분, 전원관리 기본 → 9/15 rt-poll)

| CPU | 50–100 µs | 100–200 | 200–400 | >400 | 최악 µs |
|---|---|---|---|---|---|
| 8 | 1 → 0 | 1 → 0 | 1 → 0 | 0 → 0 | 202 → **32** |
| 9 | 6 → 0 | 7 → 0 | 3 → 0 | 1 → 0 | 455 → **16** |
| 10 | 2 → 0 | 7 → 0 | 5 → 0 | 0 → 0 | 370 → **43** |
| 11 | 3 → 0 | 7 → 0 | 4 → 0 | 0 → 0 | 291 → **16** |
| 12 | 1 → 0 | 5 → 0 | 3 → 0 | 0 → 0 | 300 → **21** |
| 13 | 7 → 0 | 6 → 0 | 3 → 0 | 0 → 0 | 293 → **16** |
| 14 | 2 → 0 | 6 → 0 | 3 → 0 | 0 → 0 | 266 → **16** |
| 15 | 7 → 0 | 8 → 0 | 2 → 0 | 1 → 0 | 467 → **27** |
| **합** | 29 → **0** | 47 → **0** | 24 → **0** | 2 → **0** | 467 → **43** |

50 µs 초과 사건: **102회 → 0회**. 20–50 µs: 21 → 7. 평균 4.2 µs(변화 없음).

## 판정: **원인 = CPU 전원관리(C-state / 주파수 전이)** — 확정
- 같은 부하·같은 커널·같은 격리에서 전원관리만 끄니 50 µs 초과가 **하나도** 남지 않았다. 1-2 에서 본 "8코어가 같은 순간 늦게 깨는" 사건과, 8시간의 200 µs+ 공통 사건, 코어 6 증폭 모두 이 한 가지 원인으로 설명된다.
- 코어 6(CPU 12·13)도 다른 코어와 같은 16~21 µs 로 내려왔다 → 코어 6 "고유 결함"이 아니라 **전원관리 사건에 대한 코어별 복귀 시간 차이**(코어 6 이 가장 느림)였다. 코어 6 제외는 불필요.
- `max_cstate=1` 만으로는 부족했던 이유: 그 설정은 코어 C-state 를 C1 로 제한할 뿐, **패키지 C-state 진입과 intel_pstate(HWP) 주파수 전이는 그대로** 남는다. 8시간 .thermal 의 "800 MHz 고정" 은 HWP 아래에서 sysfs `scaling_cur_freq` 가 격리 코어에서 갱신되지 않는 자리표시자였다(실제 클럭은 MSR APERF/MPERF 로 재야 함 — `tools/rt/core_mhz.py`).
- 남은 3.5 µs 급 편차(평균 4.2)는 cyclictest 자체·타이머 해상도 수준.

## 비용 (운용 결정용)
- 격리 코어 8개가 항상 폴링(유휴 시에도 100 % 사용) + 최대 클럭 → 유휴 전력·발열 증가. 이번 30분: 패키지 45~49 °C(기본 튜닝 42~47 °C), GPU 84 °C, 스로틀 0 — 이 PC 냉각으로는 문제 없음.
- 하우스키핑 코어(0–7, 16–19)도 `idle=poll` 의 영향을 받는다(전 코어 폴링). **격리 코어에만 적용**하려면 부팅 파라미터 대신 sysfs(`/sys/devices/system/cpu/cpuN/cpuidle/stateM/disable`, `cpufreq` governor per-CPU) 로 코어별 설정 → A-3 systemd 유닛 설계에 포함.
- `intel_pstate=disable` 은 전역이라 하우스키핑 코어의 터보/절전도 사라진다. 대안: `intel_pstate=passive` + 격리 코어만 `performance` governor — 다음 실험 후보.
