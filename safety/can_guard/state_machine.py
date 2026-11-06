"""can_guard 상태머신 — 순수 함수로 만들어서(입력 상태 + 관측값 → 다음 상태) 하드웨어·타이밍 없이
결정론적으로 테스트한다. 실제 시각·CAN·공유메모리는 can_guard.py(메인 루프)의 몫이다.

docs/can_stack_development.md §5.D 참고:
  INIT(En=0, 안전 상태) → 첫 유효 명령 → ACTIVE(클램프해서 전달)
  ACTIVE → 명령 나이 > watchdog_t → HOLDING(조향 유지, 감속 램프)
  HOLDING → 명령 재개 → ACTIVE | HOLDING 지속 또는 실측 차속 정지 문턱 이하 → STOPPED(En=0)
  (모든 상태) 인지 하트비트 끊김 → DEGRADED (§7.2 GPU-crash rule: "decel per team policy")
  DEGRADED 지속 또는 실측 차속 정지 문턱 이하 → STOPPED (2026-09-21 초안, 아래 문서 참고)
  (모든 상태) 재시작 → 항상 INIT 부터
  (모든 상태) 비상 정지 요청 → ESTOP(최우선, 래치). ESTOP → (해제 요청 + 요청 원천 모두 꺼짐 + 차속 정지) → STOPPED
    — 2026-11-04 추가(bsw_test 00 SAF-ESTOP). ESTOP 은 명령이 다시 와도 풀리지 않는다.
  경기 모드(`--race`, 2026-11-06, 계획서 8절) 인자는 전부 키워드이고 기본값이면 위 규칙과 비트 단위로 같다 — next_state 설명 참고.
"""
import enum

from recovery import PHASE_COMMITTED, PHASE_RECOVER


class State(enum.Enum):
    INIT = 'INIT'
    ACTIVE = 'ACTIVE'
    HOLDING = 'HOLDING'
    DEGRADED = 'DEGRADED'
    STOPPED = 'STOPPED'
    ESTOP = 'ESTOP'


DEFAULT_WATCHDOG_T_S = 0.050       # §3-6 제안값 "50 ms" — 팀 확정 필요(설계 §5.D 에 명시)
DEFAULT_STOPPED_AFTER_S = 2.0      # HOLDING 이 이만큼 지속되면 STOPPED 로 — 잠정값, 팀 확인 필요
DEFAULT_PERCEPTION_TIMEOUT_S = 0.5  # 인지 하트비트 타임아웃 — 잠정값, 팀 확인 필요

# --- 2026-09-21 초안: DEGRADED 지속 상한 + 실측 차속 기반 STOPPED 판정 (팀 확인 전 잠정값) ---
DEFAULT_PERCEPTION_LOST_STOPPED_AFTER_S = 2.0   # DEGRADED 이만큼 지속되면 STOPPED — HOLDING 과 별도 조정 가능
DEFAULT_STOP_SPEED_KPH = 3.0                    # 실측 차속이 이 이하면 "사실상 정지"로 간주 — 잠정값


