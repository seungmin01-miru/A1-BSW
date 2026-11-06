"""상태별로 실제 무엇을 보낼지 정하는 순수 함수 — I/O 없이 테스트 가능. can_guard.py 의 메인 루프가 이
함수의 출력을 `plausibility.clamp_range` 에 마지막으로 한 번 더 통과시킨 뒤 인코딩한다(방어 이중화).

2026-10-06 실차 0x210(조향 deg, 브레이크 %, 가속 %, 축별 auto)으로 전환. EAIT 시절과 달라진 점:
  - "En" 대신 **축별 auto 비트**. auto=0 이면 그 축은 차량이 우리 명령을 무시한다(수동).
  - AEB 신호가 없다 → 정지 수단은 **브레이크 명령**뿐이다.
  - **auto 비트는 마지막으로 받은 명령 그대로**: can_guard 는 제어 노드가 넘겨주지 않은 축의 제어권을 스스로
    가져가지 않는다(예: 사람이 브레이크 페달을 쥐고 있던 축을 갑자기 auto 로 바꾸지 않음).
  - STOPPED: `stopped_mode='hold'`(기본) = auto 유지 + 브레이크 유지 + 가속 0 + 조향 고정.
    `'manual'` = auto 전부 0 + 명령 0(차량을 사람에게 넘김) — ⚠️ 이 차는 auto 000 을 받으면 페일세이프 브레이크가
    풀린다(11-03). 무인 차량에선 비권장.

2026-11-04 (bsw_test 00 SAF-ESTOP) 변경:
  - **브레이크 프로파일** `BrakeProfile(start, ramp, max)`: 보호 상태에 들어가는 순간 `start` % 로 바로 올리고,
    그 뒤 `ramp` %/s 로 `max` % 까지 **서서히** 올린다. 브레이크 응답이 느려 한 번에 세게 밟으면 휠락 위험이 커서
    (사용자 결정) 계단 대신 이 방식. 제어 노드가 이미 더 세게 밟고 있었으면 줄이지 않는다.
      HOLDING/DEGRADED/STOPPED(hold): 기본 30 % → 20 %/s → 60 % (잠정)
      ESTOP:                          기본 30 % → 40 %/s → 82 % (차량 위치는 82 % 에서 포화, 잠정)
  - **ESTOP**: 가속 즉시 0(변화율 제한 무시), 조향 진입 순간 값 고정, auto 비트 유지.
    단 원격(앱) En=1 동안은 차량이 앱을 따르므로 고정값을 0x200 실제 핸들각으로 갱신(2026-11-05, held_steer_follow_remote).
  - **보내지 않음(None)**: auto 000 을 보내면 페일세이프 브레이크가 풀리므로,
      INIT(`init_mode='silent'`, 기본) — 첫 유효 명령 전에는 0x210 을 아예 보내지 않는다.
      ESTOP 인데 마지막 명령의 auto 가 전부 0(제어권을 받은 적 없음) — 역시 보내지 않는다(차량 페일세이프에 맡김).
      ACTIVE/HOLDING/DEGRADED/STOPPED(hold) 에서 제어 노드 명령이 auto 000(어댑터 disarm, lift_cmd auto off)
      — `auto_off_mode='silent'`(기본)이면 보내지 않는다. 예전처럼 auto 000 을 내보내려면 'send'.
      STOPPED(manual) 은 "사람에게 넘김"을 명시적으로 고른 것이라 그대로 auto 000 을 보낸다.
"""
from dataclasses import dataclass, replace

from protocol import Command
from state_machine import State

STOPPED_MODES = ('hold', 'manual')
INIT_MODES = ('silent', 'legacy')
AUTO_OFF_MODES = ('silent', 'send')
HOLD_STATES = (State.HOLDING, State.DEGRADED, State.STOPPED, State.ESTOP)


@dataclass(frozen=True)
class BrakeProfile:
    start_pct: float    # 진입 즉시 이 값까지 한 번에
    ramp_pct_s: float   # 그다음 이 속도로
    max_pct: float      # 여기까지

    def __post_init__(self):
        if not (0 <= self.start_pct <= self.max_pct <= 100 and self.ramp_pct_s >= 0):
            raise ValueError(f'브레이크 프로파일 값이 이상함: {self}')


