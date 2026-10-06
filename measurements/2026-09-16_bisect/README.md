# 2026-09-16 1-6e 이분 탐색 — B(rt-poll, 43 µs)를 두 성분으로 나눠 어느 쪽이 효과인지

B = RT 튜닝 + `idle=poll` + `intel_pstate=disable` + `max_cstate=0` → 30분 최악 43 µs, 50 µs 초과 0 (9/15).

| 항목 | B1 `a1-bsw-rt-nohwp` | B2 `a1-bsw-rt-idlepoll` |
|---|---|---|
| B에서 가져온 것 | `intel_pstate=disable cpufreq.default_governor=performance` (HWP 끔, 유휴 경로는 기본 = intel_idle POLL) | `idle=poll` + `max_cstate=0` (HWP 켜짐) |
| 상태 | **완료 (11:50~12:20)** | **완료 (14:18~14:48)** |

## B1 결과 — `soak_20260916_115016.*`
- 조건 확인: cpufreq=**acpi-cpufreq/performance**(HWP 없음), cpuidle=[POLL], 격리 클럭 MSR 2,107~2,470 MHz(acpi-cpufreq `boost=1` 이라 기준 2.1 GHz 위로 소폭 변동), SMI 0, 스로틀 0, GPU 79 %, 패키지 40~46 °C.
- **최악 640 µs, 평균 4.0 µs, 400 µs 초과 6회(에피소드 2개: 사이클 205,3xx~205,4xx 에 4건 동시, 929,65x 에 2건 동시), 200 µs 초과 21회, 50 µs 초과 94회.**

| | A 기본 | **B 전역** | C3 전코어 고정(런타임) | **B1 HWP 끔** |
|---|---|---|---|---|
| 최악 | 467 | **43** | 837 | **640 µs** |
| 50 µs 초과 | 102 | **0** | 216 | **94** |
| 200 µs 초과 | 26 | 0 | 164 | **21** |
| 400 µs 초과 | 2 | 0 | 119 | **6** |

## 판정 (B1 단독) — B1 ≈ A. HWP 끔만으로는 효과 없음.

## B2 결과 — `soak_20260916_141822.*`
- 부팅(journal boot 0, 14:13): RT 튜닝 + `idle=poll intel_idle.max_cstate=0 processor.max_cstate=0`. 확인: cpuidle 드라이버 **없음**(`cpuidle=[]`), cpufreq=**intel_pstate/powersave**(HWP 켜짐), 격리 클럭 MSR 2,294~2,470 MHz, SMI 0(누적 213→213), 스로틀 0, GPU 79 %, 패키지 44~48 °C, 커널 경고 0. 부팅 시 `WARNING: polling idle and HT enabled, performance may degrade`(B 부팅에도 동일하게 있었음).
- **최악 813 µs(CPU 15), 평균 4.1 µs, 50 µs 초과 192회, 200 µs 초과 85회, 400 µs 초과 31회.** 200 µs+ 가 8코어에 균등(8~13회) — A/C3 와 같은 패키지형 분포.

| | A 기본 | **B 전역** | **B1 HWP 끔만** | **B2 idle=poll 만** |
|---|---|---|---|---|
| 부팅 성분 | — | `idle=poll` + `max_cstate=0` + `intel_pstate=disable` + gov=performance | `intel_pstate=disable` + gov=performance | `idle=poll` + `max_cstate=0` |
| cpuidle / cpufreq | POLL / intel_pstate(HWP) | 없음 / acpi-cpufreq perf | POLL / acpi-cpufreq perf | 없음 / intel_pstate(HWP) powersave |
| 최악 | 467 | **43** | 640 | **813 µs** |
| 50 µs 초과 | 102 | **0** | 94 | **192** |
| 200 µs 초과 | 26 | 0 | 21 | **85** |
| 400 µs 초과 | 2 | 0 | 6 | **31** |

