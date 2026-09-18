#!/usr/bin/env python3
"""§7.3 P-2 — 인지(perception) 를 kill -9 했을 때 can_guard 의 TX 주기가 흔들리지 않는지.
제어 노드는 계속 살려 둔다(P-2 는 인지만의 영향을 본다 — P-1 과 독립적인 조건).

pass 기준(§7.3): "guard TX 주기 영향 없음 — 최대 지터 기록; dmesg BUG 카운트"

  python3 p2_kill_perception.py [--channel vcan0]
"""
import argparse
import os
import signal
import subprocess
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sil_tests._common import (FrameRecorder, check_alive_cnt_continuous,  # noqa: E402
                               start_can_guard, start_fake_control_node,
                               start_fake_perception, wait_for_shm)


def inter_frame_gaps(frames, frame_id=0x156):
    ts = [t for t, fid, _ in frames if fid == frame_id]
    return [b - a for a, b in zip(ts, ts[1:])]


def dmesg_bug_count():
    try:
        out = subprocess.run(['dmesg'], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    keys = ('BUG:', 'Oops', 'Call Trace', 'scheduling while atomic')
    return sum(1 for ln in out.splitlines() if any(k in ln for k in keys))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--period', type=float, default=0.01)
    ap.add_argument('--perception-timeout', type=float, default=0.5)
    a = ap.parse_args()

    tag = uuid.uuid4().hex[:8]
    cmd_shm, hb_shm = f'p2_cmd_{tag}', f'p2_hb_{tag}'
    print(f'[P-2] shm={cmd_shm}/{hb_shm} period={a.period * 1000:.0f}ms '
          f'perception_timeout={a.perception_timeout * 1000:.0f}ms')

    bugs_before = dmesg_bug_count()

    # 넷 다 None 으로 미리 선언 — 아래 어느 줄에서 예외가 나도 finally 가 "그때까지 만들어진 것만" 안전하게
    # 정리한다(P-1 스크립트의 초기 버전이 이 패턴 없이 ctrl/perc 프로세스를 유출시킨 적이 있다).
    guard = ctrl = perc = rec = None
    try:
        guard = start_can_guard(cmd_shm, hb_shm, channel=a.channel, period=a.period,
                                extra_args=['--perception-timeout', str(a.perception_timeout)])
        if not wait_for_shm(cmd_shm):
            sys.exit('[P-2] FAIL — can_guard 가 공유메모리를 안 만듦(시작 실패)')

        ctrl = start_fake_control_node(cmd_shm, period=a.period)
        perc = start_fake_perception(hb_shm)
        rec = FrameRecorder(a.channel)

        print('[P-2] 정상 구간 관찰(0.5s)...')
        rec.run_for(0.5)
        before_gaps = inter_frame_gaps(rec.frames)

        print('[P-2] 인지 프로세스 kill...')
        perc.send_signal(signal.SIGKILL)
        perc.wait(timeout=2)

        print(f'[P-2] kill 이후 관찰({a.perception_timeout + 0.5:.2f}s)...')
        n_before = len(rec.frames)
        rec.run_for(a.perception_timeout + 0.5)
        after_gaps = inter_frame_gaps(rec.frames[n_before:])

        guard.terminate()
        try:
            _, stderr = guard.communicate(timeout=2)
        except Exception:
            stderr = ''
        degraded_lines = [ln for ln in stderr.splitlines() if 'DEGRADED' in ln]
        print('[P-2] can_guard 로그 중 DEGRADED 전이:', degraded_lines or '(없음)')

        ok, gaps = check_alive_cnt_continuous(rec.frames)
        bugs_after = dmesg_bug_count()

        max_before = max(before_gaps) if before_gaps else 0.0
        max_after = max(after_gaps) if after_gaps else 0.0
        print(f'[P-2] 0x156 간격: kill 전 평균/최대 {sum(before_gaps)/len(before_gaps)*1000:.2f}'
              f'/{max_before*1000:.2f}ms, kill 후 평균/최대 '
              f'{sum(after_gaps)/len(after_gaps)*1000:.2f}/{max_after*1000:.2f}ms (목표 주기 {a.period*1000:.0f}ms)')
        print(f'[P-2] Aliv_Cnt 연속 여부: {ok} (끊긴 지점 {len(gaps)}개)')
        print(f'[P-2] dmesg BUG류: kill 전 {bugs_before} → kill 후 {bugs_after}')

        # 주기가 흔들리지 않았다는 기준: 최대 간격이 목표 주기의 3배를 넘지 않음(SIL, RT 우선순위 없이도)
        period_ok = max_before < a.period * 3 and max_after < a.period * 3
        no_new_bugs = (bugs_before is None or bugs_after is None or bugs_after <= bugs_before)
        passed = ok and period_ok and no_new_bugs and bool(degraded_lines)
        print(f'\n[P-2] {"PASS" if passed else "FAIL"}'
              f' (주기 유지={period_ok}, Aliv_Cnt 연속={ok}, 새 BUG 없음={no_new_bugs}, '
              f'DEGRADED 전이={bool(degraded_lines)})')
        sys.exit(0 if passed else 1)
    finally:
        if rec is not None:
            rec.close()
        for p in (ctrl, perc, guard):
            if p is not None and p.poll() is None:
                p.kill()


if __name__ == '__main__':
    main()
