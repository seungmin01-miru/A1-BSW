"""fit_long_profiles.py 시험 — 합성 로그(차량 모델 + candump 형식)로 8종 검사와 추천 경로를 고정한다.

    python3 -m pytest -q tools/race_flag/test_fit_long_profiles.py

합성 차량 값은 시험용 가정이고 실측이 아니다. 실제 로그 시험은 11-04 zst 가 저장소에 있을 때만 돈다.
"""
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fit_long_profiles as F  # noqa: E402

REPO = F.REPO
RAW = os.path.join(REPO, 'measurements', '2026-11-04_drive_log', 'raw_can')
EVENTS_1104 = os.path.join(REPO, 'measurements', '2026-11-04_drive_log', 'tables', 'canguard_events.csv')
T0 = 1793770000.0
DT = 0.02


# ───────────────────────── 합성 로그 ─────────────────────────

def decel_of(pct):
    return float(np.interp(pct, [0, 40, 70, 90, 100], [0.1, 1.0, 2.0, 3.0, 4.0]))


def accel_of(pct):
    return 0.0 if pct < 75 else float(np.interp(pct, [75, 80, 100], [0.1, 0.13, 0.57]))


class Sim:
    """명령 시퀀스 → 참 속도. 브레이크 응답 0.1 s."""

    def __init__(self, decel_gain=1.0):
        self.v, self.t = 0.0, []
        self.true, self.acc, self.brk, self.spin, self.lock = [], [], [], [], []
        self.gain = decel_gain

    def step(self, acc=0.0, brk=0.0, spin=0.0, lock=0.0):
        delayed = self.brk[-5] if len(self.brk) >= 5 else 0.0
        a = accel_of(acc) if brk == 0 else 0.0
        if delayed > 0:
            a = -decel_of(delayed) * self.gain
        self.v = max(0.0, self.v + a * DT)
        self.true.append(self.v)
        self.acc.append(acc)
        self.brk.append(brk)
        self.spin.append(spin)
        self.lock.append(lock)

    def hold(self, s, **kw):
        for _ in range(int(round(s / DT))):
            self.step(**kw)

    def ramp_up(self, rate=45.0, top=100.0, spin=0.0):
        pct = 0.0
        while pct < top:
            pct = min(top, pct + rate * DT)
            self.step(acc=round(pct), spin=spin)

    def speedup(self, target, spin=0.0):
        self.ramp_up()
        while self.v < target:
            self.step(acc=100.0, spin=spin)

    def brake_event(self, pct, lock=0.0, max_s=3.0):
        n = 0
        while self.v > 0.5 and n * DT < max_s:
            self.step(brk=float(pct), lock=lock)
            n += 1
        self.hold(0.3)

    def stop(self):
        while self.v > 0:
            self.step(brk=85.0)
        self.hold(0.5)


def scenario(brake_pcts=(10, 30, 50, 70, 85), reps=3, spin_steps=True, step_only=False, lock_from=None, decel_gain=1.0):
    """각 브레이크 %·가속 % 를 reps 회씩. 사이에 정속(센서 비교용)."""
    s = Sim(decel_gain)
    s.hold(1.0)
    for pct in brake_pcts:
        for _ in range(reps):
            if step_only:
                s.step(acc=100.0)
                while s.v < 5.5:
                    s.step(acc=100.0)
            else:
                s.speedup(5.5)
            s.hold(4.0)   # 정속
            s.brake_event(pct, lock=0.4 if lock_from is not None and pct >= lock_from else 0.0)
    s.stop()
    for pct in (80, 90):
        for _ in range(reps):
            if step_only:
                s.hold(3.2, acc=float(pct))
            else:
                s.ramp_up(top=float(pct))
                s.hold(3.2, acc=float(pct))
            s.stop()
    if spin_steps and not step_only:
        for _ in range(reps):
            s.hold(3.2, acc=100.0, spin=0.20)   # 계단(한 프레임에 100 %) → 휠스핀
            s.stop()
    return s


