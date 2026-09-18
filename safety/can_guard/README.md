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
| `rt_setup.py` | A-3 레시피(timer slack, cpu affinity, SCHED_FIFO, mlockall) — `eait_tx.py` 와 같은 방식, 독립 구현 |
| `sd_notify.py` | systemd `Type=notify`/`WatchdogSec` 하트비트 (외부 의존 없이 소켓 직접) |
| `can_guard.py` | 메인 루프 — 위 전부를 결선 |
| `can_guard.service` | systemd 유닛(미설치) |
| `sil_tests/` | P-1/P-2 실행 시험(가짜 제어노드·인지 프로세스로 실제 kill 검증) |

```bash
cd safety/can_guard
python3 -m pytest test/ -v                              # 51개, 하드웨어 없이 전부 통과

python3 can_guard.py --channel vcan0                     # SIL 로 직접 실행
python3 sil_tests/p1_kill_control_node.py --channel vcan0   # §7.3 P-1 (실제 kill -9 로 검증)
python3 sil_tests/p2_kill_perception.py --channel vcan0     # §7.3 P-2
```

## 실측 (2026-09-19, SIL·vcan0)

| 항목 | 결과 |
|---|---|
| 유닛테스트 | 51/51 통과 (protocol·plausibility·state_machine·command_policy·tx_encode·sd_notify) |
| INIT 상태 실제 프레임 | vcan0 에 0x156/0x157 실전송 확인, `cantools` 디코드로 안전 기본값(En=0 등) 대조 일치 |
| **P-1** (제어 노드 kill -9) | **PASS** — 전이 55ms(예산 50ms+한 틱 안), Aliv_Cnt 101프레임 연속, ACC_Cmd 0.150→0.000 램프 확인 |
| **P-2** (인지 kill -9) | **PASS** — 0x156 간격 kill 전/후 평균 10.00ms(최대 10.2~10.4ms), Aliv_Cnt 연속, DEGRADED 전이 확인, 새 dmesg BUG 없음 |

## 아직 없는 것 (다음 단계)

- [ ] 진짜 ROS2 제어 노드가 `CommandChannel.open()` 으로 결선 (지금은 `fake_control_node.py` 로 대신 시험)
- [ ] 진짜 인지 프로세스가 `HeartbeatChannel.open()` 으로 결선
- [ ] 변화율(rate) 상한 값 — 팀/EAIT 사양 확인 전까지 `RateLimiter` 는 비활성 상태로 둔다
- [ ] `0x157` 에 `Alive_Cnt` 가 없는 이유 확인 (§5.D 미확인 사실)
- [ ] STOPPED 상태의 "속도 0 수렴" 판정 — 지금은 시간(HOLDING 지속시간)만으로 근사(`command_policy.py` TODO
      참고). 실제로는 RX(0x711 VS)를 같이 구독해야 정확해진다 — 그러려면 can_guard 가 RX 도 보게 할지,
      아니면 별도 감시 프로세스를 둘지 설계 결정 필요.
- [ ] `can_guard.service` 설치·A-3 레시피 실측(격리 코어+`SCHED_FIFO 90`, `sudo chrt` 로 검증)
- [ ] 벤치 시험(P-3/4/6/9/10/11) — 실 `can0`/`can1` 필요, 5-1 이후

## 설계 결정 기록 (§7.2 원문과 다른/구체화된 부분)

- "공유메모리 ring" → **seqlock 단일 슬롯**으로 구현. 제어 명령은 항상 최신값만 의미가 있어 큐가 불필요.
- `plausibility.py` 의 변화율 상한은 **의도적으로 비워 뒀다** — 안전 파라미터를 추측으로 채우지 않는다.
- HOLDING: 조향은 진입 순간 값에 얼리고(`held_eps_cmd`), 가감속은 0 으로 램프. STOPPED: EPS/ACC En 을 끄고
  AEB_En 은 유지(안전장치라 끄지 않음).
- DEGRADED(인지 하트비트 끊김)의 실제 감속 정책은 **팀 결정 전까지 ACTIVE 와 동일** — `command_policy.py`
  안에 정책을 넣을 자리를 명시적으로 남겨 뒀다.

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
