#!/usr/bin/env python3
"""경기 모드 시작 점검 표시 — can_guard StatusChannel·이벤트 로그를 읽기만 한다(버스·공유메모리에 쓰지 않음, CAN 에 닿지 않음).

  python3 tools/bsw/race_status_cli.py check                 # 시작 점검 표(계획서 WP8). 종료 코드 아래
  python3 tools/bsw/race_status_cli.py check --events <csv>  # + 이벤트 요약
  python3 tools/bsw/race_status_cli.py brief                 # 한 줄(세션 기록용). 항상 종료 코드 0
  python3 tools/bsw/race_status_cli.py events <csv>          # 이벤트 요약(이의제기 증거, R-09). can_guard 가 꺼져 있어도 된다

check 종료 코드: 0 = 출발 준비됨(PRE_GO, ❌ 없음) / 1 = ❌ 있음 / 2 = 경기 상태를 못 읽음 / 3 = 이미 PRE_GO 가 아님(표시만).
⚠️ 는 실패가 아니다 — 대회 신호 방식이 정해지기 전에는 판단할 수 없는 것들(0x124·0x127 미수신)이다.
판단은 여기서 하지 않는다. 경기 상태 판단은 can_guard 한 곳(D5)이고 이 표는 사람이 보는 표시일 뿐이다.
"""
import argparse
import csv
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'safety', 'can_guard'))
import protocol  # noqa: E402

OK, WARN, BAD = 'ok', 'warn', 'bad'
MARK = {OK: '✅', WARN: '⚠️', BAD: '❌'}
STALE_S = protocol.STATUS_MAX_AGE_S_DEFAULT
SIGNAL_QUIET_S = 1.0     # 0x124 가 이보다 오래 안 오면 ⚠️ (표시용, 판단 아님)
EXIT_READY, EXIT_NOT_READY, EXIT_NO_STATUS, EXIT_NOT_PRE_GO = 0, 1, 2, 3

# 시간표에 넣는 이벤트 — 깃발·속도 판정·정지 사유. 이름은 safety/can_guard/README.md "경기 모드" 절.
TIMELINE_EVENTS = {'race_signal', 'race_phase', 'slow_on', 'slow_off', 'res', 'res_alive_lost', 'res_alive_restored',
                   'pre_go_motion', 'override_on', 'override_off', 'recover', 'commit', 'resume', 'estop_request',
                   'race_internal_error', 'race_config_error', 'race_signal_lost', 'race_signal_restored',
                   'speed_limit_startup', 'speed_limit_mismatch', 'speed_limit_release_slow', 'foreign_0x203',
                   'foreign_0x210', 'state'}
MAX_TIMELINE = 200


def _speed_limit_row(st, slow_brake_kph):
    v = st.vehicle_speed_limit_kph
    target = '' if st.speed_limit_target_kph < 0 else f' · 0x203 목표 {st.speed_limit_target_kph:.1f} km/h'
    if v < 0:
        return BAD, f'0x202 수신 없음{target}'
    if v == 0:
        return WARN, f'0x202 = 0.0 km/h — 뜻 미확인(업체 질문){target}'
    if v <= slow_brake_kph:
        return BAD, f'0x202 = {v:.1f} km/h — 서행 제한 잔류 의심(≤ {slow_brake_kph:g}){target}'
    note = ' (원격 조종 제한값, 정상. 자율 상한 아님)' if abs(v - 30.0) < 0.05 else ''
    return OK, f'0x202 = {v:.1f} km/h{note}{target}'


def _signal_row(st):
    if st.race_signal_age_s < 0:
        return WARN, '0x124 아직 수신 없음 — 깃발 때만 보내는 방식이면 정상(브리핑 확인)'
    bits = [n for n, on in (('Go', st.sig_go), ('SlowOn', st.sig_slow_on), ('SlowOff', st.sig_slow_off),
                            ('Finish', st.sig_finish), ('Stop', st.sig_stop)) if on]
    text = f'0x124 {st.race_signal_age_s:.2f} s 전 수신 · 켜진 비트 {", ".join(bits) if bits else "없음"}'
    if st.sig_stop:
        return BAD, text + ' — Stop 켜짐'
    if st.sig_go and st.race_phase == protocol.RACE_PHASE_CODE['PRE_GO']:
        return WARN, text + ' — 옛 Go 로는 출발하지 않는다(0 을 본 뒤 올라가야 인정, A1)'
    if st.race_signal_age_s > SIGNAL_QUIET_S:
        return WARN, text + ' — 흐름이 멈춤(깃발 때만 보내는 방식이면 정상, 브리핑 확인)'
    return OK, text