def encode(msg, **values):
    raw = 0
    for sig in msg.signals:
        v = int(round((values.get(sig.name, 0) - sig.offset) / sig.factor))
        raw |= (v & ((1 << sig.length) - 1)) << sig.start
    return raw.to_bytes(8, 'little').hex().upper()


def candump(s, app_at=None, res_at=None, foreign_at=None, wheel_scale=0.97, seed=1):
    """Sim → candump 줄. 프레임 순서·주기는 실제 버스와 같게 20 ms."""
    import dbc_lite
    db = dbc_lite.load(F.DBC_PATH)
    db.update(dbc_lite.remote_messages())
    rng = np.random.default_rng(seed)
    lines = []
    for i, v in enumerate(s.true):
        t = T0 + i * DT
        w = v * wheel_scale * (1 + s.spin[i] - s.lock[i]) * 3.6 + rng.normal(0, 0.03)
        w = max(0.0, w)
        lines.append(f'({t:.6f}) kv0 201#' + encode(db[0x201], left_speed=w, right_speed=w))
        lines.append(f'({t + 0.001:.6f}) kv0 200#' + encode(db[0x200], break_postion=s.brk[max(0, i - 3)]))
        lines.append(f'({t + 0.002:.6f}) kv0 210#' + encode(db[0x210], acc_command=s.acc[i], break_command=s.brk[i],
                                                         steer_is_auto_command=1, break_is_auto_command=1, acc_is_auto_command=1))
        en = 1 if app_at is not None and app_at <= i * DT < app_at + 3 else 0
        lines.append(f'({t + 0.003:.6f}) kv0 156#' + encode(db[0x156], EPS_En=en, ACC_En=en))
        if res_at is not None:
            on = 1 if res_at <= i * DT < res_at + 3 else 0
            lines.append(f'({t + 0.004:.6f}) kv0 127#' + encode(db[0x127], RES_START=on))
        if foreign_at is not None and foreign_at <= i * DT < foreign_at + 5:
            lines.append(f'({t + 0.012:.6f}) kv0 210#' + encode(db[0x210], acc_command=0, break_command=0))
    return lines


def write_can(path, lines):
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    return str(path)


def write_gnss(path, s, shift=0.0, fix=None, pitch=None, drop=(), noise_seed=2):
    rng = np.random.default_rng(noise_seed)
    t = T0 + np.arange(len(s.true)) * DT + shift
    v = np.array(s.true) + rng.normal(0, 0.005, len(s.true))
    cols = {'t': [f'{x:.6f}' for x in t], 'speed_mps': [f'{x:.4f}' for x in v],
            'fix': list(fix if fix is not None else [4] * len(t)),
            'pitch_deg': list(pitch if pitch is not None else [0.5] * len(t))}
    for k in drop:
        cols.pop(k)
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(cols.keys())
        w.writerows(zip(*cols.values()))
    return str(path)


def write_events(path, rows=None):
    base = [(T0, 'start', 'kvaser:0'), (T0 + 1, 'state', 'INIT->ACTIVE')] + (rows or [])
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['wall_s', 'event', 'detail'])
        w.writerows(base)
    return str(path)


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture(scope='module')
def base():
    s = scenario()
    return s, candump(s)


class Case:
    def __init__(self, tmp, s, lines):
        self.tmp, self.s, self.lines = tmp, s, lines
        self.can = write_can(tmp / 'can.log', lines)
        self.gnss = write_gnss(tmp / 'gnss.csv', s)
        self.events = write_events(tmp / 'events.csv')
        self.out = str(tmp / 'out')

    def run(self, *extra, gnss=True, events=True, plots=False, can=None):
        argv = ['--can', can or self.can, '--out', self.out]
        if gnss:
            argv += ['--gnss-csv', self.gnss]
        if events:
            argv += ['--events', self.events]
        if not plots:
            argv.append('--no-plots')
        return F.main(argv + list(extra))

    def summary(self):
        with open(os.path.join(self.out, 'summary.json')) as f:
            return json.load(f)

    def status(self, key):
        return next(c['status'] for c in self.summary()['checks'] if c['key'] == key)

    @property
    def blocked_text(self):
        with open(os.path.join(self.out, 'UPDATE_BLOCKED.md')) as f:
            return f.read()

    @property
    def has_blocked(self):
        return os.path.exists(os.path.join(self.out, 'UPDATE_BLOCKED.md'))


