"""출력 클램핑 — §3-7("NN 은 분포 밖 입력에 쓰레기를 낼 수 있다. 이 체크는 나중에 MCU(=여기)로 옮긴다")이
can_guard 에 도착한 형태. 제어 노드가 무엇을 보내든, 차량으로 나가기 전 마지막 관문이 여기다.

범위(range) 클램프는 DBC 그대로라 지금 확정할 수 있다. 변화율(rate) 제한은 실차/EAIT 보드 고유의 값이라
`can_status_parameters_full.md`(이 저장소에 없음) 나 팀 확인이 필요하다 — 그래서 `RateLimiter` 는 만들되
기본은 "무제한"(None)이다. **숫자를 임의로 넣지 않는다** — 안전 파라미터를 추측으로 채우면 그 자체가 위험.
"""
from dataclasses import dataclass, replace

# DBC 원본 범위 (EAIT_CAN(AVANTE_CN7).dbc, 0x156/0x157). 여기 숫자를 바꾸면 DBC 와도 맞춰야 한다.
EPS_CMD_MIN, EPS_CMD_MAX = -500.0, 500.0       # deg
ACC_CMD_MIN, ACC_CMD_MAX = -3.0, 1.0           # m/s^2
EPS_SPEED_MIN, EPS_SPEED_MAX = 10, 250
AEB_DECEL_MIN, AEB_DECEL_MAX = 0.0, 1.0        # g


@dataclass
class Violation:
    """클램프가 실제로 값을 깎았을 때 하나씩 — can_guard 가 카운트·로깅한다(§7.2 Plausibility)."""
    field: str
    requested: float
    clamped: float


def clamp_range(cmd, violations_out=None):
    """`protocol.Command` 를 받아 DBC 범위 안으로 깎은 **새** Command 를 반환(원본은 안 바꿈).
    violations_out 을 주면 실제로 깎인 필드를 Violation 으로 append(빈 리스트면 아무 것도 안 깎인 것)."""
    def _clip(name, v, lo, hi):
        c = max(lo, min(hi, v))
        if c != v and violations_out is not None:
            violations_out.append(Violation(name, v, c))
        return c

    return replace(
        cmd,
        eps_cmd=_clip('eps_cmd', cmd.eps_cmd, EPS_CMD_MIN, EPS_CMD_MAX),
        acc_cmd=_clip('acc_cmd', cmd.acc_cmd, ACC_CMD_MIN, ACC_CMD_MAX),
        eps_speed=int(_clip('eps_speed', cmd.eps_speed, EPS_SPEED_MIN, EPS_SPEED_MAX)),
        aeb_decel_value=_clip('aeb_decel_value', cmd.aeb_decel_value, AEB_DECEL_MIN, AEB_DECEL_MAX),
    )


class RateLimiter:
    """한 신호가 한 주기에 얼마나 바뀔 수 있는지 제한. `max_delta_per_s=None` 이면 무제한(기본 — 아래 참고).

    ⚠️ **미확정**: `eps_cmd`(조향각) 의 deg/s 상한, `acc_cmd`(가감속) 의 jerk(m/s^3) 상한은 실차 액추에이터
    대역폭·조향 기어비에 따라 정해지는 값이라 이 저장소에 근거가 없다. 팀 확인 전까지 `None` 으로 두고
    can_guard 를 이 상태로 쓰면 **범위 클램프만** 적용된다(변화율은 안 걸림) — 실차 시험(P-5) 전 반드시 채운다."""

    def __init__(self, max_delta_per_s=None):
        self.max_delta_per_s = max_delta_per_s
        self._last_value = None

    def reset(self, value=None):
        self._last_value = value

    def step(self, value, dt_s):
        """dt_s 초 지난 뒤 새 요청값 → 실제로 허용되는 값. 첫 호출(또는 reset 이후)은 그대로 통과."""
        if self.max_delta_per_s is None or self._last_value is None or dt_s <= 0:
            self._last_value = value
            return value
        max_delta = self.max_delta_per_s * dt_s
        delta = value - self._last_value
        if abs(delta) > max_delta:
            value = self._last_value + max_delta * (1 if delta > 0 else -1)
        self._last_value = value
        return value