(B 의 부팅 파라미터는 `journalctl -b -6` 로 재확인: `idle=poll intel_pstate=disable cpufreq.default_governor=performance max_cstate=0` 모두 포함, `process: using polling idle threads`. B 의 세션 로그는 당시 파일명 충돌 버그로 유실됐지만 부트 자체는 확실함.)

## 판정 (이분 탐색 종료)
- **두 성분 어느 쪽도 단독으로는 B 를 재현하지 못했다.** B1 640 / B2 813 ≈ A 계열(467~995). 이분 탐색이 "둘 중 하나"를 가려내지 못했으므로 남는 해석은 둘:
  1. **상호작용**: `idle=poll` 과 `intel_pstate=disable` 이 **함께** 있어야 사라진다. (예: HWP 가 켜진 상태에서 8코어 폴링은 HWP 에게 "100 % 부하"로 보여 HWP 자율 제어가 개입하고, acpi-cpufreq 만으로는 cpuidle POLL 진입·탈출 경로가 남는다 — 두 경로 모두 막혀야 한다는 가설. **검증 안 됨.**)
  2. **B 가 재현되지 않는 단일 표본**: B 는 30분 1회다. A 계열 변동폭(467~995)에 비해 43/0건은 질적으로 다르지만, 1회 측정으로 "원인 확정"이라고 쓴 9/15 README 판정은 **과했다.**
- 두 해석을 가르는 실험은 **B 재현(rt-poll 부팅 → soak 30 을 한 번 더)** 이다. 다른 어떤 새 실험보다 먼저 해야 한다.
  - 재현 ≈43 µs → 해석 1 채택, **운용 설정 = B 그대로**(전역 `idle=poll` + `intel_pstate=disable`). 비용: 전 코어 폴링 + 하우스키핑 터보/절전 없음 → 2-1 인지 처리량을 이 설정에서 측정해 판단.
  - 재현 실패(A 계열) → 9/15 "원인 = 전원관리 확정" 판정 **철회**. 전원관리 계열(C-state·클럭·HWP·유휴 경로) 전체가 소거되고, 남는 방향은 (a) GPU 부하가 커널 전역에 일으키는 사건(RCU·메모리 관리·NVIDIA 드라이버 콜백)의 ftrace 추적을 사건 창 밖까지 넓혀 다시 보기, (b) HT 끄기(BIOS, 범위 밖·승인 필요) 또는 격리 코어에서 SMT sibling 한쪽 offline(`echo 0 > /sys/devices/system/cpu/cpu{9,11,13,15}/online`, 런타임·재부팅 시 원복) 실험.
- 9/15 rt_poll README 의 "원인 = CPU 전원관리 — 확정", "`max_cstate=1` 로 부족했던 이유(패키지 C-state·HWP 전이)" 설명은 B1/B2/C2/C3 로 **근거를 잃었다**. B 재현 결과가 나오면 그 README 에 정정 문단을 넣는다.

## B 재현 결과 — `soak_20260916_145810.*` (rt-poll 재부팅 14:55, 측정 14:58~15:28)
- 조건 확인: `idle=poll intel_pstate=disable cpufreq.default_governor=performance max_cstate=0`, cpuidle **없음**, cpufreq **acpi-cpufreq/performance**(boost=1, 격리 클럭 MSR 2,199~2,528 MHz), SMI 0(218→218), 스로틀 0, GPU 79 %, 패키지 46~50 °C, 커널 경고 0. **무인 측정**(세션 확인 없음).
- **최악 157 µs(CPU 15), 평균 4.2 µs, 50 µs 초과 2회(CPU 12: 135, CPU 15: 157), 200 µs 초과 0, 400 µs 초과 0, 20–50 µs 19회.** 히스토그램에는 시각이 없어 두 사건이 동시였는지는 모른다.

| | A 계열 (5회) | **B (9/15)** | **B 재현 (9/16)** | B1 | B2 |
|---|---|---|---|---|---|
| 최악 | 467 ~ 995 | 43 | **157** | 640 | 813 |
| 50 µs 초과 | 94 ~ 216 | 0 | **2** | 94 | 192 |
| 200 µs 초과 | 21 ~ 164 | 0 | **0** | 21 | 85 |
| 400 µs 초과 | 2 ~ 143 | 0 | **0** | 6 | 31 |

