"""SlowController — 계획서 6.1 단위 시험 + 간단한 1차 차량 모델 폐루프.

폐루프 모델은 sil/vcan/fake_a1_vehicle.py --physics 와 같은 구조(구동·제동 1차 지연 0.15 s, 저항 0.15 + 0.01·v m/s²)다.
**물리 모델이 실차와 같다는 뜻이 아니다** — 제어 로직이 닫히고 10 km/h 를 넘지 않는지만 본다(실차 판정에 쓰지 않음).

가속 곡선 둘:
  ACC_1104 : 11-04 실차 저속 측정(measurements/2026-11-04_signal_audit/quick_lon.md)에 저항을 더한 ★추정. 70 % 까지 저항을 못 이김
  ACC_PHYS : fake_a1_vehicle --physics 그대로. track.yaml 표(최소 75 %)와 맞지 않아 2.4 m/s² 로 튄다 = 표보다 훨씬 센 차 스트레스
"""
import math

import pytest

from speed_ctrl import (MODE_ENTRY, MODE_NO_SPEED, MODE_OVERSPEED, MODE_PI, SlowController, SlowParams, _interp)

DT = 0.02
ACC_1104 = ((0, 70, 80, 88, 100), (0, 0.15, 0.30, 0.55, 1.05))
ACC_PHYS = ((0, 5, 30, 100), (0, 0, 1.3, 3.0))
BRAKE_PHYS = ((0, 40, 70, 82), (0, 1.0, 2.0, 2.8))
ACTUATOR_PCT_S = (40.0, 60.0)   # 11-04: 브레이크 위치 0→82 % 약 3 s(최대 구간 기준 넉넉히), 82→0 % 약 1.4 s


def simulate(v0_kph, acc_curve=ACC_1104, grade=0.0, duration_s=25.0, dead_s=0.0, brake_rate=None, params=None,
             none_window=None, wheel_frames=False):
    """(시각, 차속 km/h, 가속 %, 브레이크 %, 모드) 목록. wheel_frames: 차속을 0x201 처럼(21 ms 주기, 바퀴 0.1 km/h 단위)."""
    ctl = SlowController(params or SlowParams())
    v = v0_kph / 3.6
    a_drive = a_brake = brake_pos = 0.0
    queue = [(0.0, 0.0)] * int(round(dead_s / DT))
    out = []
    held, next_frame = v * 3.6, 0.0
    for k in range(int(duration_s / DT)):
        t = k * DT
        meas = v * 3.6
        if wheel_frames:
            if t >= next_frame:
                held, next_frame = round(meas * 10.0) / 10.0, next_frame + 0.021
            meas = held
        if none_window and none_window[0] <= t < none_window[1]:
            meas = None
        acc, brake = ctl.step(meas, DT)
        out.append((t, v * 3.6, acc, brake, ctl.mode))
        queue.append((acc, brake))
        acc_now, brake_now = queue.pop(0)
        if brake_rate:
            brake_pos += max(-brake_rate[1] * DT, min(brake_rate[0] * DT, brake_now - brake_pos))
        else:
            brake_pos = brake_now
        k1 = min(1.0, DT / 0.15)
        a_drive += (_interp(acc_now, *acc_curve) - a_drive) * k1
        a_brake += (_interp(min(brake_pos, 82.0), *BRAKE_PHYS) - a_brake) * k1
        resist = (0.15 + 0.01 * v) if v > 0 else 0.0
        v = max(0.0, v + (a_drive - a_brake - resist + grade) * DT)
    return out


def after_entry(log, kph=8.5):
    """처음 kph 이하가 된 뒤의 기록(30→8 진입 감속 구간 제외)."""
    for i, row in enumerate(log):
        if row[1] <= kph:
            return log[i:]
    raise AssertionError(f'{kph} km/h 아래로 못 내려옴')


def n_over(rows, kph=10.0):
    return sum(1 for r in rows if r[1] > kph)


