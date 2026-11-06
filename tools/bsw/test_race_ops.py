"""경기 운영 도구 시험 — 옵션 묶음(bsw.env + race_args.sh)·시작 점검 표(race_status_cli.py)·문서 대조. CAN·차량에 닿지 않는다.

  python3 -m pytest -q tools/bsw/test_race_ops.py

bsw_up·bringup·bsw_status·bsw_down 자체는 여기서 돌리지 않는다(차량에 닿는 명령). 대회 PC 에서 --sil --race 로 확인한다(README).
"""
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid

import can
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, '..', '..')
GUARD_DIR = os.path.join(REPO, 'safety', 'can_guard')
sys.path.insert(0, HERE)
sys.path.insert(0, GUARD_DIR)
import can_guard  # noqa: E402
import protocol  # noqa: E402
import race_status_cli as cli  # noqa: E402

PHASE = protocol.RACE_PHASE_CODE
GUARD = protocol.GUARD_STATE_CODE
TRACK_YAML = os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')

# 계획서 10절 표 그대로(옵션의 dest → 값). 표가 바뀌면 여기와 bsw.env 를 함께 고친다.
PLAN_TABLE = {
    'race': True, 'race_confirm_frames': 2, 'race_stop_action': 'estop', 'race_signal_timeout_s': 0.0, 'resume_mode': 'restart',
    'res_alive_timeout_s': 0.5, 'pre_go_brake_pct': 30.0, 'slow_target_kph': 8.0, 'slow_acc_cut_kph': 8.8, 'slow_brake_kph': 9.3,
    'finish_cap_kph': 20.0, 'parked_speed_kph': 0.5, 'parked_after_s': 5.0, 'parked_brake_pct': 60.0, 'input_grace_s': 1.0,
    'recover_budget_m': 0.30, 'recover_delay_s': 0.25, 'recover_alat_max': 3.0, 'track_min_radius_m': None,
    'resume_low_speed': False, 'speed_limit_tx': False, 'speed_limit_release_kph': None, 'speed_limit_practice_kph': 30.0,
    'speed_limit_fast_period': 0.02, 'speed_limit_keep_period': 0.1, 'speed_limit_confirm_frames': 3,
    'speed_limit_mismatch_s': 0.3, 'speed_limit_release_note_s': 1.0, 'speed_limit_startup_timeout': 2.0,
}


def bash(script, env=None):
    src = f'set -u; source {shlex.quote(os.path.join(HERE, "bsw.env"))}; source {shlex.quote(os.path.join(HERE, "race_args.sh"))}; '
    return subprocess.run(['bash', '-c', src + script], capture_output=True, text=True, env={**os.environ, **(env or {})})


def bundle(overrides=''):
    r = bash(overrides + ' bsw_race_guard_args; printf "%s\\n" "${RACE_GUARD_ARGS[@]}"')
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


# ── 옵션 묶음 ─────────────────────────────────────────────────
def test_bundle_matches_plan_table():
    a = can_guard.parse_args(bundle())
    for dest, want in PLAN_TABLE.items():
        assert getattr(a, dest) == want, dest


def test_bundle_values_equal_can_guard_defaults():
    """묶음을 넘겨도 can_guard 기본값과 같다 = `--race` 하나만 준 것과 차이 없음(기록용으로 적어 넘길 뿐)."""
    with_bundle = vars(can_guard.parse_args(bundle()))
    only_race = vars(can_guard.parse_args(['--race']))
    assert with_bundle == only_race


def test_bundle_has_no_spaces_in_values():
    """bringup --guard-args 가 공백으로 나눈다."""
    r = bash('bsw_race_guard_args; for x in "${RACE_GUARD_ARGS[@]}"; do [[ "$x" == *" "* ]] && echo "$x"; done; true')
    assert r.returncode == 0 and r.stdout == ''


