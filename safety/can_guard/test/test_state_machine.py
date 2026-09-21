"""상태머신 전이표 검증 — 순수 함수라 시간·IPC 없이 모든 경로를 표로 확인한다."""
from state_machine import State, next_state


def test_init_stays_init_without_command():
    assert next_state(State.INIT, cmd_age_s=None, holding_duration_s=None,
                      perception_age_s=0.0) == State.INIT


def test_init_to_active_on_first_command():
    assert next_state(State.INIT, cmd_age_s=0.001, holding_duration_s=None,
                      perception_age_s=0.001) == State.ACTIVE


def test_init_to_degraded_if_perception_missing_from_start():
    assert next_state(State.INIT, cmd_age_s=0.001, holding_duration_s=None,
                      perception_age_s=None) == State.DEGRADED


def test_active_stays_active_while_fresh():
    assert next_state(State.ACTIVE, cmd_age_s=0.01, holding_duration_s=None,
                      perception_age_s=0.01) == State.ACTIVE


def test_active_to_holding_when_command_goes_stale():
    assert next_state(State.ACTIVE, cmd_age_s=0.2, holding_duration_s=0.0,
                      perception_age_s=0.01, watchdog_t_s=0.05) == State.HOLDING


def test_active_to_degraded_when_perception_lost_but_command_fresh():
    assert next_state(State.ACTIVE, cmd_age_s=0.01, holding_duration_s=None,
                      perception_age_s=1.0, perception_timeout_s=0.5) == State.DEGRADED


def test_holding_returns_to_active_when_command_resumes():
    assert next_state(State.HOLDING, cmd_age_s=0.001, holding_duration_s=0.3,
                      perception_age_s=0.01) == State.ACTIVE


def test_holding_stays_holding_before_timeout():
    assert next_state(State.HOLDING, cmd_age_s=0.5, holding_duration_s=1.0,
                      perception_age_s=0.01, stopped_after_s=2.0) == State.HOLDING


def test_holding_to_stopped_after_timeout():
    assert next_state(State.HOLDING, cmd_age_s=0.5, holding_duration_s=2.5,
                      perception_age_s=0.01, stopped_after_s=2.0) == State.STOPPED


def test_holding_to_stopped_ignores_perception():
    """명령이 죽어 있으면 정지 시퀀스가 우선이다 — 인지가 죽었는지는 이 판단에 영향 없음(설계 결정, docstring 참고)."""
    assert next_state(State.HOLDING, cmd_age_s=0.5, holding_duration_s=2.5,
                      perception_age_s=None, stopped_after_s=2.0) == State.STOPPED


def test_degraded_to_active_when_perception_recovers():
    assert next_state(State.DEGRADED, cmd_age_s=0.01, holding_duration_s=None,
                      perception_age_s=0.01) == State.ACTIVE


def test_degraded_to_holding_when_command_also_goes_stale():
    assert next_state(State.DEGRADED, cmd_age_s=0.5, holding_duration_s=0.0,
                      perception_age_s=1.0, watchdog_t_s=0.05,
                      perception_timeout_s=0.5) == State.HOLDING


def test_stopped_is_terminal():
    """STOPPED 는 재시작(INIT) 으로만 빠져나간다 — 명령이 살아 있어도 자동 복귀하지 않는다(안전 우선)."""
    assert next_state(State.STOPPED, cmd_age_s=0.001, holding_duration_s=None,
                      perception_age_s=0.001) == State.STOPPED


# --- 2026-09-21 초안: DEGRADED 지속 상한 + 실측 차속 기반 STOPPED ---

def test_degraded_stays_degraded_before_its_own_timeout():
    assert next_state(State.DEGRADED, cmd_age_s=0.01, holding_duration_s=0.0,
                      perception_age_s=1.0, perception_timeout_s=0.5,
                      degraded_duration_s=1.0, perception_lost_stopped_after_s=2.0) == State.DEGRADED


def test_degraded_to_stopped_after_its_own_timeout():
    """cmd 는 계속 fresh 해도(HOLDING 경로를 안 탐) DEGRADED 가 오래 지속되면 스스로 STOPPED 로 간다."""
    assert next_state(State.DEGRADED, cmd_age_s=0.01, holding_duration_s=0.0,
                      perception_age_s=1.0, perception_timeout_s=0.5,
                      degraded_duration_s=2.5, perception_lost_stopped_after_s=2.0) == State.STOPPED


def test_degraded_timeout_independent_of_holding_timeout():
    """degraded_duration_s 가 없으면(호출자가 안 넘기면) 예전처럼 무한정 DEGRADED 유지 — 하위 호환."""
    assert next_state(State.DEGRADED, cmd_age_s=0.01, holding_duration_s=0.0,
                      perception_age_s=1.0, perception_timeout_s=0.5) == State.DEGRADED


def test_degraded_to_stopped_early_if_vehicle_already_near_stop():
    """지속시간 상한 전이라도 실측 차속이 정지 문턱 이하면 바로 STOPPED — 이미 멈췄는데 2초 더 기다릴 필요 없음."""
    assert next_state(State.DEGRADED, cmd_age_s=0.01, holding_duration_s=0.0,
                      perception_age_s=1.0, perception_timeout_s=0.5,
                      degraded_duration_s=0.1, perception_lost_stopped_after_s=2.0,
                      vehicle_speed_kph=1.0, stop_speed_kph=3.0) == State.STOPPED


def test_holding_to_stopped_early_if_vehicle_already_near_stop():
    assert next_state(State.HOLDING, cmd_age_s=0.5, holding_duration_s=0.1,
                      perception_age_s=0.01, stopped_after_s=2.0,
                      vehicle_speed_kph=2.0, stop_speed_kph=3.0) == State.STOPPED


def test_holding_stays_holding_if_vehicle_still_moving():
    assert next_state(State.HOLDING, cmd_age_s=0.5, holding_duration_s=0.1,
                      perception_age_s=0.01, stopped_after_s=2.0,
                      vehicle_speed_kph=40.0, stop_speed_kph=3.0) == State.HOLDING


def test_active_ignores_vehicle_speed():
    """정상 주행 중 서행/정차(코너·다른 차 대기 등)를 정지로 오판하면 안 된다 — ACTIVE 는 차속을 절대 안 본다."""
    assert next_state(State.ACTIVE, cmd_age_s=0.01, holding_duration_s=0.0,
                      perception_age_s=0.01, vehicle_speed_kph=0.0, stop_speed_kph=3.0) == State.ACTIVE