def assert_exclusive(log):
    assert all(not (r[2] > 0 and r[3] > 0) for r in log), '가속·브레이크 동시 양수'


# --- 폐루프: 30 → 8 km/h, 10 km/h 초과 0회 ------------------------------------------------------------------------
def test_closed_loop_30_to_8_measured_curve():
    log = simulate(30.0)
    rows = after_entry(log)
    assert n_over(rows) == 0
    assert_exclusive(log)
    assert rows[0][0] < 4.0     # 6.2절 ★ 약 2.9 s + 시작 램프
    tail = [r[1] for r in log if r[0] > 15.0]
    assert 7.5 < sum(tail) / len(tail) < 8.5
    assert max(tail) < 8.8 and min(tail) > 7.0


def test_closed_loop_with_real_wheel_frame_quantization():
    """0x201 의 21 ms·0.1 km/h 계단이 가속도 추정(앞당김)을 망가뜨려 정속이 무너지지 않는다."""
    log = simulate(30.0, wheel_frames=True, dead_s=0.2, brake_rate=ACTUATOR_PCT_S)
    assert n_over(after_entry(log)) == 0
    tail = [r[1] for r in log if r[0] > 15.0]
    assert 7.5 < sum(tail) / len(tail) < 8.5 and max(tail) < 9.0


def test_closed_loop_with_physics_plant_and_track_table():
    """fake_a1_vehicle --physics 곡선에 track.yaml 표를 쓰면 차가 표보다 훨씬 세다(75 % → 2.4 m/s²). 그래도 넘지 않아야 한다."""
    log = simulate(30.0, acc_curve=ACC_PHYS)
    assert n_over(after_entry(log)) == 0
    assert_exclusive(log)


def test_closed_loop_physics_plant_with_matching_table_settles():
    params = SlowParams(accel_map_mps2=(0.1, 1.3), accel_map_pct=(5.0, 30.0))
    log = simulate(30.0, acc_curve=ACC_PHYS, params=params)
    tail = [r[1] for r in log if r[0] > 15.0]
    assert max(tail) - min(tail) < 0.2 and abs(sum(tail) / len(tail) - 8.0) < 0.1


@pytest.mark.parametrize('v0', [15.0, 30.0, 50.0])
def test_closed_loop_various_entry_speeds_with_delay(v0):
    """순수 지연 0.3 s + 브레이크 액추에이터 속도 제한."""
    log = simulate(v0, dead_s=0.3, brake_rate=ACTUATOR_PCT_S, duration_s=30.0)
    assert n_over(after_entry(log)) == 0
    assert_exclusive(log)


def test_closed_loop_from_standstill_with_measured_start_delay():
    """11-04 출발 지연 0.7 s 를 순수 지연으로 넣어도 불감대를 넘어 출발하고 10 km/h 를 넘지 않는다."""
    log = simulate(0.0, dead_s=0.7, brake_rate=ACTUATOR_PCT_S)
    assert n_over(log) == 0
    tail = [r[1] for r in log if r[0] > 15.0]
    assert 7.0 < sum(tail) / len(tail) < 9.0


def test_lookahead_is_what_keeps_the_delayed_start_legal():
    """앞당김을 끄면 같은 조건에서 10 km/h 를 넘는다 — acc_cut_lookahead_s 가 왜 있는지 고정."""
    log = simulate(0.0, dead_s=0.7, brake_rate=ACTUATOR_PCT_S, params=SlowParams(acc_cut_lookahead_s=0.0))
    assert n_over(log) > 0


def test_closed_loop_downhill_3pct():
    """내리막 약 3 %(0.3 m/s²). 가속 없이도 빨라지는 경우 — 9.3 브레이크가 잡아야 한다."""
    log = simulate(30.0, grade=0.3, dead_s=0.2, brake_rate=ACTUATOR_PCT_S)
    assert n_over(after_entry(log)) == 0


