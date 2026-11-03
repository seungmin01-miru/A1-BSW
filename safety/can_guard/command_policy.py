"""상태별로 실제 무엇을 보낼지 정하는 순수 함수 — I/O 없이 테스트 가능. can_guard.py 의 메인 루프가 이
함수의 출력을 `plausibility.clamp_range` 에 마지막으로 한 번 더 통과시킨 뒤 인코딩한다(방어 이중화).

2026-10-06 실차 0x210(조향 deg, 브레이크 %, 가속 %, 축별 auto)으로 전환. EAIT 시절과 달라진 점:
  - "En" 대신 **축별 auto 비트**. auto=0 이면 그 축은 차량이 우리 명령을 무시한다(수동).
  - AEB 신호가 없다 → 정지 수단은 **브레이크 명령**뿐이다. 그래서 HOLDING/DEGRADED/STOPPED 에서 브레이크를
    `hold_brake_pct`(기본 30 %, 계획서 §6 D-a 제안값)까지 `brake_ramp`(기본 30 %/s) 속도로 올린다.
    제어 노드가 이미 그보다 세게 밟고 있었으면 **줄이지 않는다**(목표 = 둘 중 큰 값).
  - **auto 비트는 마지막으로 받은 명령 그대로**: can_guard 는 제어 노드가 넘겨주지 않은 축의 제어권을 스스로
    가져가지 않는다(예: 사람이 브레이크 페달을 쥐고 있던 축을 갑자기 auto 로 바꾸지 않음).
  - STOPPED: `stopped_mode='hold'`(기본) = auto 유지 + 브레이크 유지 + 가속 0 + 조향 고정.
    `'manual'` = auto 전부 0 + 명령 0(차량을 사람에게 넘김) — 무인 차량에선 아무도 제어하지 않게 되므로 비권장.
"""
from dataclasses import replace

from protocol import Command
from state_machine import State

STOPPED_MODES = ('hold', 'manual')
HOLD_STATES = (State.HOLDING, State.DEGRADED, State.STOPPED)


def held_steer_on_transition(prev_state, new_state, held_steer_deg, last_valid_cmd):
    """고정할 조향각 갱신 규칙 — ACTIVE/INIT 에서 HOLDING·DEGRADED·STOPPED 중 **어디로든** 처음 들어가는 순간의
    명령 조향각을 캡처하고, 그 세 상태끼리 옮겨 다닐 때는 처음 캡처한 값을 그대로 둔다.

    2026-10-06 리허설에서 잡은 버그의 수정: 차가 서 있을 때(차속 ≤ 정지 문턱) 명령이 끊기면 상태머신이
    HOLDING 을 거치지 않고 ACTIVE → STOPPED 로 바로 가는데, 예전 코드는 HOLDING/DEGRADED 진입 때만 캡처해서
    STOPPED 가 **오래된 값(초기 0°)**으로 조향을 고정했다 — 실차라면 핸들이 12° → 0° 로 튄다. EAIT 시절엔
    STOPPED 가 EPS_En=0(조향 놓음)이라 드러나지 않았다. 또 DEGRADED → HOLDING 에서 다시 캡처하면 DEGRADED 동안
    계속 들어온(인지 없이 나온, 신뢰 못 할) 명령으로 갱신되므로 그것도 막는다."""
    if new_state in HOLD_STATES and prev_state not in HOLD_STATES:
        return last_valid_cmd.steer_cmd_deg
    return held_steer_deg


def command_for_state(state, last_valid_cmd, held_steer_deg, steer_limiter, acc_limiter, brake_ramp, dt_s,
                      hold_brake_pct=30.0, stopped_mode='hold'):
    """state: 이번 주기에 확정된 상태(state_machine.next_state 의 결과).
    last_valid_cmd: 마지막으로 성공적으로 받은 제어 명령.
    held_steer_deg: HOLDING/DEGRADED 진입 시점에 얼린 조향각(호출자가 전이 시점에 캡처).
    steer_limiter/acc_limiter: `plausibility.RateLimiter` — 제어 노드 명령의 변화율 제한(기본 무제한).
    brake_ramp: `plausibility.RateLimiter` — can_guard 가 스스로 거는 브레이크의 램프(확정 숫자 필요).
    반환: 클램프 전 `Command`(호출자가 `plausibility.clamp_range` 를 마지막으로 한 번 더 돌린다)."""
    if state == State.INIT:
        steer_limiter.reset(0.0)
        acc_limiter.reset(0.0)
        brake_ramp.reset(0.0)
        return Command()   # auto 전부 0(수동), 명령 0 — 안전 상태

    if state == State.ACTIVE:
        steer = steer_limiter.step(last_valid_cmd.steer_cmd_deg, dt_s)
        acc = acc_limiter.step(last_valid_cmd.acc_cmd_pct, dt_s)
        brake_ramp.reset(last_valid_cmd.brake_cmd_pct)   # HOLDING 으로 가면 지금 브레이크에서부터 램프 시작
        return replace(last_valid_cmd, steer_cmd_deg=steer, acc_cmd_pct=acc)

    if state in (State.HOLDING, State.DEGRADED) or (state == State.STOPPED and stopped_mode == 'hold'):
        # 조향은 진입 순간 값에 고정, 가속은 0, 브레이크는 유지값까지 램프(이미 더 세면 그대로).
        # DEGRADED(인지 끊김, §7.2 "decel per team policy")도 HOLDING 과 같은 처리 — 검증된 경로 재사용.
        steer = steer_limiter.step(held_steer_deg, dt_s)
        acc = acc_limiter.step(0.0, dt_s)
        brake = brake_ramp.step(max(hold_brake_pct, last_valid_cmd.brake_cmd_pct), dt_s)
        return replace(last_valid_cmd, steer_cmd_deg=steer, acc_cmd_pct=acc, brake_cmd_pct=brake)

    if state == State.STOPPED:   # stopped_mode == 'manual'
        return Command()

    raise ValueError(f'알 수 없는 상태: {state}')
