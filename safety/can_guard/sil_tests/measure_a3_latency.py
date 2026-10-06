#!/usr/bin/env python3
"""can_guard 의 TX 주기 지터를 잰다 — A-3 레시피(격리 코어+SCHED_FIFO) 전/후 비교용.
eait_tx.py 가 이 레시피로 5~9µs 를 낸 것과 같은 방식의 측정을, can_guard 자체 TX 루프에 대해 한다
(§5.C 의 "ROS2 슬라이스엔 A-3 무효과" 결론과 대칭 — can_guard 는 raw SocketCAN 이라 효과가 있어야 정상).

가짜 제어노드+인지로 ACTIVE 상태를 유지시켜(상태 전이 잡음 제거) 0x210 프레임 간격만 순수하게 잰다.

  python3 measure_a3_latency.py --channel vcan0 --duration 5                          # 기준선(RT 없음)
  python3 measure_a3_latency.py --channel vcan0 --cpu 8 --rt-priority 90 --duration 5  # A-3

--rt-priority 를 주면 `_common.start_can_guard()` 가 can_guard.py 프로세스 **하나만** `sudo chrt` 로 감싼다
(내부적으로 비밀번호를 물어본다 — 터미널에서 직접 입력). 이 스크립트 자신과 가짜 제어노드/인지/관찰자는
일반 우선순위로 남아, 측정 관찰자가 자기 자신도 FIFO 라 경합하는 오염을 피한다. 예전에는 이 스크립트
전체를 `sudo chrt` 로 감쌌는데, fork() 상속 때문에 관찰자까지 덩달아 FIFO 90 이 돼 측정이 오염됐었다
(2026-09-18 발견 — README 참고). can_guard.py 종료 시 stderr 에 찍는 "자체측정(late=now-next_t)" 줄이
eait_tx.py 와 동일한 정의의 진짜 비교 대상이다 — 이 스크립트의 외부 관찰 수치는 참고용으로 같이 본다.
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sil_tests._common import (FrameRecorder, start_can_guard, start_fake_control_node,  # noqa: E402
                               start_fake_perception, wait_for_shm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--period', type=float, default=0.020)
    ap.add_argument('--duration', type=float, default=5.0, help='측정 구간(초), 정착 시간 0.5s 는 별도')
    ap.add_argument('--cpu', default='', help='can_guard 에 넘길 --cpu (격리 코어, A-3)')
    ap.add_argument('--rt-priority', type=int, default=0, help='can_guard 에 넘길 --rt-priority (A-3)')
    a = ap.parse_args()

    import uuid
    tag = uuid.uuid4().hex[:8]
    cmd_shm, hb_shm = f'lat_cmd_{tag}', f'lat_hb_{tag}'
    extra = []
    if a.cpu:
        extra += ['--cpu', a.cpu]
    if a.rt_priority:
        extra += ['--rt-priority', str(a.rt_priority)]

    guard = ctrl = perc = rec = None
    try:
        if a.rt_priority:
            print('[측정] can_guard 를 sudo chrt 로 띄웁니다 — 터미널에 sudo 비밀번호를 물어볼 수 있습니다.',
                  file=sys.stderr)
        guard = start_can_guard(cmd_shm, hb_shm, channel=a.channel, period=a.period, extra_args=extra)
        shm_timeout = 30.0 if a.rt_priority else 2.0   # 비밀번호 입력 시간까지 기다려야 함
        # cmd_shm 만 기다리면 안 된다 — hb_shm 은 그 다음에 만들어져서, 그 틈에 fake_perception 을 띄우면
        # HeartbeatChannel.open() 이 즉사한다(2026-09-18, soak_8h.py 8h 실행에서 실제로 재현됨).
        if not (wait_for_shm(cmd_shm, timeout=shm_timeout) and wait_for_shm(hb_shm, timeout=shm_timeout)):
            guard.kill()
            try:
                _, stderr = guard.communicate(timeout=2)
            except Exception:
                stderr = '(stderr 회수 실패)'
            sys.exit(f'FAIL — can_guard 시작 실패 ({shm_timeout:.0f}초 대기함)\n--- can_guard stderr ---\n{stderr}')
        ctrl = start_fake_control_node(cmd_shm, period=a.period)
        perc = start_fake_perception(hb_shm)
        rec = FrameRecorder(a.channel)

        rec.run_for(0.5)   # ACTIVE 정착 대기(측정에서 뺀다)
        rec.frames.clear()
        rec.run_for(a.duration)

        guard.terminate()
        try:
            _, stderr = guard.communicate(timeout=2)
        except Exception:
            stderr = ''
        env_line = next((ln for ln in stderr.splitlines() if '실행 환경' in ln), '(실행 환경 로그 없음)')

        ts = [t for t, fid, _ in rec.frames if fid == 0x210]
        gaps = [b - a_ for a_, b in zip(ts, ts[1:])]
        if not gaps:
            sys.exit('FAIL — 프레임을 못 받음')
        dev = [abs(g - a.period) for g in gaps]   # 목표 주기로부터의 편차 — eait_tx.py 와 같은 지표
        print(env_line)
        print(f'채널={a.channel} 목표주기={a.period*1000:.1f}ms 표본={len(gaps)}개({a.duration:.1f}s)')
        print(f'실제 간격    평균={statistics.mean(gaps)*1e6:8.1f}µs  '
              f'표준편차={statistics.pstdev(gaps)*1e6:8.1f}µs  '
              f'최소={min(gaps)*1e6:8.1f}µs  최대={max(gaps)*1e6:8.1f}µs')
        print(f'주기 편차(|실제-목표|)  평균={statistics.mean(dev)*1e6:8.1f}µs  '
              f'최대={max(dev)*1e6:8.1f}µs   ← eait_tx.py 의 "평균 5 / 최대 9 µs" 와 같은 정의')
    finally:
        if rec is not None:
            rec.close()
        for p in (ctrl, perc, guard):
            if p is not None and p.poll() is None:
                p.kill()


if __name__ == '__main__':
    main()