def test_tx_stays_off_and_needs_release_value():
    assert '--speed-limit-tx' not in bundle()
    with pytest.raises(SystemExit):
        can_guard.parse_args(bundle('BSW_RACE_SPEED_LIMIT_TX=1;'))
    a = can_guard.parse_args(bundle('BSW_RACE_SPEED_LIMIT_TX=1; BSW_RACE_SPEED_LIMIT_RELEASE_KPH=77;'))
    assert a.speed_limit_tx and a.speed_limit_release_kph == 77.0


def test_optional_values_pass_through():
    a = can_guard.parse_args(bundle('BSW_RACE_TRACK_MIN_RADIUS_M=20; BSW_RACE_RESUME_LOW_SPEED=1;'))
    assert a.track_min_radius_m == 20.0 and a.resume_low_speed


def test_race_opt_after_bundle_wins():
    """bsw_up 은 --race-opt 를 묶음 뒤에 붙인다. bringup 의 --steer-limit-deg 150 앞에 오는 값도 같은 원리로 진다."""
    a = can_guard.parse_args(['--steer-limit-deg', '150'] + bundle() + ['--pre-go-brake-pct', '20', '--steer-limit-deg', '15'])
    assert a.pre_go_brake_pct == 20.0 and a.steer_limit_deg == 15.0


def test_hold_after_matches_track_yaml():
    r = bash(f'bsw_race_hold_after {shlex.quote(TRACK_YAML)}')
    assert float(r.stdout.strip()) == 0.10
    assert bash(f'bsw_race_check_hold {shlex.quote(TRACK_YAML)} "$BSW_RACE_ADAPTER_HOLD_AFTER_S"').returncode == 0


def test_hold_after_mismatch_refused(tmp_path):
    assert '다름' in bash(f'bsw_race_check_hold {shlex.quote(TRACK_YAML)} 0.2').stdout
    assert bash(f'bsw_race_check_hold {shlex.quote(TRACK_YAML)} 0.2').returncode == 1
    y = tmp_path / 'x.yaml'
    y.write_text('a1:\n  stale_timeout_s: 0.15\n')
    r = bash(f'bsw_race_check_hold {shlex.quote(str(y))} 0.10')
    assert r.returncode == 1 and '없음' in r.stdout


def test_hold_after_numeric_compare(tmp_path):
    y = tmp_path / 'x.yaml'
    y.write_text('    hold_after_s: 0.1   # 주석\n')
    assert bash(f'bsw_race_check_hold {shlex.quote(str(y))} 0.10').returncode == 0


# ── 시작 점검 표 ───────────────────────────────────────────────
def ready(**kw):
    d = dict(race_enabled=True, guard_state=GUARD['ACTIVE'], race_phase=PHASE['PRE_GO'], model_healthy_s=12.0,
             vehicle_speed_limit_kph=30.0, race_signal_age_s=0.02, res_alive_ok=True, age_s=0.02)
    d.update(kw)
    return protocol.GuardStatus(**d)


def text(rows):
    return '\n'.join(t for _, t in rows)


def test_ready():
    rows, code = cli.evaluate(ready())
    assert code == cli.EXIT_READY and all(level == cli.OK for level, _ in rows)
    assert '원격 조종 제한값' in text(rows) and '정상' in text(rows)


def test_no_status():
    assert cli.evaluate(None)[1] == cli.EXIT_NO_STATUS
    assert cli.evaluate(protocol.GuardStatus(race_enabled=False))[1] == cli.EXIT_NO_STATUS


