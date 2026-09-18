"""can_guard 상태머신 — 순수 함수로 만들어서(입력 상태 + 관측값 → 다음 상태) 하드웨어·타이밍 없이
결정론적으로 테스트한다. 실제 시각·CAN·공유메모리는 can_guard.py(메인 루프)의 몫이다.

can_stack_development.md §5.D 참고:
  INIT(En=0, 안전 상태) → 첫 유효 명령 → ACTIVE(클램프해서 전달)
  ACTIVE → 명령 나이 > watchdog_t → HOLDING(조향 유지, 감속 램프)
  HOLDING → 명령 재개 → ACTIVE | HOLDING 지속 → STOPPED(속도 0 수렴 후 En=0)
  (모든 상태) 인지 하트비트 끊김 → DEGRADED (§7.2 GPU-crash rule)
  (모든 상태) 재시작 → 항상 INIT 부터
"""
import enum


class State(enum.Enum):
    INIT = 'INIT'
    ACTIVE = 'ACTIVE'
    HOLDING = 'HOLDING'
    DEGRADED = 'DEGRADED'
    STOPPED = 'STOPPED'


DEFAULT_WATCHDOG_T_S = 0.050       # §3-6 제안값 "50 ms" — 팀 확정 필요(설계 §5.D 에 명시)
DEFAULT_STOPPED_AFTER_S = 2.0      # HOLDING 이 이만큼 지속되면 STOPPED 로 — 잠정값, 팀 확인 필요
DEFAULT_PERCEPTION_TIMEOUT_S = 0.5  # 인지 하트비트 타임아웃 — 잠정값, 팀 확인 필요


def next_state(current, cmd_age_s, holding_duration_s, perception_age_s,
               watchdog_t_s=DEFAULT_WATCHDOG_T_S,
               stopped_after_s=DEFAULT_STOPPED_AFTER_S,
               perception_timeout_s=DEFAULT_PERCEPTION_TIMEOUT_S):
    """현재 상태 + 관측값 → 다음 상태. cmd_age_s/perception_age_s 는 None 이면 "한 번도 못 받음"(무한 나이 취급).

    DEGRADED 는 인지가 끊겼을 때만 들어가고, 인지가 돌아오면 나머지 조건(cmd_age 등)에 따라 정상 전이로
    복귀한다 — DEGRADED 자체는 "정지"가 아니라 "인지 없이 판단하지 마라"는 신호라서 조향/가감속 판단은
    ACTIVE/HOLDING 로직과 같게 두고, 실제 감속 폭은 can_guard.py 의 TX 인코딩 단계에서 팀 정책으로 얹는다.

    **우선순위 설계 결정**: 명령이 stale 이면(cmd_stale) 인지 상태와 무관하게 HOLDING/STOPPED 계열로 간다 —
    조향 유지·감속 램프는 인지 입력이 필요 없는 동작이라, "명령 없음"이 "인지 없음"보다 항상 우선한다.
    즉 DEGRADED 는 "명령은 살아있는데 인지만 없는" 좁은 창에서만 나타난다.
    """
    perception_lost = perception_age_s is None or perception_age_s > perception_timeout_s
    cmd_stale = cmd_age_s is None or cmd_age_s > watchdog_t_s

    if current == State.INIT:
        if cmd_stale:
            return State.INIT   # 아직 첫 명령 안 옴 — 안전 상태 유지
        return State.DEGRADED if perception_lost else State.ACTIVE

    if current == State.STOPPED:
        return State.STOPPED   # 완전히 멈춘 뒤에는 재시작(INIT)으로만 빠져나간다 — 자동 복귀 없음(안전 우선)

    # ACTIVE / HOLDING / DEGRADED 공통: 명령이 살아있으면 정상 계열, 아니면 HOLDING 계열로
    if not cmd_stale:
        return State.DEGRADED if perception_lost else State.ACTIVE

    if holding_duration_s is not None and holding_duration_s >= stopped_after_s:
        return State.STOPPED
    return State.HOLDING