def _res_row(st):
    if st.res_active:
        return BAD, 'RES 작동 중(0x127 RES_START = 1)'
    if st.res_alive_ok:
        return OK, '0x127 alive 정상, RES 대기'
    if st.race_phase == protocol.RACE_PHASE_CODE['SUSPENDED']:
        return BAD, '0x127 alive 끊김 또는 RES 뒤 중단(SUSPENDED)'
    return WARN, '0x127 아직 수신 없음 — RES 때만 보내는 방식이면 정상(브리핑 확인)'


def _guard_row(st):
    name = st.guard_state_name
    if name == 'ACTIVE':
        return OK, '안전 축 ACTIVE(어댑터 arm, 모델 입력 받는 중)'
    if name == 'INIT':
        return BAD, '안전 축 INIT — 아직 arm 전(모델 발행 또는 어댑터 확인). 이대로 Go 가 오면 출발하지 못한다'
    if name in ('HOLDING', 'DEGRADED'):
        return BAD, f'안전 축 {name} — 모델 입력·하트비트 끊김(돌아오면 자동 복귀. 차는 Go 전이라 그대로 정지)'
    return BAD, f'안전 축 {name} — 재시작해야 빠져나온다'


def evaluate(st, slow_brake_kph=9.3, min_model_s=10.0):
    """(행 목록 [(수준, 글)], 종료 코드). st 는 GuardStatus(나이 포함) 또는 None."""
    if st is None:
        return [(BAD, 'StatusChannel 없음 — can_guard 가 꺼졌거나 아직 첫 주기 전')], EXIT_NO_STATUS
    if not st.race_enabled:
        return [(BAD, 'can_guard 가 경기 모드(--race)가 아님')], EXIT_NO_STATUS

    phase = st.race_phase_name
    rows = []
    if st.age_s > STALE_S:
        rows.append((BAD, f'상태 값이 {st.age_s:.1f} s 오래됨 — can_guard 가 멈췄을 수 있다'))
    pre_go = phase == 'PRE_GO'
    if pre_go:
        note = ' (Go 전에 온 Slow On 기억 중 — 출발하면 서행)' if st.slow_active else ''
        rows.append((OK, f'경기 상태 = PRE_GO{note}'))
    else:
        shown = 'RACING + 서행' if phase == 'RACING' and st.slow_active else phase
        rows.append((BAD if phase == 'SUSPENDED' else WARN, f'경기 상태 = {shown} — PRE_GO 아님(시작 점검 대상 아님)'))
    rows.append(_guard_row(st))
    rows.append((OK if st.model_healthy_s >= min_model_s else BAD,
                 f'모델 정상 {st.model_healthy_s:.1f} s (기준 ≥ {min_model_s:g} s)'))
    rows.append(_speed_limit_row(st, slow_brake_kph))
    rows.append(_signal_row(st))
    rows.append(_res_row(st))

    if not pre_go:
        return rows, EXIT_NOT_PRE_GO
    return rows, EXIT_NOT_READY if any(level == BAD for level, _ in rows) else EXIT_READY


def format_table(rows, code):
    head = {EXIT_READY: '✅ 출발 준비됨', EXIT_NOT_READY: '❌ 준비 안 됨', EXIT_NO_STATUS: '❌ 경기 상태 없음',
            EXIT_NOT_PRE_GO: 'ℹ️ 이미 출발 대기가 아님 — 표시만'}[code]
    return '\n'.join([f'── 경기 시작 점검: {head}'] + [f'  {MARK[level]} {text}' for level, text in rows])


