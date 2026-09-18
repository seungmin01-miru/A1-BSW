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
