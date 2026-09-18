# safety/can_guard — 안전 임계 CAN TX (MCU 대체)

`can_stack_development.md` §5.D / §7 의 구현. **`ros2_ws/` 밖**에 독립적으로 둔다 — ROS2/DDS 를 hot loop 에
넣지 않는다는 §7.2 규칙을 디렉터리 경계로도 강제한다. 이 디렉터리는 `rclpy`·`cantools` 등 무거운 런타임
의존이 없다(표준 라이브러리 + `python-can` 만).

## 지금 있는 것 (설계 + 순수 로직, 전부 유닛테스트됨 — 하드웨어·CAN·root 불필요)

| 파일 | 역할 |
|---|---|
| `protocol.py` | `CommandChannel` — 제어 노드 ↔ can_guard 공유메모리 인터페이스(seqlock 단일 슬롯) |
| `plausibility.py` | 범위 클램프(DBC 그대로, 지금 확정) + 변화율 제한(`RateLimiter`, 숫자는 미확정) |
| `state_machine.py` | `next_state()` — INIT/ACTIVE/HOLDING/DEGRADED/STOPPED 순수 전이 함수 |
| `tx_encode.py` | `Command` → 0x156/0x157 8바이트 인코더, `cantools` 대조로 검증 |
| `can_guard.service` | systemd 유닛 초안(미설치) |

```bash
cd safety/can_guard
python3 -m pytest test/ -v      # 38개, 하드웨어 없이 전부 통과
```

## 아직 없는 것 (다음 단계)

- [ ] `can_guard.py` 메인 루프 — 위 네 모듈을 결선: `CommandChannel.read()` → `plausibility.clamp_range`
      (+`RateLimiter`) → `state_machine.next_state()` → `tx_encode` → `can.Bus.send()`. RT 셋업(A-3 레시피:
      isolated core, `SCHED_FIFO 90`, `mlockall`, timer slack)은 `eait_tx.py` 의 `apply_rt_setup` 패턴 그대로.
- [ ] `sd_notify` 하트비트(systemd `WatchdogSec` 와 맞물림) — `can_guard.service` 의 TODO 참고
- [ ] 인지 하트비트 채널 (`CommandChannel` 과 같은 seqlock 패턴, 별도 세그먼트)
- [ ] SIL 시험 스크립트: P-1(제어 노드 kill), P-2(인지 kill) — §7.3, 지금 구조로 바로 가능
- [ ] 변화율(rate) 상한 값 — 팀/EAIT 사양 확인 전까지 `RateLimiter` 는 비활성 상태로 둔다
- [ ] `0x157` 에 `Alive_Cnt` 가 없는 이유 확인 (§5.D 미확인 사실)
- [ ] 벤치 시험(P-3/4/6/9/10/11) — 실 `can0`/`can1` 필요, 5-1 이후

## 설계 결정 기록 (§7.2 원문과 다른 부분)

- "공유메모리 ring" → **seqlock 단일 슬롯**으로 구현. 제어 명령은 항상 최신값만 의미가 있어 큐가 불필요
  (`protocol.py` 상단 docstring에 근거).
- `plausibility.py` 의 변화율 상한은 **의도적으로 비워 뒀다** — 안전 파라미터를 추측으로 채우지 않는다.
