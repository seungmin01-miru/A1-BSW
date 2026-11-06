"""경기 상태 기계 — 계획서 4절(WP2). 안전 상태(state_machine.py)와 별개의 축이고, 둘 중 더 엄격한 쪽이 이긴다(3.1절).

순수 함수 `next_race_state(state, inputs, params) -> (state, limits, events)`. 시각·CAN·공유메모리는 호출자의 몫이다.

신호 방식이 미확정(질의서 질문 2)이라 **올라가는 순간을 이벤트로 받고 상태는 래치**한다. 비트가 내려가는 것만으로는
상태를 되돌리지 않는다.

같은 주기의 이벤트는 stop > finish > slow_on > slow_off > go 순으로 적용한다. stop 은 SUSPENDED 로 보내고, slow_on 이
있으면 slow_off 는 버린다(보수적). 그 밖의 이벤트는 각자의 규칙대로 모두 반영하므로 제한 방향 깃발을 잃지 않는다
(예: PRE_GO 에서 go + slow_on → RACING + 서행).

계획서 4.2절 표에 없는 줄의 판단(보수적으로, 0.4절 — 메인이 open item 으로 모은다):
  - PRE_GO·SUSPENDED 의 finish: Slow On 과 같이 기억해 뒀다가 다음 출발이 FINISHED 가 된다.
  - PRE_GO·SUSPENDED 의 slow_off: 기억해 둔 Slow On 을 지운다(램프 없음 — 차가 서 있다). 안 지우면 경기 내내 서행이다.
  - FINISHED 의 slow_off: 무시한다. 정차 구역까지 서행으로 가도 규정 위반이 아니다.
  - RACING 의 go, FINISHED·PARKED 의 go·finish·slow_off: 무시하고 `race_event_ignored` 로 기록한다.
  - 서행 중 Finish: 모델이 정차해야 하므로(1.4절) 서행 제어가 종방향을 독점하면 차가 영영 못 선다 →
    `allow_model_stop` 으로 "서행 제어와 모델 중 더 엄격한 쪽"을 쓰게 한다(race_policy.py).
"""
import enum
from dataclasses import dataclass, replace
from typing import Optional, Tuple

from state_machine import DEFAULT_STOP_SPEED_KPH

RACE_STOP_ACTIONS = ('estop',)   # 질의서 질문 4 답에 따라 늘어날 수 있다. 답 전에는 다른 동작을 만들지 않는다.
RESUME_MODES = ('restart', 'go-reset')

# /race/state.phase 표시값(3.1절)
PHASE_CODE_PRE_GO = 0
PHASE_CODE_RACING = 1
PHASE_CODE_SLOW = 2
PHASE_CODE_FINISHED = 3
PHASE_CODE_PARKED = 4
PHASE_CODE_SUSPENDED = 5


class RacePhase(enum.Enum):
    PRE_GO = 'PRE_GO'
    RACING = 'RACING'
    FINISHED = 'FINISHED'
    PARKED = 'PARKED'
    SUSPENDED = 'SUSPENDED'


_NO_MOTION_PHASES = (RacePhase.PRE_GO, RacePhase.PARKED, RacePhase.SUSPENDED)
_WAITING_PHASES = (RacePhase.PRE_GO, RacePhase.SUSPENDED)   # 깃발을 기억해 뒀다가 출발 때 적용


