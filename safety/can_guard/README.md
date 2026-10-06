# safety/can_guard — 안전 임계 CAN TX (MCU 대체)

`can_stack_development.md` §5.D / §7 의 구현. **`ros2_ws/` 밖**에 독립적으로 둔다 — ROS2/DDS 를 hot loop 에
넣지 않는다는 §7.2 규칙을 디렉터리 경계로도 강제한다. 이 디렉터리는 `rclpy`·`cantools` 등 무거운 런타임
의존이 없다(표준 라이브러리 + `python-can` 만. `sil_tests/` 의 검증 스크립트는 예외 — 테스트 도구라 `cantools`
를 써도 된다, can_guard 본체가 아니다).

## 있는 것

| 파일 | 역할 |
|---|---|
| `protocol.py` | `CommandChannel`(제어 명령) + `HeartbeatChannel`(인지 생존) — 공유메모리 seqlock |
| `plausibility.py` | 범위 클램프(DBC 그대로, 확정) + 변화율 제한(`RateLimiter`, 숫자는 미확정) |
| `state_machine.py` | `next_state()` — INIT/ACTIVE/HOLDING/DEGRADED/STOPPED 순수 전이 함수 |
| `command_policy.py` | 상태별로 실제 어떤 `Command` 를 보낼지(조향 유지, 가감속 램프 등) — 순수 함수 |
| `tx_encode.py` | `Command` → 0x156/0x157 8바이트 인코더 |
| `rx_decode.py` | 0x711(EAIT_INFO_ACC) 에서 VS(차속) 만 읽는 RX 디코더 — STOPPED 실측 차속 판정용(2026-09-21 초안) |
| `rt_setup.py` | A-3 레시피(timer slack, cpu affinity, SCHED_FIFO, mlockall) — `eait_tx.py` 와 같은 방식, 독립 구현 |
| `sd_notify.py` | systemd `Type=notify`/`WatchdogSec` 하트비트 (외부 의존 없이 소켓 직접) |
| `can_guard.py` | 메인 루프 — 위 전부를 결선 |
| `can_guard.service` | systemd 유닛(미설치) |
| `sil_tests/` | P-1/P-2/A-3/8h 실행 시험(가짜 제어노드·인지 프로세스로 실제 kill 검증, 지연 실측, 장시간 운전) |

```bash
cd safety/can_guard
python3 -m pytest test/ -v                              # 64개, 하드웨어 없이 전부 통과

python3 can_guard.py --channel vcan0                     # SIL 로 직접 실행
python3 sil_tests/p1_kill_control_node.py --channel vcan0   # §7.3 P-1 (실제 kill -9 로 검증)
python3 sil_tests/p2_kill_perception.py --channel vcan0     # §7.3 P-2
python3 sil_tests/measure_a3_latency.py --channel vcan0 --cpu 8 --rt-priority 90 --duration 5  # A-3 지연
python3 sil_tests/soak_8h.py --channel vcan0 --hours 8 --cpu 8 --rt-priority 90                # §7.3 P-9 SIL 근사
```

## 실측 (2026-09-19, SIL·vcan0)

| 항목 | 결과 |
|---|---|
| 유닛테스트 | 51/51 통과 (protocol·plausibility·state_machine·command_policy·tx_encode·sd_notify) |
| INIT 상태 실제 프레임 | vcan0 에 0x156/0x157 실전송 확인, `cantools` 디코드로 안전 기본값(En=0 등) 대조 일치 |
| **P-1** (제어 노드 kill -9) | **PASS** — 전이 55ms(예산 50ms+한 틱 안), Aliv_Cnt 101프레임 연속, ACC_Cmd 0.150→0.000 램프 확인 |
| **P-2** (인지 kill -9) | **PASS** — 0x156 간격 kill 전/후 평균 10.00ms(최대 10.2~10.4ms), Aliv_Cnt 연속, DEGRADED 전이 확인, 새 dmesg BUG 없음 |
| **A-3 지연 기준선** (RT 없음, `measure_a3_latency.py`) | 주기 편차(|실제−10ms 목표|) 평균 **70µs**, 최대 **~415µs** (5초 × 2회 재현) |
| **A-3 적용, 외부 관찰**(격리 코어+`sudo chrt` 로 진짜 SCHED_FIFO 90, vcan0 에서 프레임 간격으로 측정) | 평균 **16.8µs**, 최대 **178.8µs**(5초) — 기준선 대비 개선되나, 아래 자체측정과 비교하면 **관찰자 자신의 오차가 섞인 과대평가**임이 드러남 |
| **A-3 적용, 자체측정**(`can_guard.py --max-cycles 500`, `late=now-next_t`, `eait_tx.py` 와 동일 정의) | 평균 **17.0µs**, 최대 **29.1µs**(500주기≈5초) — **이게 진짜 비교 대상.** D4 예산(최대 ≤1ms) 대비 **2.9%** 사용 |
| **P-9 SIL 근사, 8시간** (`sil_tests/soak_8h.py`, A-3+디스플레이 끔 조건, 2026-09-18 21:14–09-19 05:14) | **PASS** — 목표 2,880,000주기 전부 완주(중단 없음). 상태 전이 **1건**(시작 시 INIT→ACTIVE 뿐, 8h 동안 스푸리어스 전이 0). 자체측정 누적 평균 **17.1µs** 최대 **44.6µs**(D4 예산 대비 4.46%), 스파이크(>100µs) **0건**. 1분 단위 479개 창(윈도) 최대값도 20.0~44.6µs 범위로 드리프트·이상치 없이 안정. dmesg BUG류 없음, `/dev/shm`·프로세스 전부 정리 확인 |

