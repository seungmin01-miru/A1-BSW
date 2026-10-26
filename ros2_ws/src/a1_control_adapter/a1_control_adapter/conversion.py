"""제어기 출력(목표 가감속 m/s², 타이어각 °) → can_guard 명령(핸들각 °, 브레이크 %, 가속 %) 변환 — ROS 무관 순수 함수.

표(map)는 전부 config/track.yaml 에서 온다. 초기값은 10-07 리프트·10-08 주행 이미지에서 잡은 **잠정값**이고
(docs/a1_vehicle_characteristics.md), 트랙에서 tools/track_day/TRACK_MANUAL.md T2~T4 로 실측해 바꾼다.

  조향:   타이어각 → (steer_sign) → 표 보간 → 핸들각 → ±한계 클램프 → 속도 제한
  종방향: a > +불감대 → 가속 % 표, a < −불감대 → 브레이크 % 표(감속 크기 기준), 그 사이 → 둘 다 0
          가속·브레이크는 동시에 0 이 아닐 수 없다(표 구조상 보장 + can_guard 정책과 같음)
표 밖 입력은 표 끝값으로 포화한다(외삽 안 함).
"""
import math
from bisect import bisect_right
from dataclasses import dataclass


def interp(x, xs, ys):
    """구간 선형 보간. xs 는 오름차순, 범위 밖은 끝값으로 포화."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def _check_table(name, xs, ys):
    if len(xs) < 2 or len(xs) != len(ys):
        raise ValueError(f'{name}: 점이 2개 이상이고 x·y 길이가 같아야 함 ({len(xs)} vs {len(ys)})')
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError(f'{name}: x 가 엄격한 오름차순이 아님 {xs}')
    if not all(math.isfinite(v) for v in list(xs) + list(ys)):
        raise ValueError(f'{name}: NaN/inf 포함')


@dataclass
class SteerMap:
    """타이어각 ↔ 핸들각 표. 좌우 비대칭이면 음수 쪽 점을 따로 넣는다(예: tire [-20,-10,0,10,20])."""
    tire_deg: list
    wheel_deg: list
    sign: float = 1.0           # 제어기 + 좌회전 ↔ CAN + 방향이 반대면 −1 (T1 에서 확정)
    wheel_limit_deg: float = 150.0

    def __post_init__(self):
        _check_table('steer_map', self.tire_deg, self.wheel_deg)
        if any(b < a for a, b in zip(self.wheel_deg, self.wheel_deg[1:])):
            raise ValueError('steer_map: 핸들각이 타이어각에 대해 단조 증가가 아님')
        if self.sign not in (1.0, -1.0):
            raise ValueError('steer_sign 은 1 또는 -1')

    def to_wheel(self, tire_deg):
        w = interp(self.sign * tire_deg, self.tire_deg, self.wheel_deg)
        return max(-self.wheel_limit_deg, min(self.wheel_limit_deg, w))

    def to_tire(self, wheel_deg):
        """역변환(0x200 실측 핸들각 → 타이어각, 상태 표시·제어기 피드백용)."""
        return self.sign * interp(wheel_deg, self.wheel_deg, self.tire_deg)


@dataclass
class LongMap:
    """목표 가감속 → (가속 %, 브레이크 %). 감속 표는 감속 **크기**(양수 m/s²)로 쓴다."""
    accel_mps2: list
    acc_pct: list
    decel_mps2: list
    brake_pct: list
    deadband_mps2: float = 0.1
    acc_max_pct: float = 30.0
    brake_max_pct: float = 100.0

    def __post_init__(self):
        _check_table('accel_map', self.accel_mps2, self.acc_pct)
        _check_table('brake_map', self.decel_mps2, self.brake_pct)
        for name, ys in (('acc_pct', self.acc_pct), ('brake_pct', self.brake_pct)):
            if min(ys) < 0 or max(ys) > 100:
                raise ValueError(f'{name}: 0~100 % 밖 값')
        if not (0 <= self.acc_max_pct <= 100 and 0 <= self.brake_max_pct <= 100):
            raise ValueError('acc_max_pct/brake_max_pct 는 0~100')

    def to_pedals(self, accel_mps2):
        if accel_mps2 > self.deadband_mps2:
            return min(interp(accel_mps2, self.accel_mps2, self.acc_pct), self.acc_max_pct), 0.0
        if accel_mps2 < -self.deadband_mps2:
            return 0.0, min(interp(-accel_mps2, self.decel_mps2, self.brake_pct), self.brake_max_pct)
        return 0.0, 0.0


@dataclass
class InputLimits:
    """이 범위를 벗어난 입력은 '제어기 고장'으로 보고 버린다(클램프 아님). 정상 범위는 표가 포화로 처리."""
    accel_abs_mps2: float = 15.0
    steer_tire_abs_deg: float = 45.0


def validate(accel_mps2, steer_tire_deg, lim):
    """문제 없으면 None, 있으면 사유 문자열."""
    if not (math.isfinite(accel_mps2) and math.isfinite(steer_tire_deg)):
        return f'NaN/inf 입력 (accel={accel_mps2}, steer={steer_tire_deg})'
    if abs(accel_mps2) > lim.accel_abs_mps2:
        return f'가감속 {accel_mps2:.2f} m/s² 가 입력 한계 ±{lim.accel_abs_mps2:g} 밖'
    if abs(steer_tire_deg) > lim.steer_tire_abs_deg:
        return f'타이어각 {steer_tire_deg:.2f}° 가 입력 한계 ±{lim.steer_tire_abs_deg:g} 밖'
    return None


class SlewLimiter:
    """핸들각 속도 제한 [deg/s]. rate <= 0 이면 통과. reset(x) 로 현재 위치에서 다시 시작(arm 순간 튐 방지)."""

    def __init__(self, rate_per_s):
        self.rate = rate_per_s
        self.y = None

    def reset(self, y=None):
        self.y = y

    def step(self, target, dt):
        if self.rate <= 0 or self.y is None or dt <= 0:
            self.y = target
            return target
        d = self.rate * dt
        self.y = max(self.y - d, min(self.y + d, target))
        return self.y