@dataclass(frozen=True)
class RaceParams:
    """기본값은 계획서 10절. 표에 없는 값(★ 표시)은 잠정이고 보고에 적었다."""
    race_stop_action: str = 'estop'
    race_signal_timeout_s: float = 0.0        # 0 = 끔(기록만)
    race_signal_lost_note_s: float = 0.5      # ★ race_signal_lost 기록 기준
    resume_mode: str = 'restart'
    stop_speed_kph: float = DEFAULT_STOP_SPEED_KPH
    pre_go_brake_pct: float = 30.0
    slow_target_kph: float = 8.0
    slow_acc_cut_kph: float = 8.8
    slow_brake_kph: float = 9.3
    slow_unknown_brake_pct: float = 30.0      # ★ A4: 서행 중 차속 모를 때 브레이크(진입 브레이크 시작값)
    finish_cap_kph: float = 20.0
    finish_cap_brake_margin_kph: float = 0.5
    finish_over_brake_pct: float = 30.0       # ★ FINISHED 상한 + 0.5 넘을 때 브레이크
    parked_speed_kph: float = 0.5
    parked_after_s: float = 5.0
    parked_brake_pct: float = 60.0
    suspended_brake_pct: float = 30.0         # ★ ESTOP 프로파일 시작값. 안전 축이 ESTOP 이면 그 램프가 이긴다
    input_grace_s: float = 1.0
    slow_off_ramp_pct_s: float = 50.0         # 0 이하 = 램프 없음

    def __post_init__(self):
        if self.race_stop_action not in RACE_STOP_ACTIONS:
            raise ValueError(f'race_stop_action 이상함: {self.race_stop_action}')
        if self.resume_mode not in RESUME_MODES:
            raise ValueError(f'resume_mode 이상함: {self.resume_mode}')
        if not self.slow_target_kph < self.slow_acc_cut_kph < self.slow_brake_kph:
            raise ValueError('서행 속도는 목표 < 가속 차단 < 브레이크 순서여야 한다')
        for pct in (self.pre_go_brake_pct, self.parked_brake_pct, self.suspended_brake_pct,
                    self.finish_over_brake_pct, self.slow_unknown_brake_pct):
            if not 0 <= pct <= 100:
                raise ValueError(f'브레이크 % 범위 밖: {pct}')


@dataclass(frozen=True)
class RaceInputs:
    now: float
    events: Tuple[str, ...] = ()              # RaceSignalDecoder.take_events()
    stop_level: bool = False                  # 확인된 STOP level — go-reset 조건에만 쓴다
    res_active: bool = False
    res_alive_lost: bool = False
    race_signal_age_s: Optional[float] = None  # None = 0x124 를 본 적 없음
    vehicle_speed_kph: Optional[float] = None  # 0x201 좌우 평균, 나이 0.1 s 초과면 None
    last_acc_pct: float = 0.0                 # 직전 주기 가속 명령 % — Slow Off 램프 시작값(6.4절)


@dataclass(frozen=True)
class RaceState:
    phase: RacePhase = RacePhase.PRE_GO
    slow_active: bool = False
    finish_pending: bool = False               # PRE_GO·SUSPENDED 에서 받은 Finish
    suspend_reason: Optional[str] = None       # res / res_alive_lost / stop / race_signal_timeout
    parked_since: Optional[float] = None       # FINISHED 에서 정지 조건이 시작된 시각
    grace_until: Optional[float] = None        # D7 입력 유예 끝 시각
    ramp_start_t: Optional[float] = None       # Slow Off 램프 시작 시각, None = 램프 없음
    ramp_start_pct: float = 0.0
    reset_ready: bool = False                  # go-reset: 해제 조건이 이번 주기 전에 이미 만족됐는가
    long_override: bool = False                # 직전 주기에 서행 제어가 켜져 있었는가
    pre_go_motion: bool = False
    signal_lost: bool = False


@dataclass(frozen=True)
class RaceLimits:
    """계획서 4.3절 필드 + 정책에 필요한 것(phase, long_override_start, allow_model_stop, speed_cap_brake_kph, acc_cap_pct)."""
    phase: RacePhase
    allow_motion: bool
    long_override: bool = False
    long_override_start: bool = False                # 이번 주기에 서행 제어가 켜졌다 → 제어기 reset
    allow_model_stop: bool = False                   # 서행 중 Finish: 서행 제어와 모델 중 더 엄격한 쪽
    speed_target_kph: Optional[float] = None
    speed_cap_kph: Optional[float] = None            # 넘으면 가속 차단
    speed_cap_brake_kph: Optional[float] = None      # 넘으면 브레이크(서행의 브레이크는 제어기 안에서 처리)
    acc_ramp_active: bool = False
    acc_ramp_start_pct: float = 0.0
    acc_cap_pct: Optional[float] = None              # Slow Off 램프의 이번 주기 가속 상한
    hold_brake_pct: float = 0.0                      # 정지 유지·차속 모름·상한 초과 때 쓸 브레이크(상태별 뜻은 _limits)
    input_grace: bool = False


def race_phase_code(state):
    """/race/state.phase 표시값: PRE_GO=0, RACING=1, SLOW=2(RACING + slow_active), FINISHED=3, PARKED=4, SUSPENDED=5."""
    if state.phase == RacePhase.RACING:
        return PHASE_CODE_SLOW if state.slow_active else PHASE_CODE_RACING
    return {RacePhase.PRE_GO: PHASE_CODE_PRE_GO, RacePhase.FINISHED: PHASE_CODE_FINISHED,
            RacePhase.PARKED: PHASE_CODE_PARKED, RacePhase.SUSPENDED: PHASE_CODE_SUSPENDED}[state.phase]


