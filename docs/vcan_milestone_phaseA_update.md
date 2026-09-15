# vcan 마일스톤 액션 보드 — Phase A 갱신분 (2026-09-12)

> 보드의 해당 절을 아래 내용으로 **교체**한다. 측정치·근거의 원본은 `can_stack_development.md` §5.A (브랜치 `claude/ecstatic-keller-uhtbhe`, 커밋 `c200fdd`).

---

## 전제 (교체)

- `EAIT_CAN(AVANTE_CN7).dbc` 확보 완료(비공식 개발중 버전)
- **RT 커널 `6.8.1-1059-realtime` 설치·격리 튜닝·30분 실측 완료 (2026-09-12)** — 최악 0.47 ms/30분, 99.9995 % 50 µs 이내. 예산 재정의 결정·generic 비교·A-3/A-4 남음(Phase B와 병행)
- **OS = Ubuntu 22.04.5 LTS** (24.04 아님, ROS2 Humble 유지)
- **CAN 어댑터 PEAK PCAN-PCIe FD 2채널 장착됨** (`can0`/`can1`, 커널 내장 드라이버)
- mcap 원본 미보유

---

## 변경 이력 — RT 커널 선정 경위 (교체)

| 시점 | 후보 커널 | 결과 |
|---|---|---|
| 초기 | `5.15.0-1114-realtime` (Ubuntu Pro) | ❌ **폐기** — WiFi 동글(RTL8822BU) 드라이버 `rtw88_8822bu`가 Linux 6.2+ 에만 있어 미인식. NVIDIA 470 DKMS가 RT 빌드를 거부해 GPU도 사용 불가 |
| 비RT 기준 | `6.8.0-138-generic` (HWE) | 무선·GPU 정상. 기본 부팅·롤백용으로 유지 |
| **확정** | **`6.8.1-1059-realtime`** (`linux-realtime-hwe-22.04`) | ✅ **채택** — 6.8 generic과 같은 기반이라 무선 정상, 배포판이 빌드·보안패치 제공, 22.04에서 바로 설치 |

- 이전 판에 적힌 "5.11-rt"와 "6.11"은 인수인계 문서의 전제였고 **실제 장비와 달랐다** (실측: 5.15-rt, 6.8 generic).
- **"같은 OS면 드라이버 문제도 같이 해결된다"는 가설은 기각**: 드라이버 본체는 커널 버전별 별도 코드다.
- **"호환성 전수 검사 통과"의 단서**: NVIDIA는 PREEMPT_RT를 공식 지원하지 않아 `IGNORE_PREEMPT_RT_PRESENCE=1` 우회로 빌드했다. GPU 83% 부하에서 문제는 없었지만 **리스크 항목으로 계속 추적**한다.
- 6.12 LTS(메인라인 PREEMPT_RT) 이관은 대회 후 재검토 — A-8 측정치가 비교 기준.

**확인 필요 → 회신 완료**
- [x] X-1. OS 버전 — **Ubuntu 22.04.5 LTS (jammy)**. `6.8.1-*-realtime`은 24.04 전용이 아니라 Ubuntu Pro가 22.04용으로 백포트해 제공(`6.8.1-1059.60~22.04.1`, `esm.ubuntu.com/realtime jammy`). §0의 22.04 기재가 맞음, Jazzy 이전 불필요
- [x] X-2. 커널 문자열 — `uname -r` = **`6.8.1-1059-realtime`**, `uname -v` = `#60~22.04.1-Ubuntu SMP PREEMPT_RT`

---

## Phase A — RT 커널 마무리 (교체)

