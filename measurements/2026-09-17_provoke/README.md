# 2026-09-17 양성 대조 — 네트워크 사건을 일부러 일으키며 30분 soak (운용 설정 그대로)

## 왜
운용 설정 8h(9/17)의 400 µs 초과 에피소드 3건이 WiFi 재연결·PackageKit 갱신과 겹쳤다. 인과인지 우연인지 30분으로 가르기 위해
같은 운용 설정(rt-poll + eco 800 + hk ondemand)에서 `provoke_net.sh 30 120` 을 병행: 2분마다 **WiFi off(8 s) → on(25 s 재연결) → `pkcon refresh force`(≤60 s) → 대기(~85 s)**, 9주기.

## 결과 — `soak_20260917_140837.*`, `provoke_20260917_140855.log` (14:08~14:38)

| | 어제 같은 설정 30분 (조용) | **오늘 30분 (네트워크 사건 유발)** |
|---|---|---|
| 최악 | 27 µs | **892 µs** |
| 평균 | 7.0 | 7.5 |
| 50 µs 초과 | 0 | **2,228** |
| 200 µs 초과 | 0 | **1,404** |
| 400 µs 초과 | 0 | **1,127 = 에피소드 130개** |

- 에피소드는 매번 **8코어 전부 동시**(어제 8h 의 3건과 같은 형태). 스레드별 200+ 130~220 회로 균등.
- SMI 0, 열 스로틀 0, 패키지 44~65 °C, GPU 83 %, 격리 클럭 793~1000 MHz(800 고정 유지). 커널 경고: `iwconfig` 경고 1건뿐.

### 구간별 발생률 (`episodes.py` + provoke 로그 대조)
| 구간 | 에피소드 | 총 시간 | **분당** |
|---|---|---|---|
| provoke 시작 전 (WiFi 연결, 조용) | 2 | 133 s | 0.9 |
| WiFi 꺼짐 (8 s ×9) | 1 | 66 s | 0.9 |
| 재연결 중 (25 s ×9) | 13 | 206 s | 3.8 |
| `pkcon refresh` 진행 중 | 26 | 480 s | 3.2 |
| **pkcon 완료 후 대기 (~85 s ×9)** | **88** | 915 s | **5.8** |

## 판정
- **인과 확정**: 같은 설정에서 어제 0 → 오늘 130 에피소드. 8h 의 3건은 우연이 아니라 네트워크·패키지 계열 활동이 만든 것.
- 세부 분해에서 **WiFi 어댑터가 꺼져 있던 구간이 가장 조용(0.9/분)** 하고, **pkcon 이 끝난 뒤 대기 구간이 가장 시끄럽다(5.8/분)**. 후자에는 PackageKit 백엔드의 후처리(apt 캐시·appstream 재생성 = 하우스키핑 CPU·디스크 집중 작업)와 WiFi 유휴(LPS 전이)가 겹쳐 있어, "USB WiFi 트래픽/전원 전이"와 "무거운 사용자 공간 작업"을 아직 못 갈랐다.
- 기전 후보(격리 코어에 IRQ 없이 8코어 동시 0.5~0.9 ms): 전 CPU 대상 **함수 호출 IPI**(CAL) — `wbinvd`/`flush_tlb_all`/`text_poke sync`/expedited RCU 등. 부팅 후 격리 코어 CAL 은 코어당 약 24,000회(≈20/분, 유휴에도 발생) → 대부분은 짧고 무해하며, 특정 종류만 길 가능성. `ipi_watch.sh`(csd_function_entry 추적)로 provoke 중 격리 코어에서 실행되는 IPI 함수 이름을 잡으면 갈린다.