@pytest.mark.parametrize('kw, word', [
    (dict(model_healthy_s=9.9), '모델 정상 9.9'),
    (dict(guard_state=GUARD['INIT']), 'INIT'),
    (dict(guard_state=GUARD['HOLDING']), 'HOLDING'),
    (dict(guard_state=GUARD['STOPPED']), 'STOPPED'),
    (dict(guard_state=GUARD['ESTOP']), 'ESTOP'),
    (dict(vehicle_speed_limit_kph=-1.0), '0x202 수신 없음'),
    (dict(vehicle_speed_limit_kph=9.3), '잔류'),
    (dict(res_active=True), 'RES 작동 중'),
    (dict(sig_stop=True), 'Stop 켜짐'),
    (dict(age_s=2.0), '오래됨'),
])
def test_not_ready(kw, word):
    rows, code = cli.evaluate(ready(**kw))
    assert code == cli.EXIT_NOT_READY
    assert any(level == cli.BAD and word in t for level, t in rows), word


@pytest.mark.parametrize('kw, word', [
    (dict(race_signal_age_s=-1.0), '0x124 아직 수신 없음'),
    (dict(race_signal_age_s=5.0), '흐름이 멈춤'),
    (dict(sig_go=True), '옛 Go'),
    (dict(res_alive_ok=False), '0x127 아직 수신 없음'),
    (dict(vehicle_speed_limit_kph=0.0), '뜻 미확인'),
])
def test_warning_does_not_fail(kw, word):
    rows, code = cli.evaluate(ready(**kw))
    assert code == cli.EXIT_READY
    assert any(level == cli.WARN and word in t for level, t in rows), word


def test_threshold_is_inclusive_and_configurable():
    assert cli.evaluate(ready(vehicle_speed_limit_kph=9.4))[1] == cli.EXIT_READY
    assert cli.evaluate(ready(vehicle_speed_limit_kph=9.4), slow_brake_kph=9.5)[1] == cli.EXIT_NOT_READY
    assert cli.evaluate(ready(model_healthy_s=5.0), min_model_s=5.0)[1] == cli.EXIT_READY


def test_other_phases_are_display_only():
    rows, code = cli.evaluate(ready(race_phase=PHASE['RACING'], slow_active=True))
    assert code == cli.EXIT_NOT_PRE_GO and 'RACING + 서행' in text(rows)
    rows, code = cli.evaluate(ready(race_phase=PHASE['SUSPENDED'], guard_state=GUARD['ESTOP'], res_alive_ok=False))
    assert code == cli.EXIT_NOT_PRE_GO
    assert any(level == cli.BAD and 'SUSPENDED' in t for level, t in rows)
    assert any(level == cli.BAD and 'alive 끊김' in t for level, t in rows)


def test_format_and_brief():
    rows, code = cli.evaluate(ready())
    assert '✅ 출발 준비됨' in cli.format_table(rows, code)
    assert cli.brief(None) == 'StatusChannel 없음'
    assert 'PARKED' in cli.brief(ready(race_phase=PHASE['PARKED']))
    assert '수신 없음' in cli.brief(ready(vehicle_speed_limit_kph=-1.0))


@pytest.fixture
def shm():
    return f'a1_test_status_{uuid.uuid4().hex[:8]}'


def run_cli(*args):
    return subprocess.run([sys.executable, os.path.join(HERE, 'race_status_cli.py'), *args], capture_output=True, text=True)


def test_cli_reads_channel_and_never_writes(shm):
    w = protocol.StatusChannel.create(shm)
    try:
        w.write(protocol.GuardStatus(race_enabled=True, guard_state=GUARD['ACTIVE'], model_healthy_s=11.0,
                                     vehicle_speed_limit_kph=30.0, race_signal_age_s=0.02, res_alive_ok=True))
        seq = w._payload.seq
        r = run_cli('check', '--shm', shm)
        assert r.returncode == cli.EXIT_READY or r.returncode == cli.EXIT_NOT_READY, r.stderr
        assert 'PRE_GO' in r.stdout and '모델 정상 11.0' in r.stdout and '30.0' in r.stdout
        assert 'PRE_GO' in run_cli('brief', '--shm', shm).stdout
        assert w._payload.seq == seq
        again = protocol.StatusChannel.open_or_none(shm)              # 읽는 쪽이 세그먼트를 지우지 않았다
        assert again is not None
        again.close()
    finally:
        w.close()


