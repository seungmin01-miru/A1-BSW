"""상태별 명령 생성 로직 — 실제 클램프·인코딩 없이 "무엇을 만들어내는가"만 확인."""
from command_policy import command_for_state
from plausibility import RateLimiter
from protocol import Command
from state_machine import State


def test_init_is_all_off():
    out = command_for_state(State.INIT, Command(eps_cmd=99), held_eps_cmd=0.0,
                            eps_limiter=RateLimiter(), acc_limiter=RateLimiter(), dt_s=0.01)
    assert out == Command()   # 완전 기본값(En=0 등)


def test_active_passes_through_last_valid_cmd():
    last = Command(eps_en=True, acc_en=True, eps_cmd=10.0, acc_cmd=0.5)
    out = command_for_state(State.ACTIVE, last, held_eps_cmd=0.0,
                            eps_limiter=RateLimiter(), acc_limiter=RateLimiter(), dt_s=0.01)
    assert out.eps_en is True and out.acc_en is True
    assert out.eps_cmd == 10.0 and out.acc_cmd == 0.5


def test_active_applies_rate_limit():
    eps_rl = RateLimiter(max_delta_per_s=10.0)
    eps_rl.step(0.0, dt_s=0.01)   # 기준값 확립
    last = Command(eps_cmd=1000.0)   # 극단적으로 큰 다음 요청
    out = command_for_state(State.ACTIVE, last, held_eps_cmd=0.0,
                            eps_limiter=eps_rl, acc_limiter=RateLimiter(), dt_s=0.1)
    assert out.eps_cmd == 1.0   # 0.1s * 10/s 만큼만 이동


def test_holding_freezes_steering_and_ramps_accel_to_zero():
    acc_rl = RateLimiter(max_delta_per_s=10.0)
    acc_rl.step(5.0, dt_s=0.01)   # 직전에 5.0 이었다고 가정
    last = Command(eps_en=True, acc_en=True, eps_cmd=20.0, acc_cmd=5.0)
    out = command_for_state(State.HOLDING, last, held_eps_cmd=20.0,
                            eps_limiter=RateLimiter(), acc_limiter=acc_rl, dt_s=0.1)
    assert out.eps_cmd == 20.0   # 얼린 값 그대로(변화율 제한 없어 즉시 도달)
    assert out.acc_cmd == 4.0    # 5.0 → 0.0 방향으로 0.1*10=1.0 만 이동


def test_holding_freezes_steering_even_if_last_cmd_was_different():
    """last_valid_cmd 가 그 사이 안 바뀌어도(제어 노드가 죽었으니 당연) held_eps_cmd 가 기준이다."""
    out = command_for_state(State.HOLDING, Command(eps_cmd=999.0), held_eps_cmd=20.0,
                            eps_limiter=RateLimiter(), acc_limiter=RateLimiter(), dt_s=0.01)
    assert out.eps_cmd == 20.0


def test_stopped_cuts_en_flags():
    last = Command(eps_en=True, acc_en=True, aeb_en=True, eps_speed=180)
    out = command_for_state(State.STOPPED, last, held_eps_cmd=20.0,
                            eps_limiter=RateLimiter(), acc_limiter=RateLimiter(), dt_s=0.01)
    assert out.eps_en is False and out.acc_en is False
    assert out.aeb_en is True   # AEB 는 안전장치라 끄지 않는다
    assert out.eps_speed == 180


def test_degraded_same_as_active_for_now():
    last = Command(eps_cmd=15.0, acc_cmd=0.3)
    a = command_for_state(State.ACTIVE, last, held_eps_cmd=0.0,
                          eps_limiter=RateLimiter(), acc_limiter=RateLimiter(), dt_s=0.01)
    d = command_for_state(State.DEGRADED, last, held_eps_cmd=0.0,
                          eps_limiter=RateLimiter(), acc_limiter=RateLimiter(), dt_s=0.01)
    assert a == d   # 팀 정책이 정해지기 전까지는 동일(§5.D TODO)


def test_init_resets_limiters():
    """INIT 을 지나면 리미터 기준값이 0 으로 리셋돼, 다음 ACTIVE 진입 시 이전 값에서 안 튄다."""
    eps_rl = RateLimiter(max_delta_per_s=10.0)
    eps_rl.step(500.0, dt_s=0.01)   # 뭔가 큰 값이 남아 있던 상태
    command_for_state(State.INIT, Command(), held_eps_cmd=0.0,
                      eps_limiter=eps_rl, acc_limiter=RateLimiter(), dt_s=0.01)
    out = command_for_state(State.ACTIVE, Command(eps_cmd=1.0), held_eps_cmd=0.0,
                            eps_limiter=eps_rl, acc_limiter=RateLimiter(), dt_s=0.1)
    assert out.eps_cmd == 1.0   # 0 근처에서 시작했으니 0.1*10=1.0 이내인 1.0 은 그대로 통과