@pytest.fixture
def case(tmp_path, base):
    return Case(tmp_path, *base)


# ───────────────────────── 전 검사 통과 경로 ─────────────────────────

def test_all_checks_pass_exit_0_and_recommends(case, capsys):
    yaml_path, hist = F.DEFAULT_CURRENT, os.path.join(REPO, 'docs', 'race_flag_profile_history.md')
    before = sha(yaml_path), sha(hist)
    assert case.run(plots=True) == F.EXIT_OK
    assert not case.has_blocked
    assert (sha(yaml_path), sha(hist)) == before, '도구는 yaml·이력을 고치면 안 된다'
    sm = case.summary()
    assert sm['verdict'] == 'pass'
    assert all(c['status'] == F.PASS for c in sm['checks'])
    rec = sm['recommendation']
    assert rec['a_slow']['value'] == pytest.approx(0.8 * decel_of(85), abs=0.2)   # 잠김 미관측 → 최대 감속 × 0.8
    assert rec['a_slow']['lock_start_pct'] is None
    assert rec['ramp']['value'] == pytest.approx(45.0, abs=6.0)                  # 45 %/s 는 안전, 계단은 슬립 > 10 %
    assert rec['ramp']['spin_start_pct'] == pytest.approx(100, abs=1)
    assert sm['scale'] == pytest.approx(0.97, abs=0.005)
    assert sm['brake_delay_s']['n'] >= 15
    assert sm['brake_delay_s']['p50'] == pytest.approx(0.06, abs=0.025)          # 합성 위치 응답 3프레임
    assert '전 검사 통과' in capsys.readouterr().out
    for name in ('report.md', 'segments.csv', 'summary.json', 'brake_map.png', 'accel_map.png', 'timeline.png', 'slip.png'):
        assert os.path.exists(os.path.join(case.out, name)), name
    with open(os.path.join(case.out, 'report.md')) as f:
        assert '이력 줄 초안' in f.read()


def test_stale_blocked_file_removed_on_pass(case):
    os.makedirs(case.out)
    with open(os.path.join(case.out, 'UPDATE_BLOCKED.md'), 'w') as f:
        f.write('옛 결과')
    assert case.run() == F.EXIT_OK
    assert not case.has_blocked


# ───────────────────────── 8종 검사: 하나씩 걸리게 ─────────────────────────

def assert_blocked(case, key, status=F.FAIL, code=None):
    code = case.run() if code is None else code
    assert code == F.EXIT_BLOCKED
    assert case.has_blocked
    assert case.status(key) == status, case.summary()['checks']
    return case.blocked_text


def test_gnss_fix_ratio_low(tmp_path, base):
    s, lines = base
    c = Case(tmp_path, s, lines)
    c.gnss = write_gnss(tmp_path / 'g.csv', s, fix=[4 if i % 2 else 1 for i in range(len(s.true))])
    assert 'GNSS 품질' in assert_blocked(c, 'gnss_quality')


def test_gnss_fix_ratio_at_threshold_passes(tmp_path, base):
    s, lines = base
    c = Case(tmp_path, s, lines)
    c.gnss = write_gnss(tmp_path / 'g.csv', s, fix=[1 if i % 20 == 0 else 4 for i in range(len(s.true))])   # 95 %
    assert c.run() == F.EXIT_OK


def test_app_en_blocks_even_with_clean_rest(tmp_path, base):
    s = base[0]
    c = Case(tmp_path, s, candump(s, app_at=30.0))
    assert '0x156' in assert_blocked(c, 'mixed_can')


def test_res_blocks(tmp_path, base):
    s = base[0]
    c = Case(tmp_path, s, candump(s, res_at=30.0))
    assert 'RES' in assert_blocked(c, 'mixed_can')