def next_state(current, cmd_age_s, holding_duration_s, perception_age_s,
               watchdog_t_s=DEFAULT_WATCHDOG_T_S,
               stopped_after_s=DEFAULT_STOPPED_AFTER_S,
               perception_timeout_s=DEFAULT_PERCEPTION_TIMEOUT_S,
               degraded_duration_s=None,
               perception_lost_stopped_after_s=DEFAULT_PERCEPTION_LOST_STOPPED_AFTER_S,
               vehicle_speed_kph=None,
               stop_speed_kph=DEFAULT_STOP_SPEED_KPH,
               estop=False, estop_reset=False,
               pre_go=False, recovery_phase=None, committed_duration_s=None, estop_go_reset=False):
    """현재 상태 + 관측값 → 다음 상태. cmd_age_s/perception_age_s 는 None 이면 "한 번도 못 받음"(무한 나이 취급).
    degraded_duration_s/vehicle_speed_kph 도 마찬가지로 None 이면 "모름" — 새 조건 없이 기존 동작 그대로다
    (호출자가 안 넘기면 완전히 이전 버전과 동일하게 동작, 하위 호환).

    **DEGRADED 정책 초안(2026-09-21, §7.2 "GPU-crash rule: decel per team policy" 구체화)**: 인지가 끊긴
    채로 명령만 계속 오는 상태를 무한정 신뢰하지 않는다 — HOLDING 과 같은 기준(`perception_lost_stopped_after_s`,
    기본 HOLDING 과 동일 2.0s)만큼 지속되면 STOPPED 로 넘어간다. cmd_stale 이 아니라서 HOLDING 경로를 타지
    않기 때문에 DEGRADED 자신의 지속시간을 별도로 봐야 한다(호출자가 HOLDING 과 같은 방식으로 진입 시각을
    잡아 넘겨준다, can_guard.py 참고).

    **STOPPED 실측 차속 판정 초안(2026-09-21)**: HOLDING/DEGRADED 어느 쪽이든, "지속시간이 상한을 넘었다"
    **또는** "실측 차속(0x711 VS)이 정지 문턱 이하다" 중 먼저 만족하는 쪽으로 STOPPED 진입 — 시간 상한은
    차속을 모를 때(RX 아직 없음/오래됨, vehicle_speed_kph=None)의 안전망으로 계속 남긴다. 차속을 알면 더
    빨리(차가 실제로 멈췄으면 2초씩 안 기다리고) 또는 더 정확하게 판단할 수 있다.
    ⚠️ vehicle_speed_kph 는 **ACTIVE 상태에서는 절대 참조하지 않는다** — 정상 주행 중 서행/정차(코너 진입,
    다른 차 대기 등)를 "정지"로 오판하면 안 되므로, 차속 판정은 이미 HOLDING/DEGRADED(둘 다 "명령을 완전히
    신뢰하지 못하는" 상태)로 들어온 뒤의 조기 종료 조건으로만 쓴다.

    DEGRADED 는 인지가 끊겼을 때만 들어가고, 인지가 돌아오면 나머지 조건(cmd_age 등)에 따라 정상 전이로
    복귀한다 — DEGRADED 자체는 "정지"가 아니라 "인지 없이 판단하지 마라"는 신호라서, 지속 상한 안에서는
    조향/가감속 판단이 ACTIVE/HOLDING 과 유사하게 흘러가되 실제 감속 폭은 command_policy.py 에서 정한다
    (초안: HOLDING 과 같은 메커니즘 — 조향 얼림 + 가감속 0 램프, `command_policy.py` 참고).

    **우선순위 설계 결정**: 명령이 stale 이면(cmd_stale) 인지 상태와 무관하게 HOLDING/STOPPED 계열로 간다 —
    조향 유지·감속 램프는 인지 입력이 필요 없는 동작이라, "명령 없음"이 "인지 없음"보다 항상 우선한다.
    즉 DEGRADED 는 "명령은 살아있는데 인지만 없는" 좁은 창에서만 나타난다.

    **비상 정지(2026-11-04)**: `estop`=지금 어떤 원천이든 정지 요청이 켜져 있음, `estop_reset`=이번 주기에 해제 요청이
    들어옴. ESTOP 은 어느 상태보다 우선하고 래치된다. 해제는 정지 요청이 모두 꺼졌고 **실측 차속이 정지 문턱 이하일
    때만** 받아 STOPPED(브레이크 유지)로 간다 — 차속을 모르면(None) 해제하지 않는다(can_guard 재시작으로만 빠져나감).

    **경기 모드(2026-11-06, `--race` 일 때만 can_guard 가 넘긴다. 안 넘기면 위 규칙 그대로):**
    - `pre_go`: 출발 대기 중. 차가 서 있어 vehicle_near_stop 이 항상 참이므로 차속·지속시간으로 STOPPED 로 가는 길을 **전부** 막는다
      (HOLDING·DEGRADED·ACTIVE, 브리프 A2, D11). 명령이 돌아오면 바로 ACTIVE. ESTOP 은 그대로 최우선, 이미 STOPPED 면 그대로.
    - `recovery_phase`(recovery.PHASE_*, None = 기존 규칙): 계획서 8.2. NORMAL·명령 신선 → 기존 정상 계열. RECOVER → HOLDING(복구 구간,
      신선한 입력 3주기 판정은 RecoveryTracker 가 하고 끝나면 NORMAL 을 넘긴다). COMMITTED → HOLDING 이다가 **차속 ≤ 정지 문턱 또는
      committed_duration_s ≥ stopped_after_s 일 때만** STOPPED — "≤ 3 km/h → STOPPED" 는 committed 뒤에만(출발 직후 저속 끊김에
      영구 정지되던 문제), `--stopped-after` 는 committed 이후 상한이다.
    - `estop_go_reset`: `resume_mode=go-reset` 에서 SUSPENDED → 새 Go 가 받아들여진 주기에만 참. 다른 정지 요청이 없고 차속이 정지 문턱
      이하면 ESTOP 을 풀고 명령 상태에 따라 ACTIVE·DEGRADED·HOLDING 으로 간다. 그 밖에는 ESTOP 유지(래치).
    """
    vehicle_near_stop = vehicle_speed_kph is not None and vehicle_speed_kph <= stop_speed_kph
    if current == State.ESTOP:
        if estop_reset and not estop and vehicle_near_stop:
            return State.STOPPED
        if not (estop_go_reset and not estop and vehicle_near_stop):
            return State.ESTOP
        current = State.HOLDING   # go-reset 재개: 아래 공통 규칙으로 간다(STOPPED 로는 가지 않음 — recovery_phase 규칙)
    if estop:
        return State.ESTOP

    perception_lost = perception_age_s is None or perception_age_s > perception_timeout_s
    cmd_stale = cmd_age_s is None or cmd_age_s > watchdog_t_s

    if current == State.INIT:
        if cmd_stale:
            return State.INIT   # 아직 첫 명령 안 옴 — 안전 상태 유지
        return State.DEGRADED if perception_lost else State.ACTIVE

    if current == State.STOPPED:
        return State.STOPPED   # 완전히 멈춘 뒤에는 재시작(INIT)으로만 빠져나간다 — 자동 복귀 없음(안전 우선)

    if pre_go:   # 경기 모드 출발 대기: 차속·지속시간으로 STOPPED 금지(A2)
        if cmd_stale:
            return State.HOLDING
        return State.DEGRADED if perception_lost else State.ACTIVE

    if recovery_phase is not None:   # 경기 모드 끊김 복구(계획서 8.2)
        if recovery_phase == PHASE_COMMITTED:
            if vehicle_near_stop or (committed_duration_s is not None and committed_duration_s >= stopped_after_s):
                return State.STOPPED
            return State.HOLDING
        if recovery_phase == PHASE_RECOVER or cmd_stale:
            return State.HOLDING

    # ACTIVE / HOLDING / DEGRADED 공통: 명령이 살아있으면 정상 계열, 아니면 HOLDING 계열로
    if not cmd_stale:
        if not perception_lost:
            return State.ACTIVE
        if degraded_duration_s is not None:
            if degraded_duration_s >= perception_lost_stopped_after_s or vehicle_near_stop:
                return State.STOPPED
        return State.DEGRADED

    if holding_duration_s is not None and (holding_duration_s >= stopped_after_s or vehicle_near_stop):
        return State.STOPPED
    return State.HOLDING