def _suspend_reason(ev, inputs, params):
    if inputs.res_active:
        return 'res'
    if inputs.res_alive_lost:
        return 'res_alive_lost'
    if 'stop' in ev:
        return 'stop'
    if params.race_signal_timeout_s > 0 and inputs.race_signal_age_s is not None \
            and inputs.race_signal_age_s > params.race_signal_timeout_s:
        return 'race_signal_timeout'
    return None


def _start_racing(s, now, params, events, reason):
    """출발. Finish 를 기억하고 있으면 FINISHED 로 간다."""
    phase = RacePhase.FINISHED if s.finish_pending else RacePhase.RACING
    events.append(('race_phase', f'{s.phase.value}->{phase.value} {reason}'))
    return replace(s, phase=phase, finish_pending=False, suspend_reason=None, reset_ready=False,
                   grace_until=now + params.input_grace_s)


def _apply_waiting(s, ev, inputs, params, events, ready_before):
    """PRE_GO·SUSPENDED: 깃발을 기억하고, Go 가 오면 출발한다."""
    if 'finish' in ev and not s.finish_pending:
        s = replace(s, finish_pending=True)
        events.append(('race_finish_pending', s.phase.value))
    if 'slow_on' in ev and not s.slow_active:
        s = replace(s, slow_active=True)
        events.append(('race_slow', f'on {s.phase.value}'))
    elif 'slow_off' in ev and 'slow_on' not in ev and s.slow_active:
        s = replace(s, slow_active=False)
        events.append(('race_slow', f'off {s.phase.value}'))
    if 'go' in ev:
        if s.phase == RacePhase.PRE_GO:
            return _start_racing(s, inputs.now, params, events, 'go')
        if ready_before and s.reset_ready:
            return _start_racing(s, inputs.now, params, events, 'go-reset')
        events.append(('race_event_ignored', f'go {s.phase.value}'))
    return s


def _apply_after_finish(s, ev, events):
    if 'slow_on' in ev and not s.slow_active:
        s = replace(s, slow_active=True, ramp_start_t=None)
        events.append(('race_slow', f'on {s.phase.value}'))
    for name in ('go', 'finish', 'slow_off'):
        if name in ev:
            events.append(('race_event_ignored', f'{name} {s.phase.value}'))
    return s


def _apply_racing(s, ev, inputs, params, events):
    now = inputs.now
    if 'finish' in ev:   # finish 가 먼저다 — 같은 주기의 slow_off 는 FINISHED 규칙으로 버려지고 서행이 유지된다
        events.append(('race_phase', 'RACING->FINISHED finish'))
        return _apply_after_finish(replace(s, phase=RacePhase.FINISHED), ev - {'finish'}, events)
    if 'slow_on' in ev:
        if not s.slow_active:
            events.append(('race_slow', 'on RACING'))
        s = replace(s, slow_active=True, ramp_start_t=None)
    elif 'slow_off' in ev:
        if s.slow_active:
            events.append(('race_slow', 'off RACING'))
            start = max(0.0, min(100.0, inputs.last_acc_pct))
            s = replace(s, slow_active=False, grace_until=now + params.input_grace_s,
                        ramp_start_t=now if params.slow_off_ramp_pct_s > 0 else None, ramp_start_pct=start)
        else:
            events.append(('race_event_ignored', 'slow_off RACING not_slow'))
    if 'go' in ev:
        events.append(('race_event_ignored', 'go RACING'))
    return s


def _update_parked(s, inputs, params, events):
    if s.phase != RacePhase.FINISHED:
        return s
    v = inputs.vehicle_speed_kph
    if v is None or v > params.parked_speed_kph:
        return replace(s, parked_since=None)
    since = inputs.now if s.parked_since is None else s.parked_since
    if inputs.now - since >= params.parked_after_s:
        events.append(('race_phase', 'FINISHED->PARKED stopped'))
        return replace(s, phase=RacePhase.PARKED, parked_since=None, ramp_start_t=None)
    return replace(s, parked_since=since)


def _update_signal_lost(s, inputs, params, events):
    age = inputs.race_signal_age_s
    lost = age is not None and age > params.race_signal_lost_note_s
    if lost and not s.signal_lost:
        events.append(('race_signal_lost', f'age={age:.3f}'))
    elif s.signal_lost and not lost:
        events.append(('race_signal_restored', ''))
    return replace(s, signal_lost=lost)


