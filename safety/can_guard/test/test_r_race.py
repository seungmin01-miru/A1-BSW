"""r_race.py 의 실행 없이 확인할 수 있는 부분: 채널 거부, 프로세스 모드 인자, S18 값 비교, 시나리오 표. 시나리오 실행 자체는 r_race.py 가 한다(수 분 걸림)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sil_tests'))
import r_race  # noqa: E402


@pytest.mark.parametrize('channel', ['eth9', 'pcan0', 'kvaser0'])
def test_socketcan_refuses_non_vcan(channel, capsys):
    assert r_race.main(['--interface', 'socketcan', '--channel', channel]) == 2
    assert '거부' in capsys.readouterr().err


def test_process_mode_arguments_pass_real_parsers(capsys):
    """대회 PC 에서 처음 돌 프로세스 모드: 모든 시나리오의 can_guard·차량 인자가 실제 파서를 통과해야 한다. 아무것도 실행하지 않는다."""
    for cid, case in r_race.all_cases(with_ros=True).items():
        assert r_race.print_commands(case, type('A', (), {'channel': 'vcan0'})) == 0, cid
    assert 'can_guard.py' in capsys.readouterr().out


def test_every_plan_scenario_is_listed():
    ids = set(r_race.all_cases(with_ros=True))
    for sid in ('S01', 'S02', 'S02b', 'S03', 'S03b', 'S04', 'S05', 'S05m', 'S05d', 'S06', 'S06r', 'S07', 'S08', 'S09', 'S09b', 'S10', 'S10b', 'S11a', 'S11b',
                'S12a', 'S12b', 'S12c', 'S13', 'S15', 'S15r', 'S16', 'S17a', 'S17b', 'S18', 'X01', 'W5r'):
        assert sid in ids, sid
    gaps = [i for i in ids if i.startswith('S14-ros-')]
    assert len(gaps) == 20     # 5 끊김 × 2 속도 × 2 R_min


def test_compare_scenarios_finds_a_changed_column():
    base = {'a': [(0.0, 1.0, 30.0, 0.0, 7), (0.1, 1.0, 31.0, 0.0, 7)]}
    assert r_race.compare_scenarios(base, {'a': list(base['a'])})[0][1]
    moved = {'a': [(0.0, 1.0, 30.0, 40.0, 7), (0.1, 1.0, 31.0, 0.0, 7)]}
    row = r_race.compare_scenarios(base, moved)[0]
    assert not row[1] and '가속' in row[2]
    assert not r_race.compare_scenarios(base, {})[0][1]


def test_brake_ramp_cut_short_is_not_a_difference():
    base = {'a': [(i * 0.02, 0.0, 30.0 + i, 0.0, 7) for i in range(50)]}
    shorter = {'a': base['a'][:48]}
    assert r_race.compare_scenarios(base, shorter)[0][1]
