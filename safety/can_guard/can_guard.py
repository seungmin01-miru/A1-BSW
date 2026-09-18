#!/usr/bin/env python3
"""
can_guard — 안전 임계 CAN TX 메인 루프 (§7.2, can_stack_development.md §5.D).

  python3 can_guard.py --channel vcan0
  sudo python3 can_guard.py --channel can0 --cpu 8 --rt-priority 90   # A-3 레시피(§5.C 에서 효과 실측됨)

매 주기(기본 10ms = 0x156/0x157 스펙 주기):
  CommandChannel.read() → state_machine.next_state() → command_policy.command_for_state()
  → plausibility.clamp_range()(방어 이중화) → tx_encode → bus.send(0x156), bus.send(0x157)

TODO(남은 작업, README 참고): 인지 하트비트는 --hb-shm 로 채널만 열어 두고 실제 값은 아직 아무도 안 씀
(인지 프로세스 쪽 구현 전) — 지금은 perception_age 가 항상 None 이라 상태머신이 DEGRADED 로 자주 갈 것이다,
이건 버그가 아니라 "인지 프로세스가 아직 없다"는 사실을 정직하게 반영한 것이다.
"""
import argparse
import sys
import time

import can

import sd_notify
from command_policy import command_for_state
from plausibility import RateLimiter, clamp_range
from protocol import Command, CommandChannel, HeartbeatChannel, TornReadError
from rt_setup import apply as apply_rt
from rt_setup import parse_cpulist
from state_machine import DEFAULT_PERCEPTION_TIMEOUT_S, DEFAULT_STOPPED_AFTER_S, DEFAULT_WATCHDOG_T_S
from state_machine import State, next_state
from tx_encode import encode_0x156, encode_0x157


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--channel', default='vcan0')
    ap.add_argument('--interface', default='socketcan')
    ap.add_argument('--period', type=float, default=0.010, help='TX 주기(초) — DBC 스펙 10ms')
    ap.add_argument('--cpu', default='', help='격리 코어 배치, 예 "8" (A-3)')
    ap.add_argument('--rt-priority', type=int, default=0, help='SCHED_FIFO 우선순위, 0=미적용')
    ap.add_argument('--watchdog-t', type=float, default=DEFAULT_WATCHDOG_T_S, help='명령 staleness 임계(초)')
    ap.add_argument('--stopped-after', type=float, default=DEFAULT_STOPPED_AFTER_S,
                    help='HOLDING 이 이만큼 지속되면 STOPPED')
    ap.add_argument('--perception-timeout', type=float, default=DEFAULT_PERCEPTION_TIMEOUT_S)
    ap.add_argument('--eps-rate-limit', type=float, default=None, help='deg/s, 미정이면 무제한(§5.D 참고)')
    ap.add_argument('--acc-rate-limit', type=float, default=None, help='(m/s^2)/s, 미정이면 무제한')
    ap.add_argument('--cmd-shm', default=None)
    ap.add_argument('--hb-shm', default=None)
    ap.add_argument('--max-cycles', type=int, default=0, help='0=무한. SIL 시험용으로 유한 실행할 때 사용')
    ap.add_argument('--quiet', action='store_true')
    return ap.parse_args(argv)