## 최종 판정 (1-6e 종료)
- **B 는 재현된다** — 43 µs 그대로는 아니지만(157), 200 µs 초과 0·400 µs 초과 0 은 A 계열(항상 수십~수백 건)과 질적으로 다르다. 해석 2(단일 표본 우연) 기각.
- **상호작용 확정**: `idle=poll` 과 `intel_pstate=disable` 은 **둘 다 있어야** 효과가 난다(B1·B2 각 단독은 A 계열, B·B 재현은 둘 다 B 계열, 표본 2:2). 기전(HWP 가 폴링을 100 % 부하로 보고 개입 / cpuidle POLL 진입·탈출 경로)은 여전히 가설이고, 운용 결정에는 필요하지 않다.
- **B 재현의 100 µs 초과 2건**은 B 에는 없던 것. 예산 D4(99.99 % ≤100 µs, 최악 ≤1 ms)에는 2 / 1.8 M = 0.0001 % 로 여유 있게 들어오지만, 8시간에서 얼마로 늘어나는지는 봐야 한다 → 운용 설정으로 8시간 재검증이 다음 관문.
- 9/15 rt_poll README 의 "원인 = C-state / 주파수 전이 — 확정" 문구는 **틀렸다**: C-state 는 처음부터 POLL 만 있었고(C3), 클럭 고정도 무효(C2/C3), HWP 끔 단독도 무효(B1). 맞는 표현은 "**`idle=poll` + `intel_pstate=disable` 조합이 필요조건이며, 두 부팅 옵션이 각각 제거하는 경로(cpuidle 프레임워크 / intel_pstate·HWP)가 함께 작용해 수백 µs 사건을 만든다**"까지다.

## 운용 설정 결정 (제안)
- **운용 부팅 = `a1-bsw-rt-poll`** (RT 튜닝 + `idle=poll intel_idle.max_cstate=0 processor.max_cstate=0 intel_pstate=disable cpufreq.default_governor=performance`). GRUB 항목 이미 존재. 기본(default)은 여전히 generic 이므로 대회 운용 시 이 항목을 선택하거나, 승인 후 `GRUB_DEFAULT` 를 이 항목으로 바꾼다.
- 비용 재평가: (1) 전 코어 유휴 시 100 % 폴링 → 전력·발열(패키지 +3~4 °C, 스로틀 0). (2) 하우스키핑 터보: acpi-cpufreq 는 `boost=1` 로 터보를 **허용**한다(B1·B 재현 모두 격리 코어가 기준 2.1 GHz 위 2.5 GHz 까지 올라감) → "터보 상실"이라는 9/15 표기는 부정확. 다만 하우스키핑 절전(P-state 하향)은 governor=performance 로 사라진다. **2-1 인지 처리량은 이 부팅에서 측정**하면 된다.
- 하우스키핑 코어를 `powersave`/`schedutil` governor 로 돌리는 부분 완화는 가능하지만, 격리 코어 지연에 영향이 없는지 별도 30분이 필요 → 처리량이 문제 될 때만.

## 다음
1. **8시간 재검증 (운용 설정)**: rt-poll 부팅 상태 그대로 `soak 480`, 무인. 판정: 최악 ≤1 ms, 100 µs 초과 ≤0.01 % (≈ 2,900 건 이하 / 8 h 라 사실상 여유), 200 µs 초과 건수 기록.
2. **1-1b**: generic-tuned 부팅에 같은 두 옵션을 추가한 항목(`tune generic-poll`, 신설 필요)으로 30분 → D1 최종. generic 이 B 계열이면 RT 를 버리고 NVIDIA 비지원 빌드 위험을 없앨 수 있다.
3. A-3 systemd 유닛(격리 배치·FIFO) 설계에 "부팅 항목 = rt-poll" 을 전제로 넣는다. `iso_pm.sh` 런타임 잠금은 이 부팅에서는 불필요.