DEFAULT_HOLD_PROFILE = BrakeProfile(30.0, 20.0, 60.0)
DEFAULT_ESTOP_PROFILE = BrakeProfile(30.0, 40.0, 82.0)


class BrakeRamp:
    """can_guard 가 스스로 거는 브레이크의 현재값. 보호 상태 안에서는 **절대 줄지 않는다**."""

    def __init__(self):
        self.value = 0.0

    def reset(self, value):
        self.value = value

    def step(self, profile, floor_pct, dt_s):
        """floor_pct: 제어 노드가 마지막으로 요구한 브레이크(이보다 약하게는 안 감)."""
        cur = max(self.value, profile.start_pct, floor_pct)
        target = max(profile.max_pct, floor_pct)
        if cur < target:
            cur = min(target, cur + profile.ramp_pct_s * max(dt_s, 0.0))
        self.value = cur
        return cur


def held_steer_on_transition(prev_state, new_state, held_steer_deg, last_valid_cmd):
    """고정할 조향각 갱신 규칙 — ACTIVE/INIT 에서 HOLDING·DEGRADED·STOPPED·ESTOP 중 **어디로든** 처음 들어가는
    순간의 명령 조향각을 캡처하고, 그 상태들끼리 옮겨 다닐 때는 처음 캡처한 값을 그대로 둔다.

    2026-10-06 리허설에서 잡은 버그의 수정: 차가 서 있을 때(차속 ≤ 정지 문턱) 명령이 끊기면 상태머신이
    HOLDING 을 거치지 않고 ACTIVE → STOPPED 로 바로 가는데, 예전 코드는 HOLDING/DEGRADED 진입 때만 캡처해서
    STOPPED 가 **오래된 값(초기 0°)**으로 조향을 고정했다 — 실차라면 핸들이 12° → 0° 로 튄다. 또 DEGRADED →
    HOLDING 에서 다시 캡처하면 DEGRADED 동안 계속 들어온(인지 없이 나온, 신뢰 못 할) 명령으로 갱신되므로 그것도 막는다."""
    if new_state in HOLD_STATES and prev_state not in HOLD_STATES:
        return last_valid_cmd.steer_cmd_deg
    return held_steer_deg


def held_steer_follow_remote(state, held_steer_deg, remote_enabled, actual_steer_deg, mode='follow'):
    """ESTOP 중 원격(앱) En=1 이면 고정 조향각을 차량 실제 핸들각(0x200)으로 계속 갱신한다(2026-11-05).

    11-04 17:00:43(bsw4): 앱 En 이 켜지면 차량은 우리 0x210 을 무시하고 앱(EPS_Cmd 0°)을 따른다 — 조향이 −53 → +7.6°.
    can_guard 는 ESTOP 진입 순간 값(−49.8°)을 계속 보냈고, 50 s 뒤 앱을 끄자 차량이 다시 우리 0x210 을 따라
    1 s 만에 핸들이 −53° 튀었다(그때 1.5 km/h). 원격이 쥐고 있는 동안은 실제 위치를 따라가 두면 해제 순간 튐이 없다.
    En 이 꺼진 뒤에는 마지막 실제 위치에 고정(원래 규칙). mode='hold' 면 예전 동작(진입값 고정)."""
    if mode == 'follow' and state == State.ESTOP and remote_enabled and actual_steer_deg is not None:
        return actual_steer_deg
    return held_steer_deg


def _has_any_auto(cmd):
    return cmd.steer_auto or cmd.brake_auto or cmd.acc_auto


