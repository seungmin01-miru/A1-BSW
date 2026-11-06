"""어댑터의 경기 모드 판단 — ROS 무관 순수 클래스(계획서 8.2 D3, D7, 8.4 D11, 브리프 A8).

경기 상태를 판단하는 곳은 can_guard 하나뿐이다(D5). 여기는 StatusChannel 값을 보고 어댑터의 입력·기록 규칙을 고르기만 한다.
"경기 모드" = StatusChannel 이 있고, race_enabled 이고, stamp 가 0.1 s 안. 그 밖에는 **기존 규칙 완전히 그대로**(150 ms 래치, ±15·±45 래치).

| 규칙 | 기존(폴백) | 경기 모드 |
|---|---|---|
| 입력 끊김 | stale_s 넘으면 고장 래치 | hold_after_s 넘으면 기록·하트비트만 멈춤(래치 없음), 입력이 돌아오면 재개 → can_guard 가 판정 |
| 범위 밖 입력 | 버리고 래치 | input_grace·long_override·PRE_GO/SLOW/PARKED/SUSPENDED 면 잘라서 쓰고 래치 안 함, 그 밖은 버리고 래치 |
| NaN·inf | 버리고 래치 | 같음(항상) |
| 래치 해제 | 운영자 disarm 만 | + PRE_GO 면 정상 입력 pre_go_relatch_s 뒤 자동 해제. PRE_GO 는 can_guard 가 브레이크로 잡고 있어 해제해도 차가 안 움직인다 |
| SlewLimiter | arm 순간만 실제 핸들각으로 | + PRE_GO/PARKED/SUSPENDED 동안 매 주기 실제 핸들각으로(Go 순간 조향 계단 방지) |
"""
import math
from dataclasses import dataclass
from typing import Optional

from a1_control_adapter.conversion import InputLimits, validate

# can_guard 가 모델 종방향을 어차피 안 쓰는 상태(D7)
CLIP_PHASES = ('PRE_GO', 'SLOW', 'PARKED', 'SUSPENDED')
# 조향도 모델 값을 안 쓰는 상태 → Slew 를 실제 핸들각에 붙여 둔다(A8). SLOW 는 조향이 모델 값이라 뺀다.
SLEW_RESET_PHASES = ('PRE_GO', 'PARKED', 'SUSPENDED')
# 이 상태는 재시작으로만 풀린다 — D11 도 건드리지 않는다
NO_RELATCH_GUARD_STATES = ('STOPPED', 'ESTOP')


@dataclass
class InputDecision:
    accel_mps2: float = 0.0
    steer_tire_deg: float = 0.0
    why: Optional[str] = None     # 있으면 입력을 버린다(arm 중이면 래치)
    clipped: bool = False         # 잘라서 받아들임(D7)


@dataclass
class TickDecision:
    race: bool = False             # 이번 주기 경기 모드인가
    latch_reason: Optional[str] = None
    release_latch: bool = False    # D11 자동 해제
    holding: bool = False          # D3: 끊김 — 기록·하트비트 멈춤(래치 아님)
    input_ok: bool = False         # 이번 규칙의 신선도 기준을 통과한 입력이 있나
    reset_slew: bool = False       # A8


class RaceRules:
    """set_status() 로 최신 StatusChannel 값을 넣고, 입력마다 check_input(), 주기마다 decide() 를 부른다."""

    def __init__(self, limits: InputLimits, stale_s=0.15, hold_after_s=0.10, pre_go_relatch_s=1.0, status_max_age_s=0.1):
        self.lim = limits
        self.stale_s = stale_s
        self.hold_after_s = hold_after_s
        self.pre_go_relatch_s = pre_go_relatch_s
        self.status_max_age_s = status_max_age_s
        self._st = None
        self._stamp = None
        self._relatch_since = None

    # ── StatusChannel 값 ───────────────────────────────────
    def set_status(self, st, now):
        """st = GuardStatus 또는 None(없음·오래됨). age_s 는 read() 시점 값이다."""
        self._st = st
        self._stamp = None if st is None else now - st.age_s

    def race_active(self, now):
        return (self._st is not None and bool(self._st.race_enabled)
                and now - self._stamp <= self.status_max_age_s)

    def phase(self, now):
        """경기 모드의 표시 상태 이름(RACING + slow_active = SLOW). 경기 모드가 아니면 None."""
        if not self.race_active(now):
            return None
        name = self._st.race_phase_name
        return 'SLOW' if name == 'RACING' and self._st.slow_active else name

    def _clip_window(self, now):
        return self._st.input_grace or self._st.long_override or self.phase(now) in CLIP_PHASES

    # ── 입력 ───────────────────────────────────────────────
    def check_input(self, accel_mps2, steer_tire_deg, now):
        why = validate(accel_mps2, steer_tire_deg, self.lim)
        if why is None:
            return InputDecision(accel_mps2, steer_tire_deg)
        finite = math.isfinite(accel_mps2) and math.isfinite(steer_tire_deg)
        if finite and self.race_active(now) and self._clip_window(now):
            a, s = self.lim.accel_abs_mps2, self.lim.steer_tire_abs_deg
            return InputDecision(max(-a, min(a, accel_mps2)), max(-s, min(s, steer_tire_deg)), clipped=True)
        self._relatch_since = None     # 고장 입력이 오면 D11 의 "정상 입력 연속"을 처음부터
        return InputDecision(why=why)

    # ── 주기 ───────────────────────────────────────────────
    def decide(self, now, input_age, steer_armed, long_armed, latched, wait_restart=False, actual_steer_fresh=False):
        """input_age: 마지막 유효 입력 뒤 경과(s), None = 없음. actual_steer_fresh: 0x200 실제 핸들각이 신선한가."""
        armed = steer_armed or long_armed
        if not self.race_active(now):
            self._relatch_since = None
            fresh = input_age is not None and input_age <= self.stale_s
            d = TickDecision(input_ok=fresh)
            if armed and not fresh and not latched:
                d.latch_reason = '제어기 입력 끊김' + (f' ({input_age * 1000:.0f} ms)' if input_age is not None else '')
            return d
        ok = input_age is not None and input_age <= self.hold_after_s
        phase = self.phase(now)
        d = TickDecision(race=True, input_ok=ok, holding=armed and not ok, reset_slew=phase in SLEW_RESET_PHASES)
        d.release_latch = self._relatch(now, phase, ok, armed, steer_armed, latched, wait_restart, actual_steer_fresh)
        return d

    def _relatch(self, now, phase, ok, armed, steer_armed, latched, wait_restart, actual_steer_fresh):
        """D11. 축 플래그가 하나도 안 켜져 있으면(운영자가 내림) 해제하지 않는다 — 다시 올리는 건 운영자 몫(wait_restart 도 같음).
        차속 조건: StatusChannel 에 차속이 없어 phase==PRE_GO + guard_state 로 대신한다(PRE_GO 는 can_guard 가 브레이크로 잡고 있음)."""
        eligible = (latched and armed and not wait_restart and ok and phase == 'PRE_GO'
                    and self._st.guard_state_name not in NO_RELATCH_GUARD_STATES
                    and (actual_steer_fresh or not steer_armed))
        if not eligible:
            self._relatch_since = None
            return False
        if self._relatch_since is None:
            self._relatch_since = now
        if now - self._relatch_since >= self.pre_go_relatch_s:
            self._relatch_since = None
            return True
        return False
