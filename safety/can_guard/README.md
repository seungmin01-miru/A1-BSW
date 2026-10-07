# safety/can_guard — 안전 임계 CAN TX (MCU 대체)

`can_stack_development.md` §5.D / §7 의 구현. **`ros2_ws/` 밖**에 독립적으로 둔다 — ROS2/DDS 를 hot loop 에
넣지 않는다는 §7.2 규칙을 디렉터리 경계로도 강제한다. 런타임 의존은 표준 라이브러리 + `python-can` 뿐이다
(`sil_tests/`·`test/` 는 시험 도구라 `cantools` 를 써도 된다).

**기준 프로토콜: 실차 A1 DBC(`DBC/A1_dbc_fixed.dbc`)** — 2026-10-06 EAIT(0x156/0x157, 0x710~0x713)에서 전환,
2026-10-07 EAIT DBC 파일 삭제. 차량으로 나가는 **0x210 USER_control_command 의 유일한 송신자**다.

| 0x210 신호 | 비트 | 형식 |
|---|---|---|
| steer_command | 0–15 | int16, **1 deg/raw**(업체 DBC 의 ×0.1 은 오류 — 9/17 로그 1,354구간 위치/명령 비율 1.000) |
| break_command | 16–31 | uint16, 0~100 % |
| acc_command | 32–39 | uint8, 0~100 % |
| steer/break/acc_is_auto_command | 40/41/42 | 축별 auto(1) / manual(0) |
| (alive counter) | — | **없음**(DBC 확인) — EAIT 시절의 Aliv_Cnt 소유 로직은 제거 |

## 있는 것

| 파일 | 역할 |
|---|---|
| `protocol.py` | `CommandChannel`(조향 deg·브레이크 %·가속 %·축별 auto) + `HeartbeatChannel`(인지 생존) — 공유메모리 seqlock |
| `plausibility.py` | DBC 범위 + 운용 한계(`Limits`, CLI) 클램프, 가속·브레이크 동시 명령은 브레이크 우선, `RateLimiter`(숫자 미확정, 기본 무제한) |
| `state_machine.py` | `next_state()` — INIT/ACTIVE/HOLDING/DEGRADED/STOPPED 순수 전이 함수(프로토콜 무관, 전환 때 무수정) |
| `command_policy.py` | 상태별 명령 — HOLDING/DEGRADED/STOPPED(hold): 조향 고정, 가속 0, 브레이크 유지값(30 %)까지 램프, auto 비트 유지. `held_steer_on_transition()` |
| `tx_encode.py` | `Command` → 0x210 8바이트(조향 ×1) |
| `rx_decode.py` | 0x201 → 차속(좌우 평균, STOPPED 조기 판정), 0x200 → 조향·브레이크 위치·auto(로그용) |
| `rt_setup.py` | A-3 레시피(timer slack, cpu affinity, SCHED_FIFO, mlockall) |
| `sd_notify.py` | systemd `Type=notify`/`WatchdogSec` 하트비트 (외부 의존 없이 소켓 직접) |
| `kvaser_compat.py` | python-can 4.6.1 ↔ Kvaser CANlib 5.52 호환 패치(LOCAL_TXACK 4바이트) — `--interface kvaser` 일 때만 |
| `can_guard.py` | 메인 루프 — 20 ms 주기, 시작 전 점검(다른 0x210 송신자 → 거부, 종료 코드 3), `--interface socketcan|kvaser` |
| `lift_cmd.py` | 리프트 시험용 한 줄 명령 → `CommandChannel` + 하트비트(가짜 제어 노드·인지). 버스엔 송신 안 함 |
| `can_guard.service` | systemd 유닛(미설치) |
| `sil_tests/` | P-1/P-2/A-3/8h 시험(가짜 제어노드·인지 프로세스로 실제 kill 검증, 지연 실측, 장시간 운전) |

```bash
cd safety/can_guard
python3 -m pytest test/ -q                                  # 67개(수정본 DBC + 9/17 실차 프레임 대조), 하드웨어 불필요

python3 can_guard.py --channel vcan0                         # SIL
python3 sil_tests/p1_kill_control_node.py --channel vcan0    # §7.3 P-1
python3 sil_tests/p2_kill_perception.py --channel vcan0      # §7.3 P-2
bash ../../tools/race_day/rehearse_lift_guard.sh             # 리프트 시험 C0~C7 리허설(가짜 실차 상대)

# 실차 — PEAK 카드
python3 can_guard.py --channel can0 --steer-limit-deg 30 --brake-limit-pct 60 --acc-limit-pct 10 --status-interval-s 1
# 실차 — Kvaser Leaf v3 (tools/kvaser/README.md, CANlib 채널 번호)
python3 can_guard.py --interface kvaser --channel 0 --bitrate 500000 --steer-limit-deg 30 --brake-limit-pct 60 \
    --acc-limit-pct 10 --status-interval-s 1
```

주요 CLI(전부 코드 변경 없이 조정): `--period 0.020`(9/17 실측 0x210 주기), `--watchdog-t 0.05`,
`--hold-brake-pct 30`, `--hold-brake-ramp-pct-s 30`, `--stopped-mode hold|manual`, `--steer/brake/acc-limit-*`,
`--precheck-s 1.0`, `--status-interval-s`.

