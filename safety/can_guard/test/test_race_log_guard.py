"""can_guard 는 대회 신호를 이벤트 로그에만 남긴다 — 0x210 은 신호가 있든 없든 같아야 한다(지시서 WP1 관문)."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, '..', 'sil_tests', 'race_log_check.py')


def run(mode, tmp_path):
    ev = tmp_path / f'{mode}.csv'
    out = subprocess.run([sys.executable, TOOL, mode, str(ev)], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1]), [ln.split(',', 3)[2:] for ln in ev.read_text().splitlines()[1:]]


def test_race_frames_change_nothing_on_0x210_and_are_logged(tmp_path):
    with_sig, ev_with = run('with', tmp_path)
    without, ev_without = run('without', tmp_path)
    assert with_sig['n'] > 50 and without['n'] > 50
    assert with_sig['frames'] == without['frames']              # 0x210 값 집합이 같다
    names = [e[0] for e in ev_with]
    for want in ('race_signal', 'res', 'res_alive_lost', 'speed_limit_info'):
        assert want in names, want
    details = [e[1] for e in ev_with if e[0] == 'res']
    assert any('start' in d for d in details) and any('end' in d for d in details)
    assert not {'race_signal', 'res', 'res_alive_lost', 'speed_limit_info'} & {e[0] for e in ev_without}
    # 상태 전이는 대회 신호로 생기지 않는다
    def states(ev):
        return [e[1].split()[0] for e in ev if e[0] == 'state']
    assert states(ev_with) == states(ev_without)
