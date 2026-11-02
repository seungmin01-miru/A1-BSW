"""출력 클램핑 — §3-7("NN 은 분포 밖 입력에 쓰레기를 낼 수 있다. 이 체크는 나중에 MCU(=여기)로 옮긴다")이
can_guard 에 도착한 형태. 제어 노드가 무엇을 보내든, 차량으로 나가기 전 마지막 관문이 여기다.

2026-10-06 실차 0x210 으로 전환. 범위는 `DBC/A1_dbc_fixed.dbc` 그대로이고, 그보다 **좁은 운용 한계**
(`Limits`)를 CLI 로 줄 수 있다 — 리프트 시험은 조향 ±15°, 브레이크 60 %, 가속 10 % 로 시작(계획서 §6 D-c).
가속·브레이크 동시 명령은 브레이크 우선(가속을 0 으로) — 두 페달을 같이 밟는 명령은 정상 제어에서 나올 이유가
없고, 어느 쪽을 믿을지 모를 때는 멈추는 쪽이 안전하다.

변화율(rate) 제한은 실차 액추에이터 고유 값이라 아직 근거가 없다 — `RateLimiter` 는 만들되 기본은 "무제한"(None).
**숫자를 임의로 넣지 않는다** — 안전 파라미터를 추측으로 채우면 그 자체가 위험.
"""
from dataclasses import dataclass, replace

# DBC 범위 (A1_dbc_fixed.dbc, 0x210). 여기 숫자를 바꾸면 DBC 와도 맞춰야 한다.
STEER_CMD_MIN, STEER_CMD_MAX = -150.0, 150.0   # deg
BRAKE_CMD_MIN, BRAKE_CMD_MAX = 0.0, 100.0      # %
ACC_CMD_MIN, ACC_CMD_MAX = 0.0, 100.0          # %


@dataclass(frozen=True)
class Limits:
    """운용 한계 — DBC 범위 안쪽으로만 좁힐 수 있다(생성 시 검사)."""
    steer_abs_deg: float = STEER_CMD_MAX
    brake_max_pct: float = BRAKE_CMD_MAX
    acc_max_pct: float = ACC_CMD_MAX

    def __post_init__(self):
        if not (0 <= self.steer_abs_deg <= STEER_CMD_MAX and 0 <= self.brake_max_pct <= BRAKE_CMD_MAX
                and 0 <= self.acc_max_pct <= ACC_CMD_MAX):
            raise ValueError(f'운용 한계가 DBC 범위 밖: {self}')


DBC_LIMITS = Limits()


@dataclass
class Violation:
    """클램프가 실제로 값을 깎았을 때 하나씩 — can_guard 가 카운트·로깅한다(§7.2 Plausibility)."""
    field: str
    requested: float
    clamped: float


def clamp_range(cmd, limits=DBC_LIMITS, violations_out=None):
    """`protocol.Command` 를 받아 운용 한계(기본 DBC 범위) 안으로 깎은 **새** Command 를 반환(원본은 안 바꿈).
    violations_out 을 주면 실제로 깎인 필드를 Violation 으로 append(빈 리스트면 아무 것도 안 깎인 것)."""
    def _clip(name, v, lo, hi):
        c = max(lo, min(hi, v))
        if c != v and violations_out is not None:
            violations_out.append(Violation(name, v, c))
        return c

    steer = _clip('steer_cmd_deg', cmd.steer_cmd_deg, -limits.steer_abs_deg, limits.steer_abs_deg)
    brake = _clip('brake_cmd_pct', cmd.brake_cmd_pct, BRAKE_CMD_MIN, limits.brake_max_pct)
    acc = _clip('acc_cmd_pct', cmd.acc_cmd_pct, ACC_CMD_MIN, limits.acc_max_pct)
    if acc > 0 and brake > 0:
        if violations_out is not None:
            violations_out.append(Violation('acc_with_brake', acc, 0.0))
        acc = 0.0
    return replace(cmd, steer_cmd_deg=steer, brake_cmd_pct=brake, acc_cmd_pct=acc)


class RateLimiter:
    """한 신호가 한 주기에 얼마나 바뀔 수 있는지 제한. `max_delta_per_s=None` 이면 무제한(기본 — 아래 참고).

    ⚠️ **미확정**: 조향 deg/s, 가속 %/s 상한은 실차 액추에이터 대역폭에 따라 정해지는 값이라 근거가 없다.
    팀 확인 전까지 `None` 으로 두면 **범위 클램프만** 적용된다 — 실차 주행 시험(P-5) 전 반드시 채운다.
    (HOLDING 의 브레이크 램프처럼 can_guard 가 **스스로 만드는** 값의 램프에는 확정된 숫자를 넣어 쓴다.)"""

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