def test_cli_ready_with_fresh_stamp(shm):
    w = protocol.StatusChannel.create(shm)
    try:
        w.write(protocol.GuardStatus(race_enabled=True, guard_state=GUARD['ACTIVE'], model_healthy_s=11.0,
                                     vehicle_speed_limit_kph=30.0, race_signal_age_s=0.02, res_alive_ok=True))
        st = cli.read_status(shm)
        st.age_s = 0.0      # 프로세스 띄우는 시간과 무관하게 내용만 본다
        assert cli.evaluate(st)[1] == cli.EXIT_READY
    finally:
        w.close()


def test_cli_without_channel(shm):
    r = run_cli('check', '--shm', shm)
    assert r.returncode == cli.EXIT_NO_STATUS and 'StatusChannel 없음' in r.stdout
    r = run_cli('brief', '--shm', shm)
    assert r.returncode == 0 and 'StatusChannel 없음' in r.stdout


# ── 이벤트 요약 ────────────────────────────────────────────────
EVENTS = '''wall_s,mono_s,event,detail
1700000000.000000,10.0,start,"kvaser:0 init=silent estop_can=remote"
1700000001.250000,11.25,speed_limit_startup,"observed=30.0 expected=None tx=off race=True action=pass"
1700000005.000000,15.0,pre_go_motion,"3.4 km/h"
1700000006.000000,16.0,race_signal,"go detect_lag_ms=21.0"
1700000006.000000,16.0,race_phase,"PRE_GO->RACING go"
1700000007.000000,17.0,input_clamped,"none->accel"
1700000009.000000,19.0,slow_on,"30.1 km/h"
1700000012.000000,22.0,commit,"budget gap=0.210s"
'''


def test_summary_counts_and_timeline(tmp_path):
    p = tmp_path / 'can_guard_events.csv'
    p.write_text(EVENTS)
    s = '\n'.join(cli.summarize_events(str(p)))
    assert '이벤트 8 줄' in s and 'race_phase 1' in s
    assert '⚠️ Go 전 움직임 1 회' in s
    assert 'race_phase PRE_GO->RACING go' in s and 'detect_lag_ms=21.0' in s and 'commit budget gap=0.210s' in s
    assert 'input_clamped none' not in s    # 잦은 이벤트는 개수만
    assert 'start kvaser' not in s


def test_summary_missing_empty_and_cut(tmp_path):
    assert '없음' in cli.summarize_events(str(tmp_path / 'none.csv'))[0]
    p = tmp_path / 'e.csv'
    p.write_text('wall_s,mono_s,event,detail\n')
    assert '비어 있음' in cli.summarize_events(str(p))[0]
    body = ''.join(f'1700000000.0,{i},state,"x{i}"\n' for i in range(cli.MAX_TIMELINE + 5))
    p.write_text('wall_s,mono_s,event,detail\n' + body + 'bad,row\n')
    s = cli.summarize_events(str(p))
    assert any('5 줄 더' in x for x in s)


def test_cli_events_runs_without_guard(tmp_path):
    p = tmp_path / 'e.csv'
    p.write_text(EVENTS)
    r = run_cli('events', str(p))
    assert r.returncode == 0 and 'slow_on' in r.stdout


def test_real_event_names_exist_in_can_guard():
    """시간표 이름이 can_guard 코드·README 에 있는 이름인지(오타로 요약에서 빠지지 않게)."""
    readme = open(os.path.join(GUARD_DIR, 'README.md'), encoding='utf-8').read()
    src = ''.join(open(os.path.join(GUARD_DIR, f), encoding='utf-8').read() for f in ('can_guard.py', 'race_runtime.py', 'race_state.py'))
    for name in cli.TIMELINE_EVENTS - {'state', 'estop_request', 'foreign_0x210', 'race_phase', 'res'}:
        assert f'`{name}`' in readme, name
    for name in ('state', 'estop_request', 'foreign_0x210', 'race_phase', 'res'):
        assert f"'{name}'" in src, name