def command_for_state(state, last_valid_cmd, held_steer_deg, steer_limiter, acc_limiter, brake_ramp, dt_s,
                      hold_profile=DEFAULT_HOLD_PROFILE, estop_profile=DEFAULT_ESTOP_PROFILE,
                      stopped_mode='hold', init_mode='silent', auto_off_mode='silent', last_auto_cmd=None):
    """state: 이번 주기에 확정된 상태(state_machine.next_state 의 결과).
    last_valid_cmd: 마지막으로 성공적으로 받은 제어 명령.
    held_steer_deg: 보호 상태 진입 시점에 얼린 조향각(호출자가 `held_steer_on_transition` 으로 캡처).
    steer_limiter/acc_limiter: `plausibility.RateLimiter` — 제어 노드 명령의 변화율 제한(기본 무제한).
    brake_ramp: `BrakeRamp` — can_guard 가 스스로 거는 브레이크.
    last_auto_cmd: auto 비트가 있던 마지막 명령(없으면 None).
    반환: 클램프 전 `Command`, 또는 **None = 이번 주기는 0x210 을 보내지 않는다**.

    2026-11-05 수정 — **ESTOP·STOPPED(hold) 는 disarm 으로 풀리지 않는다**: 예전에는 래치 상태에서 어댑터가 disarm
    (auto 000 명령)을 쓰면 last_valid_cmd 의 auto 가 0 이 되어 침묵 → 우리 브레이크가 사라졌다(보고서 2026-11-05
    시나리오 stop_disarm·estop_disarm). 두 상태는 재시작으로만 빠져나가는 래치이므로 auto 비트·명령 바닥값을
    last_auto_cmd 에서 가져온다. HOLDING 은 "명령 재개 → ACTIVE" 가 설계이므로 그대로(운용 규칙: 래치 중 disarm 금지)."""
    if state == State.INIT:
        steer_limiter.reset(0.0)
        acc_limiter.reset(0.0)
        brake_ramp.reset(0.0)
        return None if init_mode == 'silent' else Command()

    if state in (State.ESTOP, State.STOPPED) and not _has_any_auto(last_valid_cmd) \
            and last_auto_cmd is not None and _has_any_auto(last_auto_cmd):
        last_valid_cmd = last_auto_cmd   # 래치 상태: disarm(auto 000)을 무시하고 마지막 auto 비트·브레이크 바닥 유지

    if auto_off_mode == 'silent' and state != State.ESTOP and not _has_any_auto(last_valid_cmd) \
            and not (state == State.STOPPED and stopped_mode == 'manual'):
        # 제어권을 넘겨받지 않은 상태 — auto 000 을 보내면 페일세이프 브레이크가 풀리므로 침묵. 리미터·램프는
        # INIT 과 같이 0 으로(나중에 auto 를 켜면 0 에서 시작, 브레이크는 ACTIVE 가 다시 맞춘다).
        steer_limiter.reset(0.0)
        acc_limiter.reset(0.0)
        brake_ramp.reset(0.0)
        return None

    if state == State.ACTIVE:
        steer = steer_limiter.step(last_valid_cmd.steer_cmd_deg, dt_s)
        acc = acc_limiter.step(last_valid_cmd.acc_cmd_pct, dt_s)
        brake_ramp.reset(last_valid_cmd.brake_cmd_pct)   # 보호 상태로 가면 지금 브레이크에서부터 시작
        return replace(last_valid_cmd, steer_cmd_deg=steer, acc_cmd_pct=acc)

    if state == State.ESTOP:
        if not _has_any_auto(last_valid_cmd):
            return None
        steer = steer_limiter.step(held_steer_deg, dt_s)
        acc_limiter.reset(0.0)   # 가속은 변화율 제한 없이 즉시 0
        brake = brake_ramp.step(estop_profile, last_valid_cmd.brake_cmd_pct, dt_s)
        return replace(last_valid_cmd, steer_cmd_deg=steer, acc_cmd_pct=0.0, brake_cmd_pct=brake)

    if state in (State.HOLDING, State.DEGRADED) or (state == State.STOPPED and stopped_mode == 'hold'):
        # 조향은 진입 순간 값에 고정, 가속은 0, 브레이크는 프로파일(시작값 → 서서히 상한). 이미 더 세면 그대로.
        steer = steer_limiter.step(held_steer_deg, dt_s)
        acc = acc_limiter.step(0.0, dt_s)
        brake = brake_ramp.step(hold_profile, last_valid_cmd.brake_cmd_pct, dt_s)
        return replace(last_valid_cmd, steer_cmd_deg=steer, acc_cmd_pct=acc, brake_cmd_pct=brake)

    if state == State.STOPPED:   # stopped_mode == 'manual'
        return Command()

    raise ValueError(f'알 수 없는 상태: {state}')
