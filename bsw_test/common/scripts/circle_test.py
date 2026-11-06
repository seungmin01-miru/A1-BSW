#!/usr/bin/env python3
"""원 돌기 시험(지시서 2.5·4절) — 핸들각 단계 입력 + 저속 유지(≤ 8 km/h). profile_gen 세션으로 돈다(Enter·중단·로그가 같다).

  python3 bsw_test/common/scripts/circle_test.py --run 20261114_ER-CIRCLE-01 --dry-run          # 계획만
  python3 bsw_test/common/scripts/circle_test.py --run 20261114_ER-CIRCLE-01 --channel <채널>    # bringup --no-adapter 뒤

**핸들각으로 직접 명령한다**(어댑터·조향 표를 거치지 않음): 표를 재는 시험이라 표를 통과하면 안 된다(E-7). 안전은 profile_gen 그대로:
회차마다 Enter, 차속 상한, 0x201 끊김, can_guard 개입이면 세션 중단, 끝내기 브레이크 100 %. 분석은 tools/engine_room/fit_circle.py.
"""
import argparse
import math
import os
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter'))
from a1_control_adapter.conversion import SteerMap  # noqa: E402

GEN = os.path.join(HERE, 'profile_gen.py')
PROFILE = os.path.join(REPO, 'bsw_test', '02_LON_profile', 'profiles', 'steer_circle_speed.yaml')
CTRL = os.path.join(REPO, 'bsw_test', '09_ENGINE_ROOM', 'ctrl', 'speed_ctrl_circle.yaml')
TRACK = os.path.join(REPO, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')
WHEELBASE_M = 2.36
MARGIN = 1.25               # 표가 틀려 반경이 커도 원하는 바퀴 수를 채우게 여유
MAX_KPH = 8.0               # 지시서 상한


def plan(handles, sides, laps, kph, steer_map):
    """[(핸들 °, 예상 타이어 °, 예상 반경 m, 시간 s)]. 시간 = 거리 / 속도 × 여유 + 출발 램프."""
    out = []
    for h in handles:
        for s in sides:
            deg = s * h
            tire = abs(steer_map.to_tire(deg))
            r = WHEELBASE_M / math.tan(math.radians(tire))
            t = laps * 2 * math.pi * r / (kph / 3.6) * MARGIN + kph / 2.0
            out.append((deg, tire, r, round(t, 1)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True)
    ap.add_argument('--handles', default='37,75,112,150', help='핸들 ° 크기')
    ap.add_argument('--sides', default='left,right')
    ap.add_argument('--laps', type=float, default=2.0)
    ap.add_argument('--kph', type=float, default=5.0, help='목표 차속. 저속 유지가 약 ±1 km/h 흔들리므로(SIL) 상한 8 km/h 에 여유를 둔다')
    ap.add_argument('--out', default='~/a1_race_capture/track_latest')
    ap.add_argument('--interface', default='socketcan')
    ap.add_argument('--channel', default='')
    ap.add_argument('--bitrate', default='500000')
    ap.add_argument('--cmd-shm', default='a1_can_guard_cmd')
    ap.add_argument('--hb-shm', default='a1_can_guard_perception_hb')
    ap.add_argument('--ctrl', default=CTRL)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--sil', action='store_true', help='SIL 전용(Enter 없이) — 실차 금지')
    a = ap.parse_args(argv)
    if a.kph > MAX_KPH - 1.0:
        print(f'--kph 는 {MAX_KPH - 1:g} 이하(차속 상한 {MAX_KPH:g} km/h 여유)', file=sys.stderr)
        return 2
    with open(TRACK, encoding='utf-8') as f:
        p = yaml.safe_load(f)['a1_cmd_adapter']['ros__parameters']
    sm = SteerMap(p['steer_map']['tire_deg'], p['steer_map']['wheel_deg'], p['steer_sign'], p['steer_wheel_limit_deg'])
    sides = [{'left': 1, 'right': -1}[x] for x in a.sides.split(',')]
    items = plan([float(x) for x in a.handles.split(',')], sides, a.laps, a.kph, sm)
    print(f'원 돌기 {len(items)} 회차, {a.laps:g} 바퀴, {a.kph:g} km/h — 표는 잠정, 공간 지름은 예상값에 여유를 두고 볼 것')
    for deg, tire, r, t in items:
        print(f'  핸들 {deg:+6.1f}°  예상 타이어 {tire:5.2f}°  지름 {2 * r:5.1f} m  {t:6.1f} s')
    print(f'  합계 약 {sum(x[3] for x in items) / 60:.1f} 분(회차 사이 대기 제외)')
    if a.dry_run:
        return 0
    spec = ';'.join(f'deg={d:g},t={t:g}' for d, _, _, t in items)
    argv2 = [sys.executable, GEN, 'run', PROFILE, '--items', spec, '--var', f'kph={a.kph:g}', '--var', f'vmax={MAX_KPH:g}',
             '--var', 'accmax=100', '--ctrl', a.ctrl, '--run', a.run, '--out', a.out, '--interface', a.interface,
             '--channel', a.channel, '--bitrate', a.bitrate, '--cmd-shm', a.cmd_shm, '--hb-shm', a.hb_shm]
    if a.sil:
        argv2.append('--sil')
    return subprocess.call(argv2)


if __name__ == '__main__':
    sys.exit(main())
