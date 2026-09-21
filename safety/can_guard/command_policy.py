"""상태별로 실제 무엇을 보낼지 정하는 순수 함수 — I/O 없이 테스트 가능. can_guard.py 의 메인 루프가 이
함수의 출력을 `plausibility.clamp_range` 에 마지막으로 한 번 더 통과시킨 뒤 인코딩한다(방어 이중화).
"""
from dataclasses import replace

from protocol import Command
from state_machine import State


def command_for_state(state, last_valid_cmd, held_eps_cmd, eps_limiter, acc_limiter, dt_s):
    """state: 이번 주기에 확정된 상태(state_machine.next_state 의 결과).
    last_valid_cmd: 마지막으로 성공적으로 받은 제어 명령(ACTIVE 용, HOLDING/DEGRADED 는 En/AEB 필드만 참고).
    held_eps_cmd: HOLDING/DEGRADED/STOPPED 진입 시점에 얼린 조향각(호출자가 상태 전이 시점에 캡처해서 넘긴다
    — 어느 상태로 얼렸든 같은 변수 하나를 재사용해도 된다, 세 상태가 동시에 성립할 수 없어서 서로 안 섞인다).
    eps_limiter/acc_limiter: `plausibility.RateLimiter` 인스턴스 — 주기마다 같은 객체를 재사용(내부 상태 보유).
    반환: 클램프 전 `Command`(호출자가 `plausibility.clamp_range` 를 마지막으로 한 번 더 돌린다)."""
    if state == State.INIT:
        eps_limiter.reset(0.0)
        acc_limiter.reset(0.0)
        return Command()   # 전부 기본값 — En=0, 안전 상태

    if state == State.ACTIVE:
        eps = eps_limiter.step(last_valid_cmd.eps_cmd, dt_s)
        acc = acc_limiter.step(last_valid_cmd.acc_cmd, dt_s)
        return replace(last_valid_cmd, eps_cmd=eps, acc_cmd=acc)

    if state in (State.HOLDING, State.DEGRADED):
        # 조향은 진입 순간 값에 얼리고, 가감속은 0(더 이상 가·감속하지 않음)으로 램프.
        # DEGRADED(인지 끊김, §7.2 "GPU-crash rule": "decel per team policy") 초안(2026-09-21): 신뢰 못 할
        # (인지 없이 나온) 명령을 그대로 따르지 않는다는 점에서 HOLDING 과 같은 처리를 기본값으로 삼았다 —
        # 이미 검증된 코드 경로를 재사용해 새 안전 로직을 늘리지 않는 선택. 팀이 실제 제동(목표를 0 이
        # 아닌 음수로)을 원하면 아래 acc_limiter.step 의 목표값 하나만 바꾸면 된다.
        eps = eps_limiter.step(held_eps_cmd, dt_s)   # 목표=얼린 값 → 변화율 제한 안에서 사실상 유지
        acc = acc_limiter.step(0.0, dt_s)             # 가감속 0 으로 램프(§5.D)
        return replace(last_valid_cmd, eps_cmd=eps, acc_cmd=acc)

    if state == State.STOPPED:
        return Command(eps_en=False, acc_en=False, aeb_en=last_valid_cmd.aeb_en,
                       eps_speed=last_valid_cmd.eps_speed)

    raise ValueError(f'알 수 없는 상태: {state}')