- [x] A-0a. 커널 라인 확정 — `6.8.1-1059-realtime`
- [x] A-0b. 이행 호환성 검사 — 무선 ✅ / CAN ✅ / GPU ⚠️(우회 빌드, 동작) / 유선 NIC ✅ / Secure Boot 비활성 / `hwlatdetect` 0건
- [x] A-0c. RT 실제 부팅 확인 — `uname -r` + `/sys/kernel/realtime` = `1`
- [x] A-0d. GRUB 롤백 경로 — 매 부팅 메뉴 10초, 기본값 `6.8.0-138-generic`, 이전 커널 전부 유지
- [x] A-0e. RT에서 무선 재확인 — WiFi 연결 ✅. **Bluetooth는 해당 없음(이 PC에 BT 하드웨어 없음)** — 필요 시 USB 동글 구매로 별도 항목화
- [x] A-1. `isolcpus`/`nohz_full`/`rcu_nocbs` — 부팅 항목 `A1-BSW: … + RT 튜닝`으로 부팅 확인(2026-09-12 16:36). `/proc/cmdline` 반영, `isolated` = `nohz_full` = `8-15`
- [x] A-2. IRQ affinity — `irqaffinity=0-7,16-19` 적용. 격리 코어에 남은 장치 인터럽트는 NVMe CPU별 큐 8개(IRQ 163~170)뿐이고 **발생 0회**(커널 관리 인터럽트라 이동 불가, 그 CPU에서 디스크 I/O를 할 때만 발생) → **RT 태스크는 디스크 I/O 금지** 규칙으로 관리
- [ ] A-3. 제어 노드를 격리 코어에 `taskset -c 8-15` + `chrt -f`로 띄우는 실행 스크립트/systemd 유닛 — **격리 코어는 명시 배치한 태스크만 돌기 때문에 이게 없으면 튜닝 효과도 없음**
- [ ] A-4. `mlockall` 적용 확인용 테스트 프로그램
- [x] A-5. 무부하 측정 (튜닝 전) — 1차 116 µs / 2차 65 µs. ⚠️ 보드 명령의 `-N`은 `-h 400`과 단위 충돌 → **`-N` 없이 측정**
- [x] A-6. 부하 측정 — 튜닝 전 60초: CPU 23 µs / CPU+GPU 20 µs. **튜닝 후 정식 30분(GPU 83 %+CPU·메모리·I/O)**: 평균 3.9 µs, 99.9995 % 50 µs 이내, 100–400 µs 71회, **400 µs 초과 2회(최악 467 µs, 단일 사건)**. 1차 30분(839 µs, 93회)은 GPU 도구 크래시·코어덤프가 겹친 오염 측정으로 참고용
- [~] A-7. 예산 대조 — **최악값 기준 §7(수십~100 µs) 미달(0.47 ms), 99.9995 % 기준 충족.** CAN 스택 데드라인(10 ms 주기)에는 5 %로 충분. → **팀 결정 필요**: 예산을 "최악 1 ms, 99.99 % 100 µs"로 재정의 vs 100 µs급 필수 루프 MCU 이관
- [x] A-8. §5.A 표 기입 (커널 칸 = `6.8.1-1059-realtime`) — 튜닝 후 30분 행 기입 완료. generic+preempt=full 비교 행만 _TBD_

**튜닝 후 첫 측정 실패 → 수정 완료 (2026-09-12)**: 격리 코어에서 cyclictest가 `FATAL: No allowable cpus to run on`으로 즉시 종료. `isolcpus`가 켜지면 새 프로세스의 허용 CPU가 격리 코어 밖으로 제한되기 때문 → 측정 명령을 `taskset -c 0-19 … -a8-15 --mainaffinity=0-7,16-19`로 수정. **같은 이유로 A-3의 ROS2 노드도 반드시 `taskset`으로 격리 코어에 배치해야 한다.**

**Phase B 착수 판정 (2026-09-12)**: **착수 가능.** Phase A의 남은 항목(예산 결정, generic 비교 측정, A-3/A-4)은 vcan 환경 구성과 독립적이며 병행 가능. 실측된 실시간성(최악 0.5 ms)은 CAN 스택 SIL 개발에 지장이 없다.

**다음 순서 (그룹1, Phase B와 병행)**: generic 비교 `soak 30`(항목 `a1-bsw-generic-tuned`) → RT에서 `trace 30 300` → 팀 예산 결정 → A-3(격리 코어 배치 스크립트) → A-4(mlockall).

**RT 런타임 권고**: 주행 구간에서 WiFi/BT 비활성(`rfkill block all`), 대회 기간 커널·NVIDIA 드라이버 동결(`apt-mark hold`).