def brief(st):
    if st is None:
        return 'StatusChannel 없음'
    if not st.race_enabled:
        return '경기 모드 아님'
    lim = '수신 없음' if st.vehicle_speed_limit_kph < 0 else f'{st.vehicle_speed_limit_kph:.1f} km/h'
    return (f'{st.race_phase_name}{" + 서행" if st.slow_active else ""} · 안전 축 {st.guard_state_name} · 0x202 {lim} · '
            f'모델 정상 {st.model_healthy_s:.1f} s · 나이 {st.age_s:.2f} s')


def read_status(name):
    """읽기 전용. 없거나 못 읽으면 None."""
    try:
        ch = protocol.StatusChannel.open_or_none(name)
    except (ValueError, OSError):       # 만드는 도중이거나 크기가 다른 구버전 세그먼트
        return None
    if ch is None:
        return None
    try:
        return ch.read(max_age_s=None)  # 표시용 — 나이는 age_s 로 보여 준다
    except protocol.TornReadError:
        return None
    finally:
        ch.close()


def summarize_events(path):
    """can_guard --event-log CSV 요약 줄 목록(Markdown 목록, SESSION.md 에 붙임). 시각은 이 PC 벽시계."""
    if not os.path.isfile(path):
        return [f'- 이벤트 로그 없음: {path}']
    with open(path, newline='') as f:
        rows = [r for r in csv.DictReader(f) if r.get('event')]
    if not rows:
        return [f'- 이벤트 로그 비어 있음: {path}']
    counts = Counter(r['event'] for r in rows)
    lines = [f'- 이벤트 {len(rows)} 줄: ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items()))]
    for name, what in (('pre_go_motion', 'Go 전 움직임'), ('race_internal_error', '경기 계산 예외'),
                       ('foreign_0x210', '다른 0x210 송신자'), ('foreign_0x203', '다른 0x203 송신자')):
        if counts[name]:
            lines.append(f'- ⚠️ {what} {counts[name]} 회({name})')
    picked = [r for r in rows if r['event'] in TIMELINE_EVENTS]
    lines.append('- 시간표(벽시계):')
    for r in picked[:MAX_TIMELINE]:
        try:
            t = float(r['wall_s'])
            stamp = time.strftime('%H:%M:%S', time.localtime(t)) + f'.{int(t * 1000) % 1000:03d}'
        except (TypeError, ValueError):
            stamp = '?'
        lines.append(f'  - {stamp} {r["event"]} {r.get("detail") or ""}'.rstrip())
    if len(picked) > MAX_TIMELINE:
        lines.append(f'  - … {len(picked) - MAX_TIMELINE} 줄 더(전체는 {os.path.basename(path)})')
    return lines


def build_parser():
    ap = argparse.ArgumentParser(description='경기 모드 시작 점검 표시(읽기 전용)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check', help='시작 점검 표')
    c.add_argument('--shm', default=protocol.STATUS_SHM_NAME_DEFAULT, help='StatusChannel 이름')
    c.add_argument('--slow-brake-kph', type=float, default=9.3, help='0x202 가 이 이하이면 잔류 서행 제한 의심(can_guard 와 같은 값)')
    c.add_argument('--min-model-s', type=float, default=10.0, help='모델 정상 시간 기준(계획서 WP8)')
    c.add_argument('--events', help='이벤트 로그가 있으면 요약도 출력')
    b = sub.add_parser('brief', help='한 줄 상태')
    b.add_argument('--shm', default=protocol.STATUS_SHM_NAME_DEFAULT, help='StatusChannel 이름')
    e = sub.add_parser('events', help='이벤트 요약')
    e.add_argument('path', help='can_guard --event-log CSV')
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == 'events':
        print('\n'.join(summarize_events(a.path)))
        return 0
    st = read_status(a.shm)
    if a.cmd == 'brief':
        print(brief(st))
        return 0
    rows, code = evaluate(st, a.slow_brake_kph, a.min_model_s)
    print(format_table(rows, code))
    if a.events and os.path.isfile(a.events):
        print('\n'.join(summarize_events(a.events)))
    return code


if __name__ == '__main__':
    sys.exit(main())