## 실측

### A1 전환 후 (2026-10-07, SIL·vcan0)

| 항목 | 결과 |
|---|---|
| 유닛테스트 | 67/67 |
| **P-1** (제어 노드 kill -9) | **PASS** — 69 ms 뒤 HOLDING(50 ms + 한 주기), 조향 고정, 가속 0, 브레이크 2→19 %(램프 중), 송신 끊김 0 |
| **P-2** (인지 kill -9) | **PASS** — 0x210 간격 kill 전/후 평균 20.00 ms(최대 20.45), DEGRADED 전이, dmesg BUG 0 |
| 리프트 리허설(`rehearse_lift_guard.sh`) | **31/31** — 시작 거부, INIT 안전 프레임, auto·조향(배율 1 추종)·이중 한계·브레이크·가속, 하트비트 끊김·제어 노드 죽음 → HOLDING/STOPPED(조향 마지막 값 유지), can_guard kill 시 송신 즉시 중단, 다른 송신자 경고 |
| A-3 지연(격리 + FIFO) | **미측정** — sudo 필요. 주기 20 ms 라 여유가 크지만 대회 전 재측정 |
| 장시간 soak | **미실시**(새 코드) |

### EAIT 시절 (2026-09-18~19) — 구조·타이밍 근거로는 지금도 유효

같은 메인 루프 구조(seqlock 읽기 2회 + 송신)로 잰 값이다. 송신이 2회(0x156/0x157)에서 1회(0x210)로 줄어 지금은
같거나 가볍다.

| 항목 | 결과 |
|---|---|
| A-3 적용, 자체측정(`late=now-next_t`) | 평균 17.0 µs, 최대 29.1 µs(D4 예산 1 ms 의 2.9 %) |
| P-9 SIL 근사 8시간(A-3 + 디스플레이 끔) | 2,880,000주기 완주, 스푸리어스 전이 0, 최대 44.6 µs(4.46 %), 100 µs 초과 0 |
| 외부 관찰 vs 자체측정 | 최대 178.8 vs 29.1 µs — 외부 관찰자 자신의 스케줄링 지연이었음. 송신 타이밍 판단은 자체측정 기준 |

## 보류 — 외부 입력 필요

- [ ] **진짜 ROS2 제어 노드**를 `CommandChannel.open()` 으로 결선(지금은 `lift_cmd.py`·`fake_control_node.py`)
- [ ] **진짜 인지 프로세스**를 `HeartbeatChannel.open()` 으로 결선
- [ ] **변화율 상한** — 조향 deg/s, 가속 %/s. 실차 액추에이터 사양 필요. 그 전까지 `RateLimiter` 비활성
- [ ] **업체 확인**: 0x210 조향 배율(×1 맞나), 명령 타임아웃·fail-safe 동작, auto 비트 수락 조건 — 실차 인수일 C단계로도 확인
- [ ] 잠정값 확정: watchdog 50 ms, STOPPED 2.0 s, 인지 0.5 s, 정지 3 km/h, 유지 브레이크 30 %

## 다음 단계

- [ ] 실차 리프트 송신 시험(C단계) — `tools/race_day/VEHICLE_MANUAL.md`
- [ ] A-3 지연 재측정(`sudo chrt -f 90 sudo -u ailab python3 can_guard.py --cpu 8 --rt-priority 90 --max-cycles N`)
- [ ] 새 코드 장시간 soak(`sil_tests/soak_8h.py`)
- [ ] `can_guard.service` 설치(진짜 제어 노드가 생긴 뒤), 벤치 시험 P-3/4/6/9/10/11(실 버스)

## 설계 결정 기록

- "공유메모리 ring" → **seqlock 단일 슬롯**. 제어 명령은 최신값만 의미가 있어 큐가 불필요.
- 변화율 상한은 **의도적으로 비워 둔다** — 모르는 안전 숫자는 추측으로 채우지 않는다.
- HOLDING/DEGRADED/STOPPED(hold): 조향은 ACTIVE 에서 처음 벗어나는 순간 값에 고정, 가속 0, 브레이크는
  `max(유지값 30 %, 마지막 명령)` 까지 30 %/s 램프. **auto 비트는 마지막 명령 그대로** — can_guard 는 넘겨받지 않은
  축의 제어권을 스스로 가져가지 않는다. AEB 신호가 없어 정지 수단은 브레이크 명령뿐.
- STOPPED 기본은 `hold`(auto 유지 + 브레이크 유지) — `manual`(auto 0 으로 사람에게 넘김)은 무인 차량에선 아무도
  제어하지 않게 되므로 비권장. STOPPED 는 재시작으로만 풀린다.
- DEGRADED(인지 끊김)도 HOLDING 과 같은 처리 + 자기 지속 상한(2.0 s) 뒤 STOPPED. 차속(0x201 평균)이 3 km/h 이하면
  상한을 기다리지 않고 STOPPED — 차가 서 있으면 HOLDING 을 거치지 않고 ACTIVE → STOPPED 로 직행한다(설계대로).
  ACTIVE 에서는 차속을 참조하지 않는다.