def test_two_senders_on_can_blocks(tmp_path, base):
    s = base[0]
    c = Case(tmp_path, s, candump(s, foreign_at=30.0))
    assert '송신자 둘' in assert_blocked(c, 'mixed_can')


def test_foreign_event_blocks(case):
    case.events = write_events(case.tmp / 'ev2.csv', [(T0 + 20, 'foreign_0x210', 'n=1')])
    assert 'foreign_0x210' in assert_blocked(case, 'mixed_can')


def test_estop_event_blocks(case):
    case.events = write_events(case.tmp / 'ev2.csv', [(T0 + 20, 'estop_request', 'remote remote_en')])
    assert 'ESTOP' in assert_blocked(case, 'mixed_estop')


def test_events_from_another_session_ignored(case):
    case.events = write_events(case.tmp / 'ev2.csv', [(T0 + 90000, 'estop_request', 'remote remote_en')])
    assert case.run() == F.EXIT_OK


def test_no_event_log_means_estop_unknown(case):
    assert_blocked(case, 'mixed_estop', F.UNKNOWN, case.run(events=False))


def test_event_log_found_next_to_can_log(tmp_path, base):
    c = Case(tmp_path, *base)
    shutil.copy(c.events, tmp_path / 'can_guard_events.csv')
    assert c.run(events=False) == F.EXIT_OK


def test_physical_limit(tmp_path):
    s = scenario(decel_gain=1.8)   # 85 % → 4.95 m/s²
    c = Case(tmp_path, s, candump(s))
    assert '물리 한계' in assert_blocked(c, 'physical')


def test_sensor_mismatch(tmp_path, base):
    s = base[0]
    c = Case(tmp_path, s, candump(s, wheel_scale=0.92))
    assert '센서 불일치' in assert_blocked(c, 'sensor')


def test_sensor_no_constant_segment_is_unknown():
    assert F.check_sensor([], True).status == F.UNKNOWN


@pytest.mark.parametrize('shift,status', [(0.2, F.FAIL), (-0.2, F.FAIL), (5.0, F.UNKNOWN), (52.0, F.UNKNOWN), (100000.0, F.UNKNOWN)])
def test_time_offset(tmp_path, base, shift, status):
    s, lines = base
    c = Case(tmp_path, s, lines)
    c.gnss = write_gnss(tmp_path / 'g.csv', s, shift=shift)
    assert '시간 어긋남' in assert_blocked(c, 'time', status)


def test_time_offset_under_limit_passes(tmp_path, base):
    s, lines = base
    c = Case(tmp_path, s, lines)
    c.gnss = write_gnss(tmp_path / 'g.csv', s, shift=0.02)
    assert c.run() == F.EXIT_OK


def test_sample_shortage(tmp_path):
    s = scenario(brake_pcts=(10, 50, 70, 85))   # 20~40 % 구간이 비었다
    c = Case(tmp_path, s, candump(s))
    assert '브레이크 20~40 %: 0회' in assert_blocked(c, 'samples')


def test_two_events_in_a_bin_is_short(tmp_path):
    s = scenario(reps=2)
    c = Case(tmp_path, s, candump(s))
    assert_blocked(c, 'samples')


def test_slope(tmp_path, base):
    s, lines = base
    c = Case(tmp_path, s, lines)
    p = np.full(len(s.true), 0.5)
    p[np.array(s.brk) > 0] = 3.0
    c.gnss = write_gnss(tmp_path / 'g.csv', s, pitch=[f'{x:.2f}' for x in p])
    assert '경사' in assert_blocked(c, 'pitch')


def test_abrupt_change_asks_human(tmp_path, base):
    c = Case(tmp_path, *base)
    cur = yaml.safe_load(open(F.DEFAULT_CURRENT))
    cur['a_slow'] = 1.0
    path = tmp_path / 'cur.yaml'
    path.write_text(yaml.safe_dump(cur))
    code = c.run('--current', str(path))
    txt = assert_blocked(c, 'abrupt', code=code)
    assert '사람 확인 요청' in txt and 'a_slow' in txt
    assert path.read_text() == yaml.safe_dump(cur)