def test_sensor_dropout_during_slow_stays_legal():
    log = simulate(30.0, none_window=(10.0, 10.5))
    rows = after_entry(log)
    assert n_over(rows) == 0
    assert all(r[2] == 0.0 and r[3] >= 30.0 for r in log if 10.0 <= r[0] < 10.5)


# --- 6.1 단계별 동작 ---------------------------------------------------------------------------------------------
def test_entry_brake_ramps_from_start_to_table_value():
    """a_slow 2.0 → 감속표 70 %. 30 % 에서 시작해 40 %/s 로 올리고 가속은 0."""
    c = SlowController()
    acc, br = c.step(30.0, DT)
    assert (acc, br) == (0.0, 30.0) and c.mode == MODE_ENTRY
    acc, br = c.step(30.0, DT)
    assert acc == 0.0 and br == pytest.approx(30.8)
    for _ in range(200):
        acc, br = c.step(30.0, DT)
    assert br == pytest.approx(70.0)


def test_entry_brake_uses_decel_table():
    c = SlowController(SlowParams(a_slow_mps2=3.0))     # 표 90 % → 출력 상한 82 %
    for _ in range(300):
        acc, br = c.step(40.0, DT)
    assert br == pytest.approx(82.0)


def test_entry_ends_at_target_plus_half_not_before():
    c = SlowController()
    c.step(30.0, DT)
    _, br = c.step(8.6, DT)
    assert br > 0 and c.mode == MODE_ENTRY
    acc, br = c.step(8.5, DT)
    assert c.mode == MODE_PI and br == 0.0


def test_no_entry_when_starting_at_or_below_brake_threshold():
    c = SlowController()
    c.step(9.2, DT)
    assert c.mode == MODE_PI


def test_acc_cut_at_8_8_even_if_pi_wants_more():
    """적분을 상한까지 쌓아 둔 뒤에도 8.8 이상이면 가속 0."""
    c = SlowController()
    for _ in range(3000):
        c.step(6.0, DT)
    assert c.integ_mps2 > 0
    acc, _ = c.step(8.79, DT)
    acc, _ = c.step(8.8, DT)
    assert acc == 0.0


def test_lookahead_cuts_before_8_8_when_accelerating():
    c = SlowController()
    c.step(6.0, DT)
    acc = None
    v = 6.0
    while v < 8.0:      # 1 m/s² 로 가속 중이면 8.8 − 0.7 s × 3.6 km/h/s 근처에서 이미 꺼짐
        v += 1.0 * 3.6 * DT
        acc, _ = c.step(v, DT)
    assert acc == 0.0


def test_overspeed_brake_ramp_and_hysteresis():
    c = SlowController()
    c.step(8.0, DT)
    acc, br = c.step(9.29, DT)
    assert br == 0.0
    acc, br = c.step(9.3, DT)
    assert (acc, br) == (0.0, 30.0) and c.mode == MODE_OVERSPEED
    for _ in range(50):         # 1 s → 20 %/s
        acc, br = c.step(9.5, DT)
    assert br == pytest.approx(50.0)
    for _ in range(100):
        acc, br = c.step(9.5, DT)
    assert br == pytest.approx(60.0)    # 상한
    acc, br = c.step(9.0, DT)           # 9.3 아래여도 8.8 아래가 아니면 유지(히스테리시스)
    assert br >= 60.0 and acc == 0.0 and c.mode == MODE_OVERSPEED
    acc, br = c.step(8.8, DT)
    assert br >= 60.0
    acc, br = c.step(8.79, DT)
    assert br == 0.0 and c.mode == MODE_PI
    acc, br = c.step(9.3, DT)           # 다시 넘으면 시작값부터
    assert br == 30.0


def test_never_acc_and_brake_together_across_speed_sweep():
    c = SlowController()
    for k in range(5000):
        v = 6.0 + 4.5 * math.sin(k * 0.01)
        acc, br = c.step(v if k % 97 else None, DT)
        assert not (acc > 0 and br > 0)
        assert 0 <= acc <= 100 and 0 <= br <= 82


