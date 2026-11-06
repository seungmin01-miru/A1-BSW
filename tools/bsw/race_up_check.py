#!/usr/bin/env python3
"""BSW 시작 점검(지시서 WP7) — /race/state 가 나오는지, 0x202 현재값, 지금 쓰는 long_map 버전·source 를 보여 준다. 읽기만 한다.

  python3 tools/bsw/race_up_check.py <저장소 루트> [--timeout 5]    # bsw_up 이 부른다. 종료 코드 0 = 발행 확인, 1 = 못 받음
  python3 tools/bsw/race_up_check.py <저장소 루트> --map-only        # 표 버전 줄만(ROS 없이)

차량으로 아무것도 보내지 않는다. 0x203 도 보내지 않는다(BSW 는 표시만).
"""
import argparse
import os
import sys
import time

import yaml


def map_lines(repo, track=None):
    """track.yaml 의 long_map_file → 표 버전·source 줄."""
    track = track or os.path.join(repo, 'ros2_ws', 'src', 'a1_control_adapter', 'config', 'track.yaml')
    with open(track, encoding='utf-8') as f:
        p = yaml.safe_load(f)['a1_cmd_adapter']['ros__parameters']
    rel = p.get('long_map_file', '')
    lines = [f'조향 표: {p.get("map_version", "?")}']
    if not rel:
        return lines + ['종방향 표: 1차원(long_map_file 없음)']
    path = rel if os.path.isabs(rel) else os.path.join(repo, rel)
    with open(path, encoding='utf-8') as f:
        m = yaml.safe_load(f)['long_map']
    return lines + [f'종방향 표: long_map v{m["version"]} — source {m["source"]}']


def describe(m):
    """RaceState → 한 줄씩."""
    flags = [n for n in ('go', 'slow_on', 'slow_off', 'finish', 'stop') if getattr(m, f'sig_{n}')]
    lim = m.vehicle_speed_limit_kph
    return [f'/race/state 발행 확인 — 깃발 {",".join(flags) or "없음"}, 마지막 {m.last_signal}, '
            f'0x124 {"수신 없음" if m.race_signal_age_s < 0 else f"{m.race_signal_age_s:.2f} s 전"}',
            f'RES {"작동 중" if m.res_active else "없음"}, 0x127 {"수신 없음" if m.res_age_s < 0 else f"{m.res_age_s:.2f} s 전"}, '
            f'앱 {"잡음" if m.remote_app_active else "안 잡음"}',
            f'0x202 차량 속도 제한 {"수신 없음" if lim < 0 else f"{lim:.1f} km/h"} (표시만, 0x203 송신 없음)']


def wait_state(timeout):
    import rclpy
    from a1_can_msgs.msg import RaceState
    from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
    rclpy.init()
    node = rclpy.create_node('race_up_check')
    box = []
    qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(RaceState, '/race/state', box.append, qos)
    end = time.monotonic() + timeout
    while not box and time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
    node.destroy_node()
    rclpy.shutdown()
    return box[0] if box else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('repo')
    ap.add_argument('--timeout', type=float, default=5.0)
    ap.add_argument('--map-only', action='store_true')
    a = ap.parse_args(argv)
    try:
        lines = map_lines(a.repo)
    except (OSError, KeyError, TypeError, yaml.YAMLError) as e:
        print(f'  ❌ 표를 읽을 수 없음: {e}')
        return 1
    if a.map_only:
        print('\n'.join(lines))
        return 0
    for ln in lines:
        print(f'  ✅ {ln}')
    m = wait_state(a.timeout)
    if m is None:
        print(f'  ❌ /race/state 를 {a.timeout:g} s 안에 못 받음 — race_state_publisher 확인')
        return 1
    for ln in describe(m):
        print(f'  ✅ {ln}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
