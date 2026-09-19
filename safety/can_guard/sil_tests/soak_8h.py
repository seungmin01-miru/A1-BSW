#!/usr/bin/env python3
"""§7.3 P-9 의 SIL 근사판 — can_guard 를 A-3 조건(격리 코어+SCHED_FIFO)으로 오래 돌려 지연 누적과
상태머신 안정성(스푸리어스 전이 없는지)을 함께 본다. 실 8h 벤치는 실 can0/can1 이 필요(5-1 이후) — 이건
vcan0 SIL 버전.

  python3 soak_8h.py --channel vcan0 --hours 8 --cpu 8 --rt-priority 90

시작하자마자 sudo 비밀번호를 물어볼 수 있다(can_guard 프로세스 하나만 A-3 로 감쌈, README 참고) —
그때 입력할 것. 8시간 내내 열어 둘 터미널이 필요하다 — SSH 라면 연결이 끊기면 죽으니 tmux/screen 권장.
Ctrl+C 로 중간에 끝내도 그때까지 로그를 파싱해 같은 형식으로 요약을 출력한다.

can_guard 의 stderr 는 (measure_a3_latency.py 등 다른 sil_tests 스크립트와 달리) 파이프가 아니라
파일로 직접 리다이렉트한다 — 장시간 실행 중 아무도 안 읽는 파이프가 가득 차면 can_guard 의 print() 가
블록될 수 있는데, 이건 실시간 루프 안에서 절대 있으면 안 되는 일이다(격리 코어+SCHED_FIFO 로 막으려는
바로 그 종류의 지연을 로깅 코드가 스스로 만드는 꼴).
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sil_tests._common import (can_guard_cmd, start_fake_control_node,  # noqa: E402
                               start_fake_perception, wait_for_shm)
from sil_tests.p2_kill_perception import dmesg_bug_count  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--period', type=float, default=0.010)
    ap.add_argument('--hours', type=float, default=8.0)
    ap.add_argument('--cpu', default='8', help='격리 코어 (A-3), 빈 문자열이면 미적용')
    ap.add_argument('--rt-priority', type=int, default=90, help='0 이면 RT 미적용(상태머신 안정성만 관찰)')
    ap.add_argument('--stats-interval-s', type=float, default=60.0, help='지연 스냅샷 로그 간격')
    ap.add_argument('--log', default=None, help='기본: sil_tests/soak_8h_<타임스탬프>.log')
    a = ap.parse_args()

    log_path = a.log or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     f'soak_8h_{time.strftime("%Y%m%d_%H%M%S")}.log')
    max_cycles = round(a.hours * 3600 / a.period)

    extra = ['--stats-interval-s', str(a.stats_interval_s), '--max-cycles', str(max_cycles)]
    if a.cpu:
        extra += ['--cpu', a.cpu]
    if a.rt_priority:
        extra += ['--rt-priority', str(a.rt_priority)]

    cmd_shm, hb_shm = 'soak8h_cmd', 'soak8h_hb'
    cmd = can_guard_cmd(cmd_shm, hb_shm, channel=a.channel, period=a.period, extra_args=extra)

    bugs_before = dmesg_bug_count()
    print(f'[soak] can_guard {a.hours:.1f}h 목표({max_cycles} 주기), 로그: {log_path}')
    if a.rt_priority:
        print('[soak] sudo 비밀번호를 물어볼 수 있습니다 — 입력해 주세요.', file=sys.stderr)

    guard = ctrl = perc = None
    interrupted = False
    ctrl_restarts = perc_restarts = 0
    t_start = time.monotonic()
    try:
        with open(log_path, 'w') as logf:
            guard = subprocess.Popen(cmd, stderr=logf, stdout=subprocess.DEVNULL)
            # cmd_shm 만 기다리면 안 된다 — can_guard 는 cmd_shm 을 먼저 만들고 hb_shm 을 그 다음에 만드므로,
            # 그 틈에 fake_perception 을 띄우면 HeartbeatChannel.open() 이 FileNotFoundError 로 즉사한다
            # (2026-09-18, 첫 8h 시도에서 실제로 재현 — perception_age 가 계속 None → 가짜 INIT → DEGRADED).
            if not (wait_for_shm(cmd_shm, timeout=30.0) and wait_for_shm(hb_shm, timeout=30.0)):
                sys.exit(f'[soak] FAIL — can_guard 시작 실패 (로그 확인: {log_path})')
            ctrl = start_fake_control_node(cmd_shm, period=a.period)
            perc = start_fake_perception(hb_shm)
            print(f'[soak] 시작됨 (PID guard={guard.pid} ctrl={ctrl.pid} perc={perc.pid}) — 목표 {a.hours:.1f}h, '
                  f'Ctrl+C 로 중단 가능')

            # 가짜 노드가 8h 도중 죽으면 그냥 포기하지 않고 재시작한다 — 안 그러면 초반 한 번의 우연한
            # 실패로 나머지 몇 시간을 전부 "인지 없음" 상태로 날리게 된다(이번에 실제로 겪음).
            while guard.poll() is None:
                time.sleep(5.0)
                if ctrl is not None and ctrl.poll() is not None:
                    ctrl_restarts += 1
                    print(f'[soak] 가짜 제어노드가 죽어서 재시작({ctrl_restarts}번째, exit={ctrl.returncode})',
                          file=sys.stderr)
                    ctrl = start_fake_control_node(cmd_shm, period=a.period)
                if perc is not None and perc.poll() is not None:
                    perc_restarts += 1
                    print(f'[soak] 가짜 인지 프로세스가 죽어서 재시작({perc_restarts}번째, exit={perc.returncode})',
                          file=sys.stderr)
                    perc = start_fake_perception(hb_shm)
    except KeyboardInterrupt:
        interrupted = True
        print('\n[soak] Ctrl+C — can_guard 를 정상 종료시키는 중...', file=sys.stderr)
        if guard is not None:
            guard.terminate()
    finally:
        if guard is not None and guard.poll() is None:
            try:
                guard.wait(timeout=10)
            except Exception:
                guard.kill()
        for p in (ctrl, perc):
            if p is not None and p.poll() is None:
                p.kill()

    elapsed_h = (time.monotonic() - t_start) / 3600
    bugs_after = dmesg_bug_count()

    text = ''
    try:
        with open(log_path) as f:
            text = f.read()
    except Exception:
        pass
    lines = text.splitlines()
    transitions = [ln for ln in lines if '→' in ln]
    final_line = next((ln for ln in reversed(lines) if '종료 —' in ln), None)

    print('\n' + '=' * 60)
    print(f'[soak] {"중단(Ctrl+C)" if interrupted else "완료"} — 실제 경과 {elapsed_h:.2f}h')
    print(f'[soak] 가짜 노드 재시작: 제어 {ctrl_restarts}번 / 인지 {perc_restarts}번')
    if transitions:
        print(f'[soak] 상태 전이 {len(transitions)}건(스푸리어스 여부 직접 판단 필요):')
        for ln in transitions:
            print('   ' + ln)
    else:
        print('[soak] 상태 전이 0건 — 스푸리어스 전이 없음')
    print(f'[soak] can_guard 자체측정 최종: {final_line or f"(못 찾음 — 로그 직접 확인: {log_path})"}')
    bug_note = ''
    if bugs_before is not None and bugs_after is not None and bugs_after > bugs_before:
        bug_note = ' — 새로 발생!'
    print(f'[soak] dmesg BUG류: 시작 전 {bugs_before} → 종료 후 {bugs_after}{bug_note}')
    print(f'[soak] 전체 로그: {log_path}')
    print('=' * 60)


if __name__ == '__main__':
    main()