# ───────────────────────── 확인 불가 = 갱신 중단 ─────────────────────────

@pytest.mark.parametrize('drop,key', [(('fix',), 'gnss_quality'), (('pitch_deg',), 'pitch')])
def test_missing_column_is_unknown_and_blocks(tmp_path, base, drop, key):
    s, lines = base
    c = Case(tmp_path, s, lines)
    c.gnss = write_gnss(tmp_path / 'g.csv', s, drop=drop)
    assert_blocked(c, key, F.UNKNOWN)


def test_can_only_is_blocked_with_reasons(case, capsys):
    assert case.run(gnss=False) == F.EXIT_BLOCKED
    for key in ('gnss_quality', 'sensor', 'time', 'pitch'):
        assert case.status(key) == F.UNKNOWN
    assert case.status('mixed_can') == F.PASS
    assert '⛔ 갱신 중단' in capsys.readouterr().out


def test_unreadable_bag_blocks_instead_of_crashing(case):
    assert case.run('--bag', str(case.tmp / 'no_such_bag'), gnss=False) == F.EXIT_BLOCKED
    assert case.status('gnss_quality') == F.UNKNOWN


def test_preview_exit_0_no_blocked_file(case, capsys):
    assert case.run('--can-only-preview', gnss=False) == F.EXIT_OK
    assert not case.has_blocked
    out = capsys.readouterr().out
    assert '참고' in out and '전 검사 통과' not in out
    with open(os.path.join(case.out, 'report.md')) as f:
        txt = f.read()
    assert '미리보기' in txt and '(참고)' in txt and '이력 줄 초안' not in txt


def test_preview_shows_contamination_but_stays_0(tmp_path, base):
    s = base[0]
    c = Case(tmp_path, s, candump(s, app_at=30.0))
    assert c.run('--can-only-preview', gnss=False) == F.EXIT_OK
    assert c.status('mixed_can') == F.FAIL


# ───────────────────────── 추천 규칙 ─────────────────────────

def test_lock_lowers_a_slow(tmp_path):
    s = scenario(lock_from=82)
    c = Case(tmp_path, s, candump(s))
    cur = yaml.safe_load(open(F.DEFAULT_CURRENT))
    cur['a_slow'] = 1.7
    path = tmp_path / 'cur.yaml'
    path.write_text(yaml.safe_dump(cur))
    assert c.run('--current', str(path)) == F.EXIT_OK
    rec = c.summary()['recommendation']['a_slow']
    assert rec['lock_start_pct'] == 82
    assert rec['base']['lo'] == 60
    assert rec['value'] == pytest.approx(0.8 * decel_of(70), abs=0.15)


def test_step_without_spin_recommends_no_ramp_and_asks_human(tmp_path):
    s = scenario(step_only=True)
    c = Case(tmp_path, s, candump(s))
    assert c.run() == F.EXIT_BLOCKED           # 50 → 무한 은 급변
    rec = c.summary()['recommendation']['ramp']
    assert rec['value'] == 'inf'
    assert rec['spin_start_pct'] is None
    assert c.status('abrupt') == F.FAIL
    assert 'slow_off_ramp_pct_s' in c.blocked_text


def ev(pct, slip, value=2.0):
    return dict(pct=pct, slip=slip, value=value)


def test_recommend_ramp_rules():
    def rise(rate, slip):
        return dict(rate=rate, slip=slip)
    ok = [rise(40.0, 0.05)] * 3 + [rise(80.0, 0.08)] * 3
    r = F.recommend_ramp(ok, [])
    assert r['value'] == pytest.approx(80.0)
    # 빠른 구간에서 슬립 초과 → 그 아래 값
    r = F.recommend_ramp(ok + [rise(150.0, 0.2)], [])
    assert r['value'] == pytest.approx(80.0)
    # 느린 구간이 터지면 빠른 구간이 멀쩡해도 쓰지 않는다
    r = F.recommend_ramp([rise(40.0, 0.2)] * 3 + [rise(80.0, 0.05)] * 3, [])
    assert r['value'] is None
    # 표본 2회는 근거가 안 된다
    r = F.recommend_ramp([rise(40.0, 0.05)] * 2, [])
    assert r['value'] is None