## 도구
- `tools/rt/provoke_net.sh [분] [간격초]` — WiFi off/on + pkcon refresh 반복, 시각 로그
- `tools/rt/episodes.py <soak.log> [--provoke <log>] [--window 초]` — overflow 사이클→시각, 에피소드 묶음, provoke·저널 대조
- `tools/rt/ipi_watch.sh [초] [-- 명령]` — 격리 코어 CAL 증가량 + IPI 함수별 횟수 (+ 보낸 쪽)
- `tools/rt/soak_guard.sh on race | check | off` — 대회 조건(라디오 끔, PackageKit·snapd·타이머 정지)

## 다음
1. **기전 5분 테스트**: `sudo bash tools/rt/ipi_watch.sh 150 -- bash tools/rt/provoke_net.sh 3 60` → 격리 코어에서 실행된 IPI 함수 상위 목록. `wbinvd`·`flush_tlb`·`sync_core` 류가 수백 회면 그것이 원인.
2. **저녁 대회 조건 8h**: `soak_guard on race` → `soak 480`. 예상 = 에피소드 0~1.
3. 운용 규칙 확정: 주행 중 WiFi/WWAN 라디오 끔(또는 USB 어댑터 제거) + PackageKit/snapd/unattended-upgrades 정지 — A-3 유닛에 포함.

---

## 기전 테스트 1 — 격리 코어 IPI 추적 (`ipi_watch.sh 150 -- provoke_net.sh 3 60`, 15:42:51~15:45:21, cyclictest 없음)
- 150초 동안 격리 코어에 도착한 함수 호출 IPI(CAL): **코어당 6회 = 전 CPU 브로드캐스트 2건 × 3연속**(`do_sync_core`, 코드 패치 `text_poke_bp` 의 3단계 동기화). 보낸 쪽은 `kworker/4:0`, `kworker/19:1`(커널 워크큐), 시각 **+43.3 s / +43.6 s — provoke 시작(+60 s) 전**.
- **provoke 구간(+60~+100 s: WiFi off/on, pkcon refresh)과 그 뒤 50초 동안 격리 코어로 간 IPI = 0.** TLB flush IPI(`flush_tlb_func`)는 수천 건 있었지만 전부 하우스키핑 CPU 대상.
- **판정: "네트워크·패키지 활동 → 전 CPU IPI → 격리 코어 스톨" 가설은 기각.** 어제 9/14 ftrace(사건 창에 격리 코어 커널 이벤트 없음)와 합쳐 보면, 스톨은 **커널이 볼 수 없는 계층**(하드웨어·펌웨어·언코어)에서 난다. SMI 는 0.
- 남은 후보: 코어 주파수 제한 사유 레지스터 `MSR_CORE_PERF_LIMIT_REASONS`(0x64F: PL1/PL2/전류(ICCmax)/VR 열/패키지 레벨 등 사유 로그 비트), `IA32_PACKAGE_THERM_STATUS`(0x1B1) 로그 비트 — 사건 전후로 비트가 켜지는지. USB 호스트(xHCI)·PCIe 링크 전원관리(ASPM/LPM)와의 연관은 `rfkill` 대신 **어댑터 물리 제거** 조건과 비교하면 갈린다.
- 이 테스트는 부하(stress-ng·GPU) 없이 했다. 양성 대조의 130 에피소드는 부하 중이었으므로, 부하가 필요조건인지도 아직 모른다(9/12~15 의 A 계열 스파이크는 GPU 부하에서만 나타났음).

### 다음 (기전)
1. soak 열 로그에 MSR 0x64F/0x1B1 로그 비트 + 하우스키핑 실클럭 + RAPL W 를 10초마다 추가 → 저녁 8h 와 다음 provoke 30분에서 에피소드 시각과 대조.
2. `a1_rt.sh trace 10 400` + provoke 병행 → 스톨 순간 격리 코어의 마지막 커널 이벤트 재확인(9/14 와 같은 형태인지).
3. USB WiFi 어댑터 **물리 제거** 상태에서 pkcon 만 반복(유선 필요) vs 어댑터 연결·라디오 off 상태 비교.