**세 수치 해석 (2026-09-18 결론)**:
1. **최대값 29.1µs vs 178.8µs — 6배 차이가 핵심 증거**: can_guard 자신이 보낸 시각은 최악 29.1µs 밖에 안
   늦었는데, vcan0 에서 받아 적는 외부 관찰자(`FrameRecorder`, 일반 우선순위·비격리 코어의 파이썬 루프)는
   자기도 가끔 스케줄링 지연을 먹어 178.8µs 까지 늦게 기록했다. **외부 관찰 수치는 can_guard 의 결함이
   아니라 관찰자의 측정 오차를 반영한다** — 이제부터 can_guard 자신의 송신 타이밍 판단은 자체측정
   (stderr 의 "자체측정(late=now-next_t)" 줄, `eait_tx.py` 와 동일 정의)을 기준으로 삼는다.
   (처음엔 "`sudo chrt` 를 오케스트레이션 스크립트 전체에 씌우면 관찰자까지 FIFO 90 을 상속해 경합한다"는
   가설을 세워 `_common.start_can_guard()` 가 can_guard.py 프로세스 하나만 감싸도록 고쳤다 — 평균은
   20.6→16.8µs 로 예측대로 개선됐지만 최대는 174.7→178.8µs 로 거의 안 바뀌어, **관찰자의 FIFO 상속 경합이
   아니라 관찰자라는 측정 방식 자체의 한계**였음이 이번 자체측정으로 최종 확인됐다.)
2. **자체측정(17.0/29.1µs)이 `eait_tx.py`(5~9µs)보다 2-3배 큰 건 구조적 차이로 설명된다**: can_guard 루프는
   매 주기 seqlock 읽기 2회(`CommandChannel.read()`+`HeartbeatChannel.age()`, ctypes 필드 접근),
   `dataclasses.replace()` 2회, `bus.send()` 2회(0x156/0x157) — `eait_tx.py`(메시지 1개 인코딩+송신 1회)
   보다 할 일이 많다. 이 정도 배율 차이는 정상 범위로 판단, D4 예산의 2.9%면 여유 충분.
3. 실행법: `sudo chrt -f 90 sudo -u ailab python3 can_guard.py --channel vcan0 --cpu 8 --rt-priority 90
   --max-cycles N` — 오케스트레이션 스크립트(`measure_a3_latency.py`)를 거치지 않고 can_guard 를 직접
   돌리면 stderr 가 파이프 캡처 없이 터미널에 바로 찍혀 가장 확실하다(오케스트레이션 스크립트에서는
   `sudo` 이중 래핑 때문에 `guard.communicate()` 가 가끔 stderr 캡처에 실패하는 현상 관찰됨 — 알려진 제약,
   근본 수정은 안 함, 직접 실행으로 우회).

## 보류 — 지금 여기서 못 하는 것 (외부 입력 필요, 나중에)

이 셋은 코드 문제가 아니라 **이 세션 밖의 무언가**(팀 확인, 아직 없는 실제 프로세스)가 있어야 진행된다 —
그래서 뒤로 미룬다. 다른 항목과 섞이지 않도록 따로 둔다.

- [ ] **진짜 ROS2 제어 노드**를 `CommandChannel.open()` 으로 결선 — ros2_ws 쪽에 아직 MPC/판단 노드가 없다
      (지금은 `fake_control_node.py` 로 대신 시험, P-1/P-2 는 이걸로 이미 검증됨)
- [ ] **진짜 인지 프로세스**를 `HeartbeatChannel.open()` 으로 결선 — 마찬가지로 아직 없다
- [ ] **변화율(rate) 상한 값** — `eps_cmd`(deg/s)·`acc_cmd`(jerk) 실차/EAIT 보드 사양. 이 저장소에
      `can_status_parameters_full.md` 가 없어 팀이 줘야 한다. 그 전까지 `RateLimiter` 는 비활성.
- [ ] `0x157`(EAIT_Control_02)에 `Alive_Cnt` 가 없는 이유 — EAIT 쪽에 물어봐야 아는 사실(§5.D 참고)

## 다음 단계 (지금 할 수 있는 것)