- 시작 전 점검: 다른 장치(원격조종 등)가 0x210 을 이미 보내고 있으면 시작 거부. 실행 중에 나타나면 경고만(스스로
  멈추면 안전 명령도 끊기므로).
- 송신은 `timeout=반 주기` — 실버스에서 bus-off·ACK 없음으로 송신 큐가 차도 루프가 멈추지 않는다.

## 개발 중 잡은 버그들 (기록)

1. `ctypes.Structure.from_buffer()` 가 mmap 에 "내보낸 포인터"를 쥐고 있어 `close()` 전에 `del` 필요.
2. `read()`/`age()` 기본 재시도(8회)가 무휴지 쓰기 스트레스 테스트(실제 별도 프로세스, 2만회)에서
   스푸리어스 실패 → 1000회로, 소진 시 `TornReadError`.
3. P-1/P-2 스크립트 초기 버전이 리소스 생성(가짜 프로세스들)을 `try` 밖에 뒀다가, 그 사이 예외(DBC 경로
   오타)가 나면 `finally` 를 못 타 자식 프로세스가 유출된 것을 실제로 겪음 — 생성부터 `try/finally` 로
   감싸도록 수정.
4. `SharedMemory(create=False)` 핸들이 `multiprocessing.resource_tracker` 에 등록돼, 그 프로세스가
   SIGKILL 로 죽으면(P-1/P-2 가 실제로 이렇게 함) "leaked shared_memory" 경고가 뜸(실제 누수 아님,
   소유자는 따로 있음) → `open()` 시점에 tracker 등록 해제로 제거.
5. **SIGTERM 이 `finally` 를 안 태움 → 진짜 리소스 누수** (2026-09-18, 실제 재현 확인). Python 은 SIGTERM 에
   기본 핸들러가 없어(SIGINT 와 달리 `KeyboardInterrupt` 로 안 바뀜) `Popen.terminate()` 가 프로세스를 즉시
   죽이고 `try/finally` 의 `finally` 가 아예 안 돈다 — `cmd_ch.close()`/`hb_ch.close()` 의 `unlink()` 가
   실행되지 않아 `/dev/shm` 세그먼트가 프로세스마다 2개씩 영구히 남는다. P-1/P-2/`measure_a3_latency.py`
   모두 `guard.terminate()` 로 종료시키므로 지금까지의 모든 SIL 실행이 이렇게 새고 있었다(각 실행이
   `uuid` 로 이름을 새로 만들어 서로 충돌은 안 해서 안 보였을 뿐). → `run()` 앞에서 SIGTERM/SIGINT 를
   `_on_shutdown_signal` 로 받아 `_stop_requested` 플래그만 세우고 메인 루프 조건에서 확인하도록 고침 —
   정상 종료 경로(= `finally` 실행)로 흡수됨. 고친 뒤 실제로 `kill -TERM` 후 `/dev/shm` 이 비는 것,
   exit code 0 인 것 확인.
6. **`wait_for_shm(cmd_shm)` 만으로는 부족 — `hb_shm` 경합** (2026-09-18, 실제 8h 첫 시도에서 재현).
   can_guard 는 `cmd_shm` 을 먼저 만들고 `hb_shm` 을 그 다음에 만드는데, 오케스트레이션 스크립트들
   (p1/p2/measure_a3_latency/soak_8h)이 `cmd_shm` 만 확인하고 바로 `fake_perception.py` 를 띄웠다.
   그 틈(마이크로초 단위지만 실제로 걸림)에 걸리면 `HeartbeatChannel.open()` 이 `FileNotFoundError` 로
   즉사 — perception_age 가 영원히 `None` 이 돼 `INIT → DEGRADED` 에서 안 벗어남(8h 목표가 20초 만에
   허위 경보로 끝남). `hb_shm` 도 같이 기다리도록 네 스크립트 전부 수정, 10회 반복 스모크 테스트로 재현
   안 되는 것 확인(0/10 재시작). 덤으로 `soak_8h.py` 는 가짜 노드가 8h 도중 죽어도 자동 재시작하도록
   보강(재시작 횟수는 최종 요약에 포함) — 초반 한 번의 우연한 실패로 나머지 몇 시간을 다 날리지 않도록.
7. **정지 상태에서 명령이 끊기면 조향이 오래된 값(0°)으로 튐** (2026-10-06 리프트 리허설에서 발견). 차속 ≤ 3 km/h
   이면 상태머신이 ACTIVE → STOPPED 로 직행하는데, 조향 고정값을 HOLDING/DEGRADED 진입 때만 캡처해서 STOPPED 가
   초기값 0° 를 보냈다(EAIT 시절엔 STOPPED 가 조향을 놓아 안 드러남). `command_policy.held_steer_on_transition()` 으로
   "ACTIVE/INIT 에서 세 정지 계열 상태로 처음 들어가는 순간 캡처, 그 사이 이동 땐 유지"로 수정 + 유닛테스트 2개.