def test_slip_exactly_at_limit_is_safe():
    r = F.recommend_ramp([dict(rate=40.0, slip=F.SLIP_MAX)] * 3, [])
    assert r['value'] == pytest.approx(40.0)


def test_a_slow_ignores_thin_bins():
    evs = [ev(90.0, 0.0, 3.0)] * 2 + [ev(50.0, 0.0, 1.5)] * 3
    bins = F.bin_events(evs, F.BRAKE_BINS)
    r = F.recommend_a_slow(evs, bins, True)
    assert r['value'] == pytest.approx(1.2)


def test_a_slow_no_gnss_notes_lock_unknown():
    evs = [ev(90.0, math.nan, 3.0)] * 3
    r = F.recommend_a_slow(evs, F.bin_events(evs, F.BRAKE_BINS), False)
    assert r['value'] == pytest.approx(2.4) and '잠김 판정 불가' in r['note']


def test_monotone_table_drops_inversions_and_thin_bins():
    bins = [dict(n=3, value=1.0, pct=30.0), dict(n=3, value=0.8, pct=50.0), dict(n=1, value=2.0, pct=70.0), dict(n=3, value=3.0, pct=90.0)]
    t, dropped = F.monotone_table(bins, 'x', 'y', start=(0.1, 0.0))
    assert t == {'x': [0.1, 1.0, 3.0], 'y': [0.0, 30.0, 90.0]}
    assert dropped == [(50.0, 0.8)]


def test_stage_judgement():
    runs = [dict(wobble_p999=0.3)] * 3
    assert F.judge_stage(runs, 0.015)['stage'] == 2
    assert F.judge_stage(runs, 0.042)['stage'] == 1          # 11-04 비율 그대로면 올라가지 않는다
    assert F.judge_stage(runs[:2], 0.015)['stage'] == 1
    assert F.judge_stage([dict(wobble_p999=0.6)] * 3, 0.015)['stage'] == 1
    assert F.judge_stage(runs, None)['stage'] == 1
    r = F.judge_stage(runs, 0.005)
    assert r['stage'] == 2 and '질의서 7' in r['note']       # 3단계는 KIAPI 답변 전까지 안 올린다


def test_slow_runs_counts_steady_runs_only():
    s = Sim()
    for _ in range(3):
        s.speedup(8.0 / 3.6 / 0.97)
        s.hold(9.0)
        s.stop()
    lg = F.parse_can(candump(s), 'sim', 'sim')
    runs = F.slow_runs([F.make_trace(lg, None, [])])
    assert len(runs) == 3
    assert all(r['median_kph'] == pytest.approx(8.0, abs=0.2) and r['wobble_p999'] < 0.3 for r in runs)


def test_changed():
    assert F.changed(2.0, 2.4) == pytest.approx(0.2)
    assert F.changed(50.0, math.inf) == math.inf
    assert F.changed(math.inf, math.inf) == 0.0
    assert F.changed(2.0, None) is None


def test_abrupt_boundary():
    def row(new):
        return [dict(name='x', old=100.0, new=new, change=F.changed(100.0, new))]
    assert F.check_abrupt(row(119.0)).status == F.PASS
    assert F.check_abrupt(row(121.0)).status == F.FAIL
    assert F.check_abrupt(row(None)).status == F.PASS     # 추천 없음 = 기존 값 유지, 바뀌는 것이 없다


# ───────────────────────── 내부 함수 ─────────────────────────

def test_hold_segments():
    cmd = np.array([0, 50, 50, 52, 50, 50, 90, 90, 0.0])
    other = np.zeros(9)
    assert F.hold_segments(cmd, other, np.zeros(9, bool), 1.0, 3.0) == [(1, 5)]
    # 제외 구간·다른 축 > 0·NaN 은 끊는다
    bad = np.zeros(9, bool)
    bad[3] = True
    assert F.hold_segments(cmd, other, bad, 1.0, 3.0) == []
    o2 = other.copy()
    o2[2] = 10
    assert F.hold_segments(cmd, o2, np.zeros(9, bool), 1.0, 3.0) == []
    c2 = cmd.copy()
    c2[3] = math.nan
    assert F.hold_segments(c2, other, np.zeros(9, bool), 1.0, 3.0) == []