# --- 차속 없음 (A4) ------------------------------------------------------------------------------------------------
def test_none_speed_from_fresh_gives_zero_acc_and_entry_start():
    c = SlowController()
    assert c.step(None, DT) == (0.0, 30.0)
    assert c.mode == MODE_NO_SPEED


def test_nan_speed_treated_as_none():
    c = SlowController()
    assert c.step(float('nan'), DT) == (0.0, 30.0)


def test_none_speed_during_pi_applies_entry_start_and_resumes():
    c = SlowController()
    for _ in range(50):
        c.step(7.0, DT)
    assert c.step(None, DT) == (0.0, 30.0)
    acc, br = c.step(7.0, DT)
    assert c.mode == MODE_PI and br == 0.0


def test_none_speed_does_not_weaken_entry_brake():
    c = SlowController()
    for _ in range(100):
        c.step(30.0, DT)
    _, before = c.step(30.0, DT)
    acc, br = c.step(None, DT)
    assert acc == 0.0 and br == pytest.approx(before)


def test_none_speed_freezes_integrator():
    c = SlowController()
    for _ in range(100):
        c.step(7.0, DT)
    i0 = c.integ_mps2
    for _ in range(100):
        c.step(None, DT)
    assert c.integ_mps2 == i0


# --- anti-windup ·상한 --------------------------------------------------------------------------------------------
def test_integrator_bounded_when_stuck_below_target():
    c = SlowController()
    for _ in range(100000):
        acc, br = c.step(7.5, DT)      # 오르막으로 안 올라가는 경우
    assert abs(c.integ_mps2) <= c.p.i_max_mps2 + 1e-9
    assert acc <= 100.0


def test_integrator_not_wound_while_output_saturated():
    """정지 상태처럼 오차가 커서 PI 가 a_max 에 포화해 있으면 적분이 쌓이지 않는다."""
    c = SlowController()
    for _ in range(5000):
        acc, _ = c.step(0.0, DT)
    assert c.integ_mps2 == 0.0
    assert acc == pytest.approx(100.0)


def test_integrator_not_wound_during_entry_or_overspeed():
    c = SlowController()
    for _ in range(500):
        c.step(30.0, DT)
    assert c.integ_mps2 == 0.0
    c.step(8.0, DT)
    i0 = c.integ_mps2
    for _ in range(500):
        c.step(9.5, DT)
    assert c.integ_mps2 == i0


def test_standstill_output_clears_start_deadband():
    """a_max 0.8 m/s² → 표 끝 100 %. 11-04 는 70 % 까지 출발 못 함, 80 % 부터 출발."""
    c = SlowController()
    acc, br = c.step(0.0, DT)
    assert acc >= 80.0 and br == 0.0


def test_small_positive_demand_is_table_floor_not_below_deadband():
    """가속을 켜면 표의 아래 끝(75 %) 밑으로는 안 나간다 — 그 아래는 차가 안 움직이는 구간."""
    c = SlowController()
    acc, br = c.step(8.0, DT)       # e=0 → a=ff 0.2
    assert 80.0 < acc < 90.0 and br == 0.0
    c2 = SlowController()
    acc, _ = c2.step(8.3, DT)       # a ≈ 0.133
    assert acc >= 75.0


def test_acc_hysteresis_prevents_chatter_at_threshold():
    """a 가 deadband·acc_off 사이면 직전 상태를 유지 — 50 Hz 0↔75 % 떨림 방지."""
    params = SlowParams(acc_cut_lookahead_s=0.0)    # 이 시험의 속도 계단이 가속도 추정에 잡히지 않게
    c = SlowController(params)
    # a = 0.2 + 0.8·(8−v)/3.6 + i, 초기 적분 0 부근. v=8.5 → a≈0.089 (0.05~0.1 사이)
    acc_off_first, _ = c.step(8.5, DT)
    assert acc_off_first == 0.0
    c2 = SlowController(params)
    on, _ = c2.step(8.0, DT)
    assert on > 0
    still_on, _ = c2.step(8.5, DT)
    assert still_on > 0


