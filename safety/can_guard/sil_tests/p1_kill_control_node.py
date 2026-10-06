#!/usr/bin/env python3
"""§7.3 P-1 — 제어 노드를 kill -9 했을 때 can_guard 가 예산 안에서 HOLDING 으로 넘어가고
0x210 송신이 끊기지 않는지. (진짜 ROS2 제어 노드가 아직 없어 fake_control_node.py 로 대신한다.)

pass 기준(§7.3): "T(watchdog_t) 뒤 한 틱 안에 전이, 송신 연속" + 실차 0x210 전환(2026-10-06) 후 추가 확인:
HOLDING 에서 조향 고정·가속 0·브레이크가 유지값(30 %) 쪽으로 램프.

  python3 p1_kill_control_node.py [--channel vcan0]
"""
import argparse
import os
import signal
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sil_tests._common import (FrameRecorder, check_tx_continuous,  # noqa: E402
                               start_can_guard, start_fake_control_node,
                               start_fake_perception, wait_for_shm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--watchdog-t', type=float, default=0.05)
    ap.add_argument('--period', type=float, default=0.02)
    a = ap.parse_args()

    tag = uuid.uuid4().hex[:8]
    cmd_shm, hb_shm = f'p1_cmd_{tag}', f'p1_hb_{tag}'
    print(f'[P-1] shm={cmd_shm}/{hb_shm} watchdog_t={a.watchdog_t * 1000:.0f}ms period={a.period * 1000:.0f}ms')

    # 넷 다 None 으로 미리 선언 — 아래 어느 줄에서 예외가 나도 finally 가 "그때까지 만들어진 것만" 안전하게
    # 정리한다(실제로 이 스크립트의 초기 버전이 FrameRecorder() 생성 실패로 ctrl/perc 를 유출시킨 적이 있다 —
    # 그 버그의 수정판).
    guard = ctrl = perc = rec = None
    try:
        guard = start_can_guard(cmd_shm, hb_shm, channel=a.channel, watchdog_t=a.watchdog_t, period=a.period)
        # cmd_shm 만 기다리면 안 된다 — can_guard 는 cmd_shm 을 먼저 만들고 hb_shm 을 그 다음에 만드므로,
        # 그 틈에 fake_perception 을 띄우면 HeartbeatChannel.open() 이 FileNotFoundError 로 즉사한다
        # (2026-09-18, soak_8h.py 8h 실행에서 실제로 재현됨 — perception_age 가 계속 None).
        if not wait_for_shm(cmd_shm) or not wait_for_shm(hb_shm):
            sys.exit('[P-1] FAIL — can_guard 가 공유메모리를 안 만듦(시작 실패)')

        ctrl = start_fake_control_node(cmd_shm, period=a.period)
        perc = start_fake_perception(hb_shm)
        rec = FrameRecorder(a.channel)

        print('[P-1] ACTIVE 진입 대기(0.5s)...')
        rec.run_for(0.5)
        active_seen = any(d['acc_is_auto_command'] == 1 for _, fid, d in rec.frames)
        if not active_seen:
            sys.exit('[P-1] FAIL — 0.5s 안에 auto=1(ACTIVE) 프레임을 못 봄')
        print(f'[P-1] ACTIVE 확인({len(rec.frames)}개 프레임 관찰). 제어 노드 kill...')

        kill_t = time.monotonic()
        ctrl.send_signal(signal.SIGKILL)
        ctrl.wait(timeout=2)

        rec.run_for(a.watchdog_t + 0.6)   # T + 브레이크 램프가 보일 만큼(30 %/s × 0.5 s ≈ 15 %) 관찰

        # can_guard 자신의 로그에서 전이 시각을 직접 확인(가장 직접적인 증거)
        guard.terminate()
        try:
            _, stderr = guard.communicate(timeout=2)
        except Exception:
            stderr = ''
        transition_lines = [ln for ln in stderr.splitlines() if 'ACTIVE → HOLDING' in ln]
        print('[P-1] can_guard 로그 중 전이 줄:', transition_lines or '(없음)')

        ok, gaps = check_tx_continuous(rec.frames, a.period)
        print(f'[P-1] 0x210 송신 연속 여부: {ok} (주기 2.5배 넘은 간격 {len(gaps)}개)')
        if gaps:
            print('        예:', gaps[:3])

        # kill 이후 프레임: 조향 고정(값이 안 바뀜), 가속 0, 브레이크가 0 에서 유지값 쪽으로 증가(램프)
        after = [d for t, fid, d in rec.frames if fid == 0x210 and t > kill_t + a.watchdog_t + 2 * a.period]
        hold_ok = False
        if len(after) >= 5:
            steers = {d['steer_command'] for d in after}
            accs = {d['acc_command'] for d in after}
            brakes = [d['break_command'] for d in after]
            hold_ok = len(steers) == 1 and accs == {0} and brakes[-1] > brakes[0] and brakes == sorted(brakes)
            print(f'[P-1] HOLDING 프레임 {len(after)}개: 조향 {sorted(steers)}, 가속 {sorted(accs)}, '
                  f'브레이크 {brakes[0]}% → {brakes[-1]}% (램프)')

        passed = bool(transition_lines) and ok and hold_ok
        print(f'\n[P-1] {"PASS" if passed else "FAIL"}'
              f' (전이 로그={bool(transition_lines)}, 송신 연속={ok}, 조향고정·가속0·브레이크램프={hold_ok})')
        sys.exit(0 if passed else 1)
    finally:
        if rec is not None:
            rec.close()
        for p in (ctrl, perc, guard):
            if p is not None and p.poll() is None:
                p.kill()


if __name__ == '__main__':
    main()