def test_hold_does_not_stretch_over_gap():
    t = np.array([0.0, 0.02, 5.0])
    x = np.array([1.0, 2.0, 3.0])
    out = F._hold(t, x, np.array([0.0, 0.1, 1.0, 5.0]), 0.2)
    assert out[0] == 1.0 and out[1] == 2.0 and math.isnan(out[2]) and out[3] == 3.0


def test_gnss_csv_extract_columns(tmp_path):
    """tools/drive_log/extract.py 의 INS 출력(t,t_bag,imu,gnss,...,spd) 이름을 그대로 받는다."""
    p = tmp_path / 'ins.csv'
    p.write_text('t,t_bag,imu,gnss,lat,lon,spd,hdg\n2.0,2.0,3,1,0,0,5.0,0\n1.0,1.0,3,4,0,0,4.0,0\n')
    g = F.load_gnss_csv(str(p))
    assert list(g.t) == [1.0, 2.0] and list(g.v) == [4.0, 5.0] and list(g.fix) == [True, False] and g.pitch is None


def test_gnss_csv_bad_columns(tmp_path):
    p = tmp_path / 'x.csv'
    p.write_text('a,b\n1,2\n')
    with pytest.raises(F.InputError):
        F.load_gnss_csv(str(p))


def test_bad_inputs_exit_2(tmp_path, capsys):
    assert F.main(['--can', str(tmp_path / 'none.log'), '--out', str(tmp_path / 'o')]) == F.EXIT_USAGE
    empty = tmp_path / 'e.log'
    empty.write_text('이건 CAN 로그가 아니다\n')
    assert F.main(['--can', str(empty), '--out', str(tmp_path / 'o')]) == F.EXIT_USAGE
    assert '입력 오류' in capsys.readouterr().err


def test_bag_and_csv_together_rejected(tmp_path):
    with pytest.raises(SystemExit) as e:
        F.main(['--can', 'x', '--bag', 'y', '--gnss-csv', 'z', '--out', str(tmp_path)])
    assert e.value.code == 2


@pytest.mark.skipif(shutil.which('zstd') is None, reason='zstd 없음')
def test_zst_same_as_plain_and_session_dir(tmp_path, base):
    c = Case(tmp_path, *base)
    assert c.run() == F.EXIT_OK
    plain = c.summary()['recommendation']
    d = tmp_path / 'sess'
    d.mkdir()
    subprocess.run(['zstd', '-q', c.can, '-o', str(d / 'candump_kv0.log.zst')], check=True)
    assert c.run(can=str(d)) == F.EXIT_OK
    assert c.summary()['recommendation'] == plain


def test_session_dir_prefers_mirror_log(tmp_path):
    for n in ('candump_kv0.log.zst', 'kvaser_ch0.log.zst', 'note.txt'):
        (tmp_path / n).write_text('')
    assert F.resolve_can_paths([str(tmp_path)])[0].endswith('kvaser_ch0.log.zst')