def run(a):
    cpus = parse_cpulist(a.cpu)
    notes = apply_rt(cpus, a.rt_priority)
    log = (lambda *args, **kw: None) if a.quiet else (lambda *args, **kw: print(*args, **kw))
    log(f'[can_guard] 실행 환경: {", ".join(notes) if notes else "기본값"}', file=sys.stderr)

    cycle = 0
    cmd_ch = hb_ch = bus = None
    try:
        cmd_kwargs = {'name': a.cmd_shm} if a.cmd_shm else {}
        hb_kwargs = {'name': a.hb_shm} if a.hb_shm else {}
        cmd_ch = CommandChannel.create(**cmd_kwargs)   # guard 가 먼저 뜬다 → 세그먼트도 guard 가 만든다(§7.2)
        hb_ch = HeartbeatChannel.create(**hb_kwargs)
        bus = can.Bus(channel=a.channel, interface=a.interface)
        log(f'[can_guard] 연결: {a.interface}:{a.channel}, 주기 {a.period * 1000:.1f}ms', file=sys.stderr)

        state = State.INIT
        aliv_cnt = 0
        last_valid_cmd = Command()
        last_cmd_mono = None       # 마지막으로 "성공 확인된" 명령 timestamp 의 monotonic 절대시각
        last_hb_mono = None
        holding_entered_at = None
        held_eps_cmd = 0.0
        eps_limiter = RateLimiter(a.eps_rate_limit)
        acc_limiter = RateLimiter(a.acc_rate_limit)

        sd_notify.ready()
        log(f'[can_guard] INIT — 안전 상태로 시작 (watchdog_t={a.watchdog_t * 1000:.0f}ms)', file=sys.stderr)

        next_t = time.monotonic()
        while a.max_cycles == 0 or cycle < a.max_cycles:
            now = time.monotonic()
            if now < next_t:
                time.sleep(next_t - now)
                now = time.monotonic()

            # --- 1. 채널 읽기 (실패해도 마지막으로 확인된 절대시각 기준으로 나이를 계속 계산) ---
            try:
                cmd, cmd_age = cmd_ch.read()
            except TornReadError:
                cmd, cmd_age = None, None
            if cmd is not None:
                last_valid_cmd = cmd
                last_cmd_mono = now - cmd_age
            cmd_age_s = (now - last_cmd_mono) if last_cmd_mono is not None else None

            try:
                hb_age = hb_ch.age()
            except TornReadError:
                hb_age = None
            if hb_age is not None:
                last_hb_mono = now - hb_age
            perception_age_s = (now - last_hb_mono) if last_hb_mono is not None else None

            # --- 2. 상태 전이 ---
            holding_duration_s = (now - holding_entered_at) if holding_entered_at is not None else 0.0
            new_state = next_state(state, cmd_age_s, holding_duration_s, perception_age_s,
                                   watchdog_t_s=a.watchdog_t, stopped_after_s=a.stopped_after,
                                   perception_timeout_s=a.perception_timeout)
            if new_state != state:
                log(f'[can_guard] {state.value} → {new_state.value}'
                    f' (cmd_age={cmd_age_s}, perception_age={perception_age_s})', file=sys.stderr)
                sd_notify.status(new_state.value)
            if new_state == State.HOLDING and state != State.HOLDING:
                holding_entered_at = now
                held_eps_cmd = last_valid_cmd.eps_cmd   # 진입 순간 조향각을 얼린다
            elif new_state != State.HOLDING:
                holding_entered_at = None
            state = new_state

            # --- 3. 명령 생성 → 방어 클램프 → 인코딩 → 전송 ---
            out = command_for_state(state, last_valid_cmd, held_eps_cmd, eps_limiter, acc_limiter, a.period)
            violations = []
            out = clamp_range(out, violations_out=violations)
            if violations and not a.quiet:
                log(f'[can_guard] plausibility 위반 {len(violations)}건: {violations}', file=sys.stderr)

            bus.send(can.Message(arbitration_id=0x156, data=encode_0x156(out, aliv_cnt), is_extended_id=False))
            bus.send(can.Message(arbitration_id=0x157, data=encode_0x157(out), is_extended_id=False))
            aliv_cnt = (aliv_cnt + 1) % 256

            sd_notify.watchdog()
            cycle += 1
            next_t += a.period
    finally:
        if bus is not None:
            bus.shutdown()
        if cmd_ch is not None:
            cmd_ch.close()
        if hb_ch is not None:
            hb_ch.close()
        log(f'[can_guard] 종료 — {cycle} 주기', file=sys.stderr)


def main():
    run(parse_args())


if __name__ == '__main__':
    main()
