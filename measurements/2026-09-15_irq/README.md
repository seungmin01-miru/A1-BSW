# 2026-09-15 장치 IRQ 유입 계수 — 8시간 soak 의 "CPU 12·13 편중" 원인 판별

## 목적
8시간 soak(`../2026-09-14_rt/soak_20260914_212042.log`)에서 400 µs 초과 231회가 전부 CPU 12·13(물리 코어 6)에 몰린 것이
(A) 코어 고유 하드웨어 사건인지 (B) 장치 IRQ 가 격리 코어로 새는 것인지 판별. 특히 가설 "NVIDIA IRQ 가 CPU 13 에 붙어 있다"
(9/12 기록 "GPU IRQ = CPU 13"; `irqaffinity=` 부팅 파라미터는 부팅 후 로드된 모듈이 새로 등록한 IRQ 에는 적용되지 않음).

## 조건
- 부팅: `6.8.1-1059-realtime`, RT 튜닝 항목(`isolated=8-15`), 가동 13시간(8시간 soak 과 같은 부팅)
- irqbalance: **active** (stop/disable 은 sudo 필요 → 이번 세션에서 실행 못 함). 계수는 읽기 전용이라 배정 변경과 무관하게 "실제 발생 위치"를 세므로 결과에 영향 없음.
- GPU 부하: `glmark2 --off-screen --run-forever -s 3840x2160 -b refract`, 사용률 93~94 % 확인 후 계수. 계수 후 종료.
- 스냅샷: `interrupts_t0.txt` (09:17:49) → `interrupts_t1.txt` (09:18:49), 60 초 간격.

## 사전 상태 (계수 전 확인)
| 항목 | 값 |
|---|---|
| NVIDIA IRQ 219 | `smp_affinity_list=6`, `effective=6` → **CPU 6 (하우스키핑)**. "CPU 13" 가설은 현재 배정에서 불성립 |
| CPU 12·13 에 배정된 장치 IRQ | NVMe 큐 9 (IRQ 167 → CPU 12), NVMe 큐 10 (IRQ 168 → CPU 13) 뿐 |
| peak_pciefd (CAN, PCI 07:00.0) | IRQ 19, `effective=1` → 격리 밖 |

## 집계 (60 초, 장치 IRQ = 번호 행)
| IRQ | CPU 12 | CPU 13 | 격리 8–15 합 | 전체 | 장치 |
|---|---|---|---|---|---|
| 129 | 0 | 0 | 0 | 14,361 | xhci_hcd (USB) |
| **219** | **0** | **0** | **0** | **8,114** | **nvidia** — 전량 CPU 6 |
| 162/171/160/161 | 0 | 0 | 0 | 46/42/38/37 | nvme0 q4/q13/q2/q3 |
| 167 / 168 | 0 | 0 | 0 | 0 | nvme0 q9 / q10 (CPU 12·13 배정) — 발생 없음 |
| 19 | 0 | 0 | 0 | 0 | peak_pciefd (CAN, 버스 DOWN) |
| 208–215 | 0 | 0 | 0 | 30 each | enp2s0f1 TxRx |
| **장치 IRQ 합계** | **0** | **0** | **0** | 24,556 | |

참고 — IPI 행 (같은 60 초): CAL CPU12=41 CPU13=41 (격리 8코어 각 41, 균등), TLB 0, RES 0, LOC 1/1, MCP 1/1, NMI/MCE/TRM 0.

## 판정: **A — 소프트웨어(장치 IRQ) 유입 배제**
- GPU 93 % 부하에서 NVIDIA 인터럽트 8,114회/분이 발생했지만 **격리 코어에는 0건**. CPU 12·13 에 배정된 NVMe 큐도 발생 0.
- IPI 는 8코어에 균등(41회씩) → 코어 6 편중의 원인이 될 수 없음.
- 따라서 8시간 soak 의 CPU 12·13 편중은 인터럽트·IPI 유입으로 설명되지 않는다. **물리 코어 6 고유의 하드웨어/전원 도메인 사건** 가설이 비로소 근거를 얻는다.
- 지시에 따라 여기서 중단: affinity 이동·isolcpus 변경·코어 6 제외 **실행하지 않음**.

## 주의
- `smp_affinity_list` 는 재부팅하면 초기화된다(이번엔 변경한 것 없음). 영구화는 원인 확정 후 별도 설계.
- irqbalance 가 켜진 채였다. 배정을 바꿀 수는 있어도 "격리 코어에 0건" 결과는 배정과 무관한 실측이므로 판정에는 영향 없음. 다음 affinity 작업 전에는 `sudo systemctl stop irqbalance && sudo systemctl disable irqbalance` 필요.