def test_odom_flat_pitch_not_trusted(tmp_path):
    """평면 자세 odom(피치 전부 0)은 측정이 아니다 → 확인 불가. ROS 없으면 건너뜀."""
    rosbag2_py = pytest.importorskip('rosbag2_py')
    from nav_msgs.msg import Odometry
    from rclpy.serialization import serialize_message
    uri = str(tmp_path / 'bag.db3')
    w = rosbag2_py.SequentialWriter()
    w.open(rosbag2_py.StorageOptions(uri=str(tmp_path / 'bag'), storage_id='sqlite3'), rosbag2_py.ConverterOptions('cdr', 'cdr'))
    w.create_topic(rosbag2_py.TopicMetadata(name='/gsof_client/odom', type='nav_msgs/msg/Odometry', serialization_format='cdr'))
    for i in range(20):
        m = Odometry()
        m.header.stamp.sec, m.header.stamp.nanosec = 100 + i, 0
        m.twist.twist.linear.x = 3.0
        m.pose.pose.orientation.w = 1.0
        w.write('/gsof_client/odom', serialize_message(m), (100 + i) * 10 ** 9)
    del w
    uri = str(tmp_path / 'bag' / 'bag_0.db3')
    g, why = F.load_gnss_bag(uri, '/gsof_client/odom', '/none', (4,))
    assert g is not None, why
    assert g.fix is None and g.pitch is None and g.v[0] == pytest.approx(3.0) and g.t[0] == pytest.approx(100.0)


# ───────────────────────── race_flag.yaml ─────────────────────────

def test_yaml_header_verbatim_from_plan():
    with open(os.path.join(REPO, 'docs', '2026-11-06_race_flag_dev_plan.md')) as f:
        plan = f.read()
    block = plan.split('### 7.3')[1].split('```yaml\n')[1].split('```')[0]
    with open(F.DEFAULT_CURRENT) as f:
        assert f.read().startswith(block)


def test_yaml_values_match_plan_and_track():
    with open(F.DEFAULT_CURRENT) as f:
        cur = yaml.safe_load(f)
    with open(os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')) as f:
        track = yaml.safe_load(f)['a1_cmd_adapter']['ros__parameters']
    assert cur['accel_map'] == track['accel_map'] and cur['brake_map'] == track['brake_map']
    assert cur['a_slow'] == 2.0 and cur['slow_off_ramp_pct_s'] == 50.0
    assert cur['slow_pi'] == {'kp': 0.8, 'ki': 0.2, 'ff': 0.2}


def test_tool_has_no_write_path_to_yaml_or_history():
    """쓰기 대상은 --out 안뿐이어야 한다. open(..., 'w') 의 경로 인자를 소스에서 확인."""
    with open(F.__file__) as f:
        src = f.read()
    for ln in src.splitlines():
        if "'w'" in ln and 'open(' in ln:
            assert 'os.path.join(out_dir' in ln or 'os.path.join(args.out' in ln, ln
    assert 'race_flag_profile_history' not in src.replace('docs/race_flag_profile_history.md', '')


# ───────────────────────── 11-04 실제 로그 ─────────────────────────

def _session(name):
    p = os.path.join(RAW, name)
    if not os.path.isdir(p):
        pytest.skip('11-04 원본 없음')
    return p


def test_real_app_session_blocks(tmp_path):
    out = tmp_path / 'o'
    code = F.main(['--can', _session('auto_bsw4'), '--events', EVENTS_1104, '--out', str(out), '--no-plots'])
    assert code == F.EXIT_BLOCKED
    txt = (out / 'UPDATE_BLOCKED.md').read_text()
    assert '앱 0x156 En=1' in txt and 'ESTOP' in txt


def test_real_two_sender_session_detected(tmp_path):
    out = tmp_path / 'o'
    assert F.main(['--can', _session('auto_bsw5'), '--events', EVENTS_1104, '--out', str(out), '--no-plots']) == F.EXIT_BLOCKED
    assert '송신자 둘' in (out / 'UPDATE_BLOCKED.md').read_text()


def test_real_clean_session_preview_and_can_checks(tmp_path):
    out = tmp_path / 'o'
    assert F.main(['--can', _session('auto_bsw3'), '--events', EVENTS_1104, '--can-only-preview', '--out', str(out), '--no-plots']) == F.EXIT_OK
    sm = json.loads((out / 'summary.json').read_text())
    st = {c['key']: c['status'] for c in sm['checks']}
    assert st['mixed_can'] == F.PASS and st['mixed_estop'] == F.PASS and st['physical'] == F.PASS
    assert st['time'] == st['sensor'] == st['gnss_quality'] == st['pitch'] == F.UNKNOWN
    assert not (out / 'UPDATE_BLOCKED.md').exists()