def test_negative_demand_uses_brake_table():
    c = SlowController(SlowParams(ff_mps2=0.0))
    c.step(8.0, DT)
    acc, br = c.step(9.2, DT)       # a = 0.8·(−1.2/3.6) ≈ −0.27 → 표 (0.1→0, 1.0→40) ≈ 7.5 %
    assert acc == 0.0 and 5.0 < br < 10.0


def test_reset_clears_state():
    c = SlowController()
    for _ in range(100):
        c.step(7.0, DT)
    c.reset()
    assert c.integ_mps2 == 0.0
    c.step(30.0, DT)
    assert c.mode == MODE_ENTRY


def test_deterministic():
    a, b = simulate(30.0), simulate(30.0)
    assert a == b


# --- 파라미터 -----------------------------------------------------------------------------------------------------
YAML_DICT = {
    'a_slow': 2.5,
    'slow_off_ramp_pct_s': 50,
    'accel_map': {'accel_mps2': [0.1, 0.13, 0.57], 'acc_pct': [75.0, 80.0, 100.0]},
    'brake_map': {'decel_mps2': [0.1, 1.0, 2.0, 3.0, 4.0], 'brake_pct': [0.0, 40.0, 70.0, 90.0, 100.0]},
    'slow_pi': {'kp': 0.7, 'ki': 0.1, 'ff': 0.25},
}


def test_from_yaml_dict_and_cli_overrides():
    p = SlowParams.from_yaml_dict(YAML_DICT, slow_target_kph=8.5, slow_acc_cut_kph=9.2, slow_brake_kph=9.5)
    assert p.a_slow_mps2 == 2.5 and p.kp == 0.7 and p.ki == 0.1 and p.ff_mps2 == 0.25
    assert p.accel_map_pct == (75.0, 80.0, 100.0) and p.decel_map_mps2[0] == 0.1
    assert p.slow_target_kph == 8.5
    assert p.i_max_mps2 == SlowParams().i_max_mps2  # slow_pi 에 없으면 기본


@pytest.mark.parametrize('missing', ['a_slow', 'accel_map', 'brake_map'])
def test_from_yaml_dict_requires_learned_values(missing):
    d = {k: v for k, v in YAML_DICT.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        SlowParams.from_yaml_dict(d)


def test_from_yaml_dict_rejects_unknown_pi_key():
    d = dict(YAML_DICT, slow_pi={'kpp': 1.0})
    with pytest.raises(ValueError, match='kpp'):
        SlowParams.from_yaml_dict(d)


@pytest.mark.parametrize('kw', [
    {'slow_target_kph': 9.0, 'slow_acc_cut_kph': 8.8},             # 순서 틀림
    {'slow_brake_kph': 10.5},                                      # 10 km/h 초과 금지
    {'accel_map_mps2': (0.2, 0.1)},                                # 내림차순
    {'accel_map_pct': (75.0, 80.0, 120.0)},                        # % 범위
    {'decel_map_pct': (0.0, 40.0)},                                # 길이
    {'a_slow_mps2': 0.0},
    {'acc_off_mps2': 0.2},                                         # deadband 보다 큼
    {'accel_map_mps2': (0.1, float('nan'), 0.57)},
])
def test_bad_params_rejected(kw):
    with pytest.raises(ValueError):
        SlowParams(**kw)


@pytest.mark.parametrize('stage', [(8.0, 8.8, 9.3), (8.5, 9.2, 9.5), (9.0, 9.4, 9.6)])
def test_speed_stages_are_valid_params(stage):
    """6.3절 단계표가 전부 파라미터 검사를 통과."""
    SlowParams(slow_target_kph=stage[0], slow_acc_cut_kph=stage[1], slow_brake_kph=stage[2])