# ── 진짜 can_guard + 묶음(python-can virtual 버스, 차량 없음) ──────
class GuardRun:
    """bsw_up 이 넘기는 묶음 그대로 can_guard.run 을 돌린다. limit_kph 를 주면 0x202 를 흉내 낸다."""

    def __init__(self, tmp_path, *extra, limit_kph=None, cycles=50):
        tag = uuid.uuid4().hex[:8]
        self.status, self.events = 's' + tag, str(tmp_path / 'can_guard_events.csv')
        self.argv = bundle() + ['--interface', 'virtual', '--channel', 'o' + tag, '--precheck-s', '0.2', '--max-cycles', str(cycles),
                                '--cmd-shm', 'c' + tag, '--hb-shm', 'h' + tag, '--estop-shm', 'e' + tag, '--status-shm', self.status,
                                '--event-log', self.events] + list(extra)
        self.limit_kph, self.channel = limit_kph, 'o' + tag
        self.seen = []

    def _background(self, done):
        bus = can.Bus(channel=self.channel, interface='virtual')
        while not done.is_set():
            if self.limit_kph is not None:
                raw = int(round(self.limit_kph * 10))
                bus.send(can.Message(arbitration_id=0x202, data=raw.to_bytes(2, 'little') + bytes(6), is_extended_id=False))
            st = cli.read_status(self.status)
            if st is not None:
                self.seen.append(st)
            time.sleep(0.01)
        bus.shutdown()

    def run(self):
        done = threading.Event()
        t = threading.Thread(target=self._background, args=(done,))
        t.start()
        try:
            return can_guard.run(can_guard.parse_args(self.argv))
        finally:
            done.set()
            t.join()


def test_no_202_is_exit_5_with_the_text_bringup_looks_for(tmp_path, capfd):
    assert GuardRun(tmp_path).run() == 5
    err = capfd.readouterr().err
    assert '0x202 시작 점검' in err and '시작 거부(종료 코드 5)' in err


def test_residual_limit_is_exit_5(tmp_path, capfd):
    assert GuardRun(tmp_path, limit_kph=9.3).run() == 5
    assert '제한이 남아 있음' in capfd.readouterr().err


def test_sil_way_to_see_exit_5(tmp_path, capfd):
    """대회 PC 확인 명령에 쓰는 방법: 가짜 차량은 0x202 = 30.0 뿐이라 연습 기대값을 어긋나게 해 거부를 만든다."""
    assert GuardRun(tmp_path, '--speed-limit-practice', '--speed-limit-practice-kph', '77', limit_kph=30.0).run() == 5
    assert '시작 거부(종료 코드 5)' in capfd.readouterr().err


def test_bad_race_config_is_exit_2_with_the_text_bringup_looks_for(tmp_path, capfd):
    assert GuardRun(tmp_path, '--slow-brake-kph', '30', limit_kph=30.0).run() == 2
    assert '경기 설정 오류' in capfd.readouterr().err


def test_bundle_runs_and_table_reads_real_channel(tmp_path, capfd):
    g = GuardRun(tmp_path, limit_kph=30.0)
    assert g.run() == 0
    assert '0x202 시작 점검' in capfd.readouterr().err
    assert g.seen, 'StatusChannel 을 한 번도 못 읽음'
    st = g.seen[-1]
    st.age_s = 0.0
    rows, code = cli.evaluate(st)
    assert code == cli.EXIT_NOT_READY                   # 모델이 없어 INIT, 모델 정상 0 s
    assert '경기 상태 = PRE_GO' in text(rows) and 'INIT' in text(rows) and '0x202 = 30.0' in text(rows)
    s = '\n'.join(cli.summarize_events(g.events))
    assert 'speed_limit_startup' in s and 'observed=30.0' in s


