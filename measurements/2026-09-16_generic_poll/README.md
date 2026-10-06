# 2026-09-16 1-1b — D1 최종 대조군: generic 6.8 + 격리 + preempt=full + 전원관리 차단 (B 와 같은 두 옵션)

## 왜
1-1(9/14)의 generic 대조군(935 µs)은 전원관리가 켜진 상태였다. 1-6e 로 RT 쪽 스파이크의 필요조건이 `idle=poll` + `intel_pstate=disable` 조합임이 확정됐으므로,
"generic 도 같은 두 옵션을 주면 RT 와 같아지는가"를 확인해야 D1(RT 유지)이 최종이 된다. generic 이 같다면 NVIDIA 비지원 빌드 위험을 없앨 수 있었다.

## 조건
- GRUB 항목 `a1-bsw-generic-poll` (`a1_rt.sh tune generic-poll`): 6.8.0-138-generic + RT 튜닝 파라미터 + `preempt=full idle=poll intel_pstate=disable cpufreq.default_governor=performance max_cstate=0`.
- 부팅 16:58, 측정 17:02~17:32 **무인**. 확인: cpuidle 없음, acpi-cpufreq/performance, 격리 클럭 MSR 2,162~2,463 MHz, SMI 0(217→217), 스로틀 0, GPU 80 %, 패키지 46~50 °C, 커널 경고 0.
- 부하·측정 방법은 모든 30분 soak 와 동일(glmark2 4K refract + stress-ng, cyclictest 8스레드 FIFO 99, 격리 8-15).

## 결과 — `soak_20260916_170210.*`

| | **RT + poll (B 재현, 9/16)** | **generic + poll (이번)** | generic + PM 켜짐 (9/14) |
|---|---|---|---|
| 최악 | 157 µs | **514 µs** | 935 |
| 평균 | 4.2 | 3.4 | — |
| 50–100 µs | 0 | **1,413** | — |
| 100–200 | 2 | **455** | — |
| 200 µs 초과 | **0** | **21** | — |
| 400 µs 초과 | **0** | **4** | — |

스레드별: 50–100 µs 가 8코어에 148~196 회씩 **균등** — 특정 코어 문제가 아니라 generic 커널 전체의 꼬리. 200 µs 초과 21회도 균등.

## 판정 — **D1 최종: RT 커널 유지**
- 같은 격리·같은 두 전원관리 옵션에서 generic 은 50 µs 초과 **1,889회**(RT 2회), 200 µs 초과 21회(RT 0). 예산 D4 "99.99 % ≤ 100 µs" 기준으로는 1,868 / 1.8 M = 0.1 % 로 **10배 초과**. 최악 514 µs 도 1 ms 안이지만 분포가 다르다.
- 전원관리 차단이 generic 에도 효과는 있었다(935 → 514, 즉 두 옵션은 커널과 무관하게 필요조건). 그러나 generic 의 `preempt=full` 은 스핀락·softirq 구간 등 선점 불가 구간을 그대로 남기므로 수십~수백 µs 꼬리가 남는다. 이것이 PREEMPT_RT 가 해결하는 부분이며, 그 차이가 측정으로 나타났다.
- RT 유지의 비용(NVIDIA 470 이 RT 를 공식 지원하지 않아 `IGNORE_PREEMPT_RT_PRESENCE=1` 로 빌드, GPU 프로세스 비정상 종료 시 `scheduling while atomic` 경고 관측)은 그대로 안고 간다 → 운용 중 GPU 프로세스는 정상 종료 경로만 쓰고, 경고 발생 시 재부팅 절차를 운용 문서에 넣는다.

## 운용 부팅 확정
**`a1-bsw-rt-poll`** = 6.8.1-1059-realtime + 격리 8-15 + `idle=poll intel_idle.max_cstate=0 processor.max_cstate=0 intel_pstate=disable cpufreq.default_governor=performance`.
다음: 폴링 비용 절감(`iso_pm.sh eco 800 smt-off`) 30분 검증 → 전력 표 → 8시간.

## 메모 (측정 무결성)
- 이번 soak 실행 중(17:13) 제가 `a1_rt.sh` 를 편집했다. bash 는 스크립트를 실행하며 파일을 계속 읽기 때문에, 측정·정리가 모두 끝난 뒤 마지막 디스패치 줄에서 `line 760: syntax error` 가 한 번 찍혔다(run.log 마지막 줄). 결과 행·GPU 180회·열 180회·부하 종료는 모두 정상 완료됐고 결과값에는 영향 없음. **실행 중인 스크립트는 편집하지 않는다** — 이후 규칙.