- [x] STOPPED 상태의 "속도 0 수렴" 판정 — **초안 완료(2026-09-21), 팀 확인 대기**. `rx_decode.py` 추가,
      can_guard 가 이미 열려 있는 TX 용 bus 를 그대로 RX 에도 써서(새 프로세스/IPC 없음, 설계 결정 완료)
      0x711(VS) 를 non-blocking 으로 읽는다. HOLDING/DEGRADED → STOPPED 는 "지속시간 상한" 또는 "실측
      차속이 정지 문턱(기본 3km/h) 이하" 중 먼저 오는 쪽 — 차속 모르면(RX 없음/오래됨) 기존 시간 기반
      안전망 그대로. 라이브 vcan0 로 조기 정지 경로까지 확인(VS=1km/h 주입 → 즉시 STOPPED). 아래 참고.
- [x] A-3 레시피 지연 실측(격리 코어+`SCHED_FIFO 90`, `sudo chrt` 로 root 확인) — **완료(2026-09-18)**.
      자체측정 평균 17.0µs·최대 29.1µs, D4 예산 2.9% 사용. 위 표 참고.
- [ ] `can_guard.service` **설치는 보류** — 진짜 제어 노드 없이 상시 기동하는 건 이르다고 판단, A-3 측정은
      `can_guard.py` 를 직접 `sudo chrt` 로 실행해서 한다(§5.C 때와 같은 방법, 설치 없이)
- [x] SIL 8시간 연속 운전(P-9 의 SIL 근사판 — 진짜 벤치는 실 `can0`/`can1` 필요, 5-1 이후) — **완료
      (2026-09-18 21:14–09-19 05:14)**. `sil_tests/soak_8h.py`, A-3+디스플레이 끔(§5.A 검증된 S2 조건)으로
      2,880,000주기 전부 완주, 스푸리어스 전이 0, 자체측정 누적 최대 44.6µs, dmesg BUG 없음. 위 표 참고.
- [ ] 벤치 시험(P-3/4/6/9/10/11) — 실 `can0`/`can1` 필요, 5-1 이후

## 설계 결정 기록 (§7.2 원문과 다른/구체화된 부분)

- "공유메모리 ring" → **seqlock 단일 슬롯**으로 구현. 제어 명령은 항상 최신값만 의미가 있어 큐가 불필요.
- `plausibility.py` 의 변화율 상한은 **의도적으로 비워 뒀다** — 안전 파라미터를 추측으로 채우지 않는다.
- HOLDING: 조향은 진입 순간 값에 얼리고(`held_eps_cmd`), 가감속은 0 으로 램프. STOPPED: EPS/ACC En 을 끄고
  AEB_En 은 유지(안전장치라 끄지 않음).
- **DEGRADED/STOPPED 초안 (2026-09-21, 팀 확인 대기)**:
  - DEGRADED(인지 하트비트 끊김, §7.2 "GPU-crash rule": "decel per team policy")는 이제 ACTIVE 가 아니라
    **HOLDING 과 같은 메커니즘**(조향 얼림 + 가감속 0 램프) — 이미 검증된 코드 경로 재사용, 새 안전 로직을
    늘리지 않는 선택. 팀이 실제 제동(목표를 0 이 아닌 음수로)을 원하면 `command_policy.py` 의 목표값
    하나만 바꾸면 된다.
  - DEGRADED 도 HOLDING 처럼 **지속시간 상한**(기본 2.0s, `--perception-lost-stopped-after`)이 지나면
    STOPPED 로 넘어간다 — 명령이 계속 fresh 해도(HOLDING 경로를 안 타므로) 인지 없이 무한정 명령을
    따르지 않도록. "perception-process death is a safety event"(§7.2) 를 직접 구현한 것.
  - **차속 조기 정지**: HOLDING/DEGRADED 어느 쪽이든 실측 차속(`rx_decode.py`, 0x711 VS)이 정지 문턱(기본
    3km/h, `--stop-speed-kph`) 이하면 지속시간 상한을 안 기다리고 바로 STOPPED. ACTIVE 상태에서는 차속을
    절대 참조하지 않는다(정상 주행 중 서행·정차를 정지로 오판하면 안 되므로) — `state_machine.py`
    docstring 에 이 이유를 명시.
  - 숫자들(2.0s, 3km/h)은 전부 **잠정값** — `plausibility.py` 의 변화율처럼 "몰라서 비워 둔" 게 아니라
    "합리적 기본값으로 채워 넣었으니 팀이 검토·조정"하라는 쪽. `--eps-rate-limit` 등과 같은 패턴으로
    CLI 인자만 바꾸면 코드 변경 없이 조정 가능.
  - 유닛테스트 13개 추가(state_machine 7·command_policy 2·rx_decode 5, cantools 대조 포함), 라이브
    vcan0 로 DEGRADED→STOPPED(지속시간 경로)·차속 조기 정지 경로 둘 다 확인, P-1/P-2 재실행 회귀 없음.

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