# ── 문서 대조: 문서의 옵션이 실제 옵션인가 ─────────────────────
BSW_UP_OPTS = {'--name', '--sil', '--peak', '--manual-arm', '--no-app', '--no-rt', '--no-ui', '--acc-limit', '--params', '--yes',
               '--race', '--race-opt', '--race-sil-server',    # 켜기 스크립트. 스크립트 쪽 대조는 사람이 한다(README 확인 상태)
               '--force', '--no-disarm', '--guard-args', '--reset'}    # 끄기·stop·bringup·비상 정지 도구
LIFT_CMD_OPTS = {'--steer-limit-deg', '--brake-limit-pct', '--acc-limit-pct', '--acc-hold-s', '--steer-hold-s', '--brake-hold-s', '--log'}
MISC_OPTS = {'--ros-args'}


def source_options(rel):
    src = open(os.path.join(REPO, rel), encoding='utf-8').read()
    return set(re.findall(r"add_argument\(\s*'(--[a-z0-9-]+)'", src))


def guard_options():
    return set(source_options('safety/can_guard/can_guard.py'))


def tool_options():
    return (guard_options() | BSW_UP_OPTS | LIFT_CMD_OPTS | MISC_OPTS | source_options('tools/bsw/race_status_cli.py')
            | source_options('tools/race_flag/fit_long_profiles.py') | source_options('bsw_test/common/scripts/profile_gen.py'))


def section(rel, start, end=None):
    s = open(os.path.join(REPO, rel), encoding='utf-8').read()
    i = s.index(start)
    j = s.index(end, i + len(start)) if end else len(s)
    return s[i:j]


DOCS = {
    'manual': ('bsw_test/FIELD_MANUAL.md', '## 8. 경기 날', None),
    'cheatsheet': ('bsw_test/FIELD_CHEATSHEET.txt', '★ 경기 날', '그날 끝 — 데이터 옮기기'),
    'readme': ('tools/bsw/README.md', '## 경기 모드', '## 확인 상태'),
    'plan': ('bsw_test/docs/race_flag_vehicle_test_plan.md', '# 경기 깃발', None),
}


@pytest.mark.parametrize('doc', sorted(DOCS))
def test_doc_options_exist(doc):
    text_ = section(*DOCS[doc])
    used = set(re.findall(r'(?<![\w-])--[a-z][a-z0-9-]+', text_))
    assert used, doc
    assert used - tool_options() == set(), doc


def race_opts_in(text_):
    return re.findall(r'--race-opt "([^"]+)"', text_)


@pytest.mark.parametrize('doc', sorted(DOCS))
def test_race_opt_examples_parse(doc):
    """--race-opt 안의 글은 can_guard 가 정말 받는 옵션·값이어야 한다(<…> 자리는 숫자로 바꿔 본다)."""
    for opt in race_opts_in(section(*DOCS[doc])):
        words = re.sub(r'<[^>]+>', '1', opt).replace('…', '').split()
        if not words:
            continue
        assert set(w for w in words if w.startswith('--')) <= guard_options(), opt
        can_guard.parse_args(bundle() + words)


def test_docs_say_30_is_normal_not_a_limit_on_autonomy():
    for doc in ('manual', 'cheatsheet'):
        assert '30.0' in section(*DOCS[doc]) and '원격 조종' in section(*DOCS[doc]), doc
    for doc in DOCS:
        t = section(*DOCS[doc])
        assert '30 km/h 로 제한돼' not in t and '30 km/h 로 제한되어' not in t, doc


def test_race_opts_documented_in_readme_table():
    t = open(os.path.join(HERE, 'README.md'), encoding='utf-8').read()
    for o in ('--race', '--race-opt', '--race-sil-server'):
        assert f'`{o}' in t, o
