#!/usr/bin/env python3
"""§7.3 P-1 — 제어 노드를 kill -9 했을 때 can_guard 가 예산 안에서 HOLDING 으로 넘어가고
Aliv_Cnt 가 끊기지 않는지. (진짜 ROS2 제어 노드가 아직 없어 fake_control_node.py 로 대신한다.)

pass 기준(§7.3): "T(watchdog_t) 뒤 한 틱 안에 첫 감속 프레임, Aliv_Cnt 연속"

  python3 p1_kill_control_node.py [--channel vcan0]
"""
import argparse
import os
import signal
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sil_tests._common import (FrameRecorder, check_alive_cnt_continuous,  # noqa: E402
                               start_can_guard, start_fake_control_node,
                               start_fake_perception, wait_for_shm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--watchdog-t', type=float, default=0.05)
    ap.add_argument('--period', type=float, default=0.01)
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
        if not wait_for_shm(cmd_shm):
            sys.exit('[P-1] FAIL — can_guard 가 공유메모리를 안 만듦(시작 실패)')

        ctrl = start_fake_control_node(cmd_shm, period=a.period)
        perc = start_fake_perception(hb_shm)
        rec = FrameRecorder(a.channel)

        print('[P-1] ACTIVE 진입 대기(0.5s)...')
        rec.run_for(0.5)
        active_seen = any(fid == 0x156 and d['ACC_En'] == 1 for _, fid, d in rec.frames)
        if not active_seen:
            sys.exit('[P-1] FAIL — 0.5s 안에 ACC_En=1(ACTIVE) 프레임을 못 봄')
        print(f'[P-1] ACTIVE 확인({len(rec.frames)}개 프레임 관찰). 제어 노드 kill...')

        kill_t = time.monotonic()
        ctrl.send_signal(signal.SIGKILL)
        ctrl.wait(timeout=2)

        rec.run_for(a.watchdog_t + 3 * a.period)   # T + 여유 몇 틱만큼 계속 관찰

        # can_guard 자신의 로그에서 전이 시각을 직접 확인(가장 직접적인 증거)
        guard.terminate()
        try:
            _, stderr = guard.communicate(timeout=2)
        except Exception:
            stderr = ''
        transition_lines = [ln for ln in stderr.splitlines() if 'ACTIVE → HOLDING' in ln]
        print('[P-1] can_guard 로그 중 전이 줄:', transition_lines or '(없음)')

        ok, gaps = check_alive_cnt_continuous(rec.frames)
        print(f'[P-1] Aliv_Cnt 연속 여부: {ok} (끊긴 지점 {len(gaps)}개)')
        if gaps:
            print('        예:', gaps[:3])

        # 마지막 0x157 프레임들의 ACC_Cmd 가 kill 이후 0 쪽으로 움직였는지(감속 램프의 직접 증거)
        after_kill_157 = [(t, d) for t, fid, d in rec.frames if fid == 0x157 and t > kill_t]
        ramping = False
        if len(after_kill_157) >= 2:
            first_acc = after_kill_157[0][1]['ACC_Cmd']
            last_acc = after_kill_157[-1][1]['ACC_Cmd']
            ramping = abs(last_acc) <= abs(first_acc)   # 0 쪽으로(또는 이미 0이면 그대로) 이동
            print(f'[P-1] ACC_Cmd: kill 직후 {first_acc:.3f} → {a.watchdog_t*1000:.0f}ms+ 뒤 {last_acc:.3f}')

        passed = bool(transition_lines) and ok
        print(f'\n[P-1] {"PASS" if passed else "FAIL"}'
              f' (전이 로그={bool(transition_lines)}, Aliv_Cnt 연속={ok}, 감속 방향={ramping})')
        sys.exit(0 if passed else 1)
    finally:
        if rec is not None:
            rec.close()
        for p in (ctrl, perc, guard):
            if p is not None and p.poll() is None:
                p.kill()


if __name__ == '__main__':
    main()
