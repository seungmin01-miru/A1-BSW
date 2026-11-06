"""경기 상태별 명령 제한 — 계획서 5절. 최종 0x210 = `command_for_state()` 결과에 `apply_race_envelope()` 를 씌운 값.

안전 축과 경기 축 중 더 엄격한 쪽이 이긴다(3.1절). 안전 상태가 ESTOP/HOLDING/DEGRADED/STOPPED 이면 그 출력의 브레이크·
가속보다 약해지지 않게 가속 min, 브레이크 max 로 합치고 ESTOP 의 조향은 건드리지 않는다.

**auto 000 을 PRE_GO·PARKED·SUSPENDED 에서 만들지 않는다**: 어댑터가 disarm·재arm 중이라 입력이 None 이거나 auto 000 이어도
auto 를 보낸 적이 있으면(`last_auto_cmd`) 그 auto 비트로 브레이크를 유지한다(A3) — 침묵하면 TODO-2 때문에 브레이크가 사라질
수 있다. auto 를 한 번도 안 보냈으면 기존 INIT 처럼 침묵한다(None).

서행 종방향은 `ctx.slow_ctrl.step(speed_kph_or_None, dt_s) -> (acc_pct, brake_pct)`, `ctx.slow_ctrl.reset()` 로 부른다
(speed_ctrl.SlowController). step 을 부르는 것 말고는 상태가 없다.
"""
from dataclasses import dataclass, replace
from typing import Any, Optional

from command_policy import HOLD_STATES
from protocol import Command
from race_state import RacePhase
from state_machine import State


@dataclass
class RaceEnvelopeContext:
    safety_state: State
    actual_steer_deg: Optional[float] = None    # 0x200 **마지막** 실제 핸들각(오래돼도 그대로), 한 번도 못 봤으면 None
    vehicle_speed_kph: Optional[float] = None   # 0x201 좌우 평균, 나이 0.1 s 초과면 None
    last_auto_cmd: Optional[Command] = None     # auto 비트가 있던 마지막 명령
    slow_ctrl: Any = None
    dt_s: float = 0.02


def _has_any_auto(cmd):
    return cmd is not None and (cmd.steer_auto or cmd.brake_auto or cmd.acc_auto)


def _hold_envelope(out, limits, ctx):
    """PRE_GO·PARKED·SUSPENDED — 가속 0, 브레이크 유지, 조향은 실제값."""
    strict = ctx.safety_state in HOLD_STATES
    live = out if _has_any_auto(out) else None
    base = live if live is not None else ctx.last_auto_cmd
    if not _has_any_auto(base):
        return None

    # 브레이크: 모델 값은 무시한다. 안전 축이 건 브레이크보다는 약해지지 않는다.
    brake = limits.hold_brake_pct
    if strict and live is not None:
        brake = max(live.brake_cmd_pct, brake)

    if ctx.safety_state == State.ESTOP and live is not None:
        steer = live.steer_cmd_deg
    elif ctx.actual_steer_deg is not None:
        steer = ctx.actual_steer_deg
    elif strict and live is not None:
        steer = live.steer_cmd_deg
    elif limits.phase == RacePhase.SUSPENDED:
        steer = base.steer_cmd_deg     # RES 중 차량은 0x210 을 무시한다. 브레이크를 놓지 않는 쪽을 택한다
    else:
        return None                    # A4: 조향 값을 모르면 그 주기 침묵
    return replace(base, steer_cmd_deg=steer, acc_cmd_pct=0.0, brake_cmd_pct=brake)


def _slow_envelope(out, limits, ctx):
    """서행 — 조향은 모델, 종방향은 서행 제어. 안전 상태거나 서행 중 Finish 면 둘 중 더 엄격한 쪽."""
    speed = ctx.vehicle_speed_kph
    acc, brake = ctx.slow_ctrl.step(speed, ctx.dt_s)
    if speed is None:   # A4
        acc = 0.0
        brake = max(brake, limits.hold_brake_pct)
    if limits.allow_model_stop or ctx.safety_state in HOLD_STATES:
        acc = min(out.acc_cmd_pct, acc)
        brake = max(out.brake_cmd_pct, brake)
        if brake > 0.0:
            acc = 0.0
    return replace(out, acc_cmd_pct=acc, brake_cmd_pct=brake)


def _cap_envelope(out, limits, speed):
    """속도 상한(FINISHED). 차속을 모르면 가속 0(A4)."""
    if speed is None or speed > limits.speed_cap_kph:
        out = replace(out, acc_cmd_pct=0.0)
    if speed is not None and limits.speed_cap_brake_kph is not None and speed > limits.speed_cap_brake_kph:
        out = replace(out, brake_cmd_pct=max(out.brake_cmd_pct, limits.hold_brake_pct))
    return out


def apply_race_envelope(out, limits, ctx):
    """out: command_for_state 결과(`Command` 또는 None=침묵), limits: `RaceLimits`, ctx: `RaceEnvelopeContext`.
    반환: 경기 제한을 씌운 `Command`, 또는 None(이번 주기 0x210 침묵). 어댑터 auto 비트는 새로 만들지 않는다."""
    if not limits.allow_motion:
        return _hold_envelope(out, limits, ctx)

    if limits.long_override_start:
        ctx.slow_ctrl.reset()
    if not _has_any_auto(out):
        # 주행 중 disarm 은 기존 규칙 그대로다(1.4절 CLAUDE.md). 서행 제어는 다음에 처음부터 시작한다.
        if limits.long_override:
            ctx.slow_ctrl.reset()
        return out

    if limits.long_override:
        out = _slow_envelope(out, limits, ctx)
    elif limits.speed_cap_kph is not None:
        out = _cap_envelope(out, limits, ctx.vehicle_speed_kph)
    if limits.acc_cap_pct is not None and out.acc_cmd_pct > limits.acc_cap_pct:
        out = replace(out, acc_cmd_pct=limits.acc_cap_pct)
    return out