def _update_pre_go_motion(s, inputs, params, events):
    v = inputs.vehicle_speed_kph
    moving = s.phase == RacePhase.PRE_GO and v is not None and v > params.stop_speed_kph
    if moving and not s.pre_go_motion:
        events.append(('pre_go_motion', f'{v:.1f} km/h'))
    return replace(s, pre_go_motion=moving)


def _limits(s, now, params, override_start):
    """상태 → 제한. hold_brake_pct 의 뜻: PRE_GO·PARKED·SUSPENDED 는 유지 브레이크, 서행은 차속 모를 때 브레이크,
    FINISHED(서행 아님)는 상한 + 여유 초과 때 브레이크."""
    grace = s.grace_until is not None and now < s.grace_until
    if s.phase in _NO_MOTION_PHASES:
        brake = {RacePhase.PRE_GO: params.pre_go_brake_pct, RacePhase.PARKED: params.parked_brake_pct,
                 RacePhase.SUSPENDED: params.suspended_brake_pct}[s.phase]
        return RaceLimits(phase=s.phase, allow_motion=False, hold_brake_pct=brake, input_grace=True)

    ramp_active, cap = False, None
    if s.ramp_start_t is not None:
        cap = s.ramp_start_pct + params.slow_off_ramp_pct_s * (now - s.ramp_start_t)
        ramp_active = cap < 100.0
        if not ramp_active:
            cap = None
    common = {'phase': s.phase, 'allow_motion': True, 'acc_ramp_active': ramp_active,
              'acc_ramp_start_pct': s.ramp_start_pct if ramp_active else 0.0, 'acc_cap_pct': cap}
    if s.slow_active:
        return RaceLimits(long_override=True, long_override_start=override_start,
                          allow_model_stop=s.phase == RacePhase.FINISHED, speed_target_kph=params.slow_target_kph,
                          speed_cap_kph=params.slow_acc_cut_kph, hold_brake_pct=params.slow_unknown_brake_pct,
                          input_grace=True, **common)
    if s.phase == RacePhase.FINISHED:
        return RaceLimits(speed_cap_kph=params.finish_cap_kph,
                          speed_cap_brake_kph=params.finish_cap_kph + params.finish_cap_brake_margin_kph,
                          hold_brake_pct=params.finish_over_brake_pct, input_grace=grace, **common)
    return RaceLimits(input_grace=grace, **common)


def next_race_state(state, inputs, params):
    """(RaceState, RaceInputs, RaceParams) → (RaceState, RaceLimits, 이벤트 목록). 이벤트는 (이름, detail 문자열)."""
    ev = set(inputs.events)
    events = []
    s = _update_signal_lost(state, inputs, params, events)

    reason = _suspend_reason(ev, inputs, params)
    if reason is not None and s.phase != RacePhase.SUSPENDED:
        events.append(('race_phase', f'{s.phase.value}->SUSPENDED {reason}'))
        s = replace(s, phase=RacePhase.SUSPENDED, suspend_reason=reason, parked_since=None, ramp_start_t=None,
                    grace_until=None)
    elif 'stop' in ev and s.phase == RacePhase.SUSPENDED:
        events.append(('race_event_ignored', 'stop SUSPENDED'))

    # go-reset: 해제 조건이 **Go 보다 먼저** 만족돼 있어야 한다(4.2절 "그 뒤 새 go 이벤트").
    ready_before = s.reset_ready
    if s.phase == RacePhase.SUSPENDED:
        v = inputs.vehicle_speed_kph
        s = replace(s, reset_ready=params.resume_mode == 'go-reset' and reason is None and not inputs.stop_level
                    and v is not None and v <= params.stop_speed_kph)

    if s.phase in _WAITING_PHASES:
        s = _apply_waiting(s, ev, inputs, params, events, ready_before)
    elif s.phase == RacePhase.RACING:
        s = _apply_racing(s, ev, inputs, params, events)
    else:
        s = _apply_after_finish(s, ev, events)

    s = _update_parked(s, inputs, params, events)
    s = _update_pre_go_motion(s, inputs, params, events)

    override = s.slow_active and s.phase in (RacePhase.RACING, RacePhase.FINISHED)
    limits = _limits(s, inputs.now, params, override and not state.long_override)
    if s.ramp_start_t is not None and not limits.acc_ramp_active:
        s = replace(s, ramp_start_t=None, ramp_start_pct=0.0)
    return replace(s, long_override=override), limits, events
