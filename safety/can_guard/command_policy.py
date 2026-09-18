"""상태별로 실제 무엇을 보낼지 정하는 순수 함수 — I/O 없이 테스트 가능. can_guard.py 의 메인 루프가 이
함수의 출력을 `plausibility.clamp_range` 에 마지막으로 한 번 더 통과시킨 뒤 인코딩한다(방어 이중화).
"""
from dataclasses import replace

from protocol import Command
from state_machine import State


def command_for_state(state, last_valid_cmd, held_eps_cmd, eps_limiter, acc_limiter, dt_s):
    """state: 이번 주기에 확정된 상태(state_machine.next_state 의 결과).
    last_valid_cmd: 마지막으로 성공적으로 받은 제어 명령(ACTIVE/DEGRADED 용).
    held_eps_cmd: HOLDING/STOPPED 진입 시점에 얼린 조향각(호출자가 상태 전이 시점에 캡처해서 넘긴다).
    eps_limiter/acc_limiter: `plausibility.RateLimiter` 인스턴스 — 주기마다 같은 객체를 재사용(내부 상태 보유).
    반환: 클램프 전 `Command`(호출자가 `plausibility.clamp_range` 를 마지막으로 한 번 더 돌린다)."""
    if state == State.INIT:
        eps_limiter.reset(0.0)
        acc_limiter.reset(0.0)
        return Command()   # 전부 기본값 — En=0, 안전 상태

    if state in (State.ACTIVE, State.DEGRADED):
        # DEGRADED 의 실제 감속 정책은 미정(§5.D, §7.2 "GPU-crash rule") — 팀 정책이 오기 전까지는
        # ACTIVE 와 동일하게 클램프된 명령을 그대로 통과시킨다. 여기가 그 정책을 넣을 자리다.
        eps = eps_limiter.step(last_valid_cmd.eps_cmd, dt_s)
        acc = acc_limiter.step(last_valid_cmd.acc_cmd, dt_s)
        return replace(last_valid_cmd, eps_cmd=eps, acc_cmd=acc)

    if state == State.HOLDING:
        eps = eps_limiter.step(held_eps_cmd, dt_s)   # 목표=얼린 값 → 변화율 제한 안에서 사실상 유지
        acc = acc_limiter.step(0.0, dt_s)             # 가감속 0 으로 램프(§5.D)
        return replace(last_valid_cmd, eps_cmd=eps, acc_cmd=acc)

    if state == State.STOPPED:
        # TODO(§5.D 미확정 항목): 실제 차량 속도를 모르는 상태라 "0 수렴 후 En=0" 을 시간(HOLDING 지속시간)
        # 만으로 근사한다 — can_guard 가 RX(0x711 VS)도 구독하게 되면 속도 기반 판정으로 바꿔야 한다.
        return Command(eps_en=False, acc_en=False, aeb_en=last_valid_cmd.aeb_en,
                       eps_speed=last_valid_cmd.eps_speed)

    raise ValueError(f'알 수 없는 상태: {state}')
