"""경기 모드 연결부 — `--race` 일 때만 can_guard 가 이 모듈을 불러온다(계획서 3·5·8·9절, WP3·WP4).

순수 로직(race_signal·race_state·race_policy·speed_ctrl·recovery·speed_limit)을 한 주기 흐름으로 묶고, 이벤트 로그·StatusChannel 을 맡는다:

  수신    feed(msg) — 0x124·0x125·0x126·0x127·0x128·0x202·0x203·0x710·0x711. `len < 8` 필터보다 먼저 탄다(짧으면 0 패딩)
  주기    begin_cycle() → 깃발·RES·경기 상태·복구 판정 → estop_asserted(ESTOP 원천 `race_res`), state_kwargs() → next_state()
          held_steer() → command_for_state(recovery_coast) → envelope() → clamp → 0x210
          after_tx() → write_status()

**예외를 삼키지 않는다:** 이 모듈 안의 계산에서 예외가 나면 이벤트 `race_internal_error` + traceback 을 남기고 SUSPENDED + ESTOP 으로
보수적으로 전환한다(재시작으로만 빠져나간다, go-reset 도 받지 않음). 메인 루프와 안전 축은 계속 돈다.

**이벤트는 상태가 바뀔 때만 남긴다.** override_on/off 는 경기 상태로만 정해져 차속과 무관하고, input_clamped 는 사유가 `CLAMP_EVENT_HOLD_S` 이상 이어질
때만 남긴다(차속이 문턱 근처에서 흔들려도 주기마다 쏟아지지 않게).
"""
import math
import traceback
from dataclasses import replace

import yaml

from protocol import GUARD_STATE_CODE, STATUS_NONE, GuardStatus, StatusChannel, STATUS_SHM_NAME_DEFAULT
from race_policy import RaceEnvelopeContext, apply_race_envelope
from race_signal import (FRAME_ID_FB_ACC, FRAME_ID_FB_STEER, FRAME_ID_PIT_ZONE, FRAME_ID_RACE_SIGNAL, FRAME_ID_REPAIR,
                         FRAME_ID_RES, FRAME_ID_RES_FB, RaceSignalDecoder, ResMonitor,
                         decode_fb_acc, decode_fb_steer, decode_pit_zone, decode_repair, decode_res_fb)
from race_state import RaceInputs, RaceLimits, RaceParams, RacePhase, RaceState, next_race_state, race_phase_code
from recovery import PHASE_COMMITTED, PHASE_RECOVER, RecoveryParams, RecoveryTracker
from speed_ctrl import SlowController, SlowParams
from state_machine import State

RACE_VS_MAX_AGE_S = 0.1          # 계획서 4.1 — 경기 판단에 쓰는 차속의 나이 상한(안전 축 VS_MAX_AGE_S 와 별개)
CLAMP_EVENT_HOLD_S = 0.5         # input_clamped 사유가 이만큼 이어져야 기록(브리프 세부 보완 g)
_ACTIVE_LIKE = (State.ACTIVE, State.HOLDING, State.DEGRADED)
_NO_MOTION_HOLD_ACTUAL = (RacePhase.PRE_GO, RacePhase.PARKED)   # 모델 조향이 차에 간 적 없는(또는 무시되는) 구간

RX_IDS = (FRAME_ID_RACE_SIGNAL, FRAME_ID_PIT_ZONE, FRAME_ID_REPAIR, FRAME_ID_RES, FRAME_ID_RES_FB,
          FRAME_ID_FB_STEER, FRAME_ID_FB_ACC)


def load_race_flag(path):
    """race_flag.yaml → (dict, Slow Off 램프 %/s). 램프는 양수 또는 `.inf`(램프 없음 → 0 = race_state 의 "램프 없음"). 값이 이상하면 ValueError."""
    with open(path, encoding='utf-8') as f:
        d = yaml.safe_load(f)
    if not isinstance(d, dict) or 'slow_off_ramp_pct_s' not in d:
        raise ValueError(f'{path}: slow_off_ramp_pct_s 없음')
    ramp = float(d['slow_off_ramp_pct_s'])
    if math.isnan(ramp) or ramp <= 0:
        raise ValueError(f'{path}: slow_off_ramp_pct_s 는 양수 또는 .inf ({ramp})')
    return d, (0.0 if math.isinf(ramp) else ramp)


def build_params(a):
    """CLI + race_flag.yaml → (RaceParams, SlowParams, RecoveryParams). 값이 이상하면 ValueError."""
    d, ramp = load_race_flag(a.race_flag_yaml)
    slow = SlowParams.from_yaml_dict(d, slow_target_kph=a.slow_target_kph, slow_acc_cut_kph=a.slow_acc_cut_kph,
                                     slow_brake_kph=a.slow_brake_kph, hold_brake_start_pct=a.hold_brake_pct,
                                     hold_brake_ramp_pct_s=a.hold_brake_ramp_pct_s, hold_brake_max_pct=a.hold_brake_max_pct)
    race = RaceParams(race_stop_action=a.race_stop_action, race_signal_timeout_s=a.race_signal_timeout_s,
                      resume_mode=a.resume_mode, stop_speed_kph=a.stop_speed_kph, pre_go_brake_pct=a.pre_go_brake_pct,
                      slow_target_kph=a.slow_target_kph, slow_acc_cut_kph=a.slow_acc_cut_kph, slow_brake_kph=a.slow_brake_kph,
                      slow_unknown_brake_pct=slow.entry_brake_start_pct, finish_cap_kph=a.finish_cap_kph,
                      parked_speed_kph=a.parked_speed_kph, parked_after_s=a.parked_after_s, parked_brake_pct=a.parked_brake_pct,
                      suspended_brake_pct=a.estop_brake_start_pct, input_grace_s=a.input_grace_s, slow_off_ramp_pct_s=ramp)
    rec = RecoveryParams(budget_m=a.recover_budget_m, delay_s=a.recover_delay_s, alat_max_mps2=a.recover_alat_max,
                         min_radius_m=a.track_min_radius_m, resume_low_speed=a.resume_low_speed)
    return race, slow, rec


class RaceRuntime:
    def __init__(self, a, ev, log):
        self.a = a
        self.ev = ev
        self.log = log
        self.params, slow, rec = build_params(a)
        self.hold_after_s = a.adapter_hold_after_s
        self.watchdog_t = a.watchdog_t
        self.decoder = RaceSignalDecoder(a.race_confirm_frames)
        self.res = ResMonitor(a.race_confirm_frames, a.res_alive_timeout_s)
        self.slow_ctrl = SlowController(slow)
        self.tracker = RecoveryTracker(rec)
        self.state = RaceState()
        self.limits = self._no_motion_limits(RacePhase.PRE_GO, self.params.pre_go_brake_pct)
        self.status_ch = StatusChannel.create(a.status_shm or STATUS_SHM_NAME_DEFAULT)
        self.fatal = False
        self.speed_kph = None
        self.last_acc_pct = 0.0
        self.pit = None
        self._prev_phase = RacePhase.PRE_GO
        self._go_reset_now = False
        self._commit_t = None
        self._healthy_since = None
        self.model_healthy_s = 0.0
        self._override_on = False
        self._slow_seen = False
        self._estop_logged = False
        self._last_res_fb = None
        self._last_repair = None
        self._last_can_err = {}
        self._clamp_shown = None
        self._clamp_cand = None
        self._clamp_cand_t = None

    # --- 수신 --------------------------------------------------------------------------------------------------
    def feed(self, msg, now):
        """이 모듈이 맡는 프레임이면 True(메인 루프는 다음 프레임으로). 짧은 프레임도 0 패딩으로 처리한다."""
        aid = msg.arbitration_id
        if aid not in RX_IDS:
            return False
        try:
            self._feed(aid, msg.data, now)
        except Exception as e:   # noqa: BLE001 — 이유를 남기고 보수적으로 전환(모듈 docstring)
            self._fail(now, 'feed', e)
        return True

    def _feed(self, aid, data, now):
        if aid == FRAME_ID_RACE_SIGNAL:
            self.decoder.feed(data, now)
        elif aid == FRAME_ID_RES:
            if len(data) > 0:   # 빈 프레임을 RES=0 으로 치지 않는다
                self.res.feed(data, now)
        elif aid == FRAME_ID_PIT_ZONE:
            pit = decode_pit_zone(data)
            if pit != self.pit:
                self.ev('race_pit_zone', f'lat={pit[0]:.7f} lon={pit[1]:.7f}', now)
            self.pit = pit
        elif aid == FRAME_ID_REPAIR:
            v = decode_repair(data)
            if v != self._last_repair:
                self.ev('race_repair', f'sig={v}', now)
            self._last_repair = v
        elif aid == FRAME_ID_RES_FB:
            f = decode_res_fb(data)
            if f != self._last_res_fb:
                self.ev('res_fb', f'res_end={f["res_end"]} res_error={f["res_error"]}', now)
            self._last_res_fb = f
        elif aid == FRAME_ID_FB_STEER:
            self._note_can_err('eps', decode_fb_steer(data)['eps_user_can_err'], now)
        elif aid == FRAME_ID_FB_ACC:
            self._note_can_err('acc', decode_fb_acc(data)['acc_user_can_err'], now)

    def _note_can_err(self, axis, value, now):
        """TODO-2 확인용 기록(0x710/0x711 *_USER_CAN_ERR). 판단에 쓰지 않는다."""
        if self._last_can_err.get(axis) != value:
            self.ev('fb_user_can_err', f'{axis}={value}', now)
        self._last_can_err[axis] = value

    # --- 시작 전 점검 ---------------------------------------------------------------------------------------------
    def idle(self, now):
        """시작 전 점검 중 — 조용함 무장을 재고 상태판이 볼 수 있게 상태를 쓴다."""
        try:
            self.decoder.note_silence(now)
            self._flush_records(now)
            self.write_status(State.INIT, now)
        except Exception as e:   # noqa: BLE001
            self._fail(now, 'idle', e)

    # --- 주기 -------------------------------------------------------------------------------------------------
    def begin_cycle(self, now, vs_kph, vs_age_s, cmd_age_s, safety_state, cmd_has_auto, other_estop):
        """프레임 드레인 뒤, next_state 앞. 안전 축 state 는 직전 주기 값."""
        fresh = cmd_age_s is not None and cmd_age_s <= self.watchdog_t
        if fresh and cmd_has_auto:
            self._healthy_since = now if self._healthy_since is None else self._healthy_since
            self.model_healthy_s = now - self._healthy_since
        else:
            self._healthy_since = None
            self.model_healthy_s = 0.0
        self.speed_kph = vs_kph if (vs_age_s is not None and vs_age_s <= RACE_VS_MAX_AGE_S) else None
        self._go_reset_now = False
        if self.fatal:
            return
        try:
            self._step_race(now, fresh, cmd_age_s, safety_state, other_estop)
        except Exception as e:   # noqa: BLE001
            self._fail(now, 'begin_cycle', e)

    def _step_race(self, now, fresh, cmd_age_s, safety_state, other_estop):
        self.decoder.note_silence(now)
        self.res.update(now)
        self._flush_records(now)
        flags = self.decoder.take_events()
        for name in flags:
            t0 = self.decoder.trigger_t(name)
            self.ev('race_signal', f'{name} detect_lag_ms={(now - t0) * 1e3:.1f}' if t0 is not None else name, now)

        prev = self.state
        self._prev_phase = prev.phase
        inputs = RaceInputs(now=now, events=flags, stop_level=self.decoder.levels['stop'], res_active=self.res.active,
                            res_alive_lost=self.res.alive_lost, race_signal_age_s=self.decoder.age_s(now),
                            vehicle_speed_kph=self.speed_kph, last_acc_pct=self.last_acc_pct)
        self.state, self.limits, events = next_race_state(prev, inputs, self.params)
        for name, detail in events:
            self._emit_race_event(name, detail, now)
        self._go_reset_now = prev.phase == RacePhase.SUSPENDED and self.state.phase != RacePhase.SUSPENDED

        if self.limits.long_override != self._override_on:
            self._override_on = self.limits.long_override
            self.ev('override_on' if self._override_on else 'override_off', f'phase={self.state.phase.value}', now)

        # 끊김 복구(계획서 8.2). 출발 대기 중에는 재지 않는다 — 래치가 Go 뒤까지 남으면 안 된다(D11)
        if self.state.phase != RacePhase.PRE_GO and safety_state in _ACTIVE_LIKE:
            gap = (cmd_age_s if cmd_age_s is not None else 0.0) + self.hold_after_s
            d = self.tracker.step(now, fresh, gap, self.speed_kph)
            for name, detail in d.events:
                self.ev(name, detail, now)
            if self.tracker.phase == PHASE_COMMITTED and self._commit_t is None:
                self._commit_t = now
            if self.tracker.phase == PHASE_COMMITTED and safety_state == State.HOLDING:
                suspended = self.state.phase == RacePhase.SUSPENDED
                if self.tracker.try_resume(now, self.model_healthy_s, other_estop, self.res.active, suspended):
                    self._commit_t = None
                    for name, detail in self.tracker.step(now, fresh, gap, self.speed_kph).events:
                        self.ev(name, detail, now)
            if self.tracker.phase != PHASE_COMMITTED:
                self._commit_t = None

    def _emit_race_event(self, name, detail, now):
        if name == 'race_slow':   # 계획서 WP3 이벤트 이름 slow_on / slow_off
            word, _, rest = detail.partition(' ')
            self.ev(f'slow_{word}', rest, now)
        else:
            self.ev(name, detail, now)
        if name in ('race_phase', 'race_slow'):
            self.log(f'[can_guard] 경기 상태: {name} {detail} (차속 {self.speed_kph})')

    def _flush_records(self, now):
        for name, detail in self.decoder.take_records():
            self.ev(name, detail, now)
        for name, detail in self.res.take_records():
            if name == 'res_start':
                self.ev('res', f'start {detail}', now)
            elif name == 'res_end':
                self.ev('res', f'end {detail}', now)
            else:
                self.ev(name, detail, now)

    @property
    def estop_asserted(self):
        """ESTOP 원천 `race_res`(계획서 D4). SUSPENDED 는 래치라 RES 가 풀려도 켜져 있다."""
        return self.fatal or self.state.phase == RacePhase.SUSPENDED

    @property
    def estop_trigger_detail(self):
        if self.fatal:
            return 'race_res internal_error'
        return f'race_res reason={self.state.suspend_reason}'

    @property
    def follow_steer(self):
        """RES·STOP 중 조향은 실제 핸들각을 따라간다(계획서 D4, 5절). 풀린 뒤에는 마지막 실제값에 고정."""
        return self.res.active or (self.state.phase == RacePhase.SUSPENDED and self.state.suspend_reason == 'stop')

    @property
    def coasting(self):
        return self.tracker.phase == PHASE_RECOVER

    def state_kwargs(self, now):
        """next_state 경기 인자(키워드만)."""
        pre_go = self.state.phase == RacePhase.PRE_GO
        committed = None if self._commit_t is None else now - self._commit_t
        return {'pre_go': pre_go, 'recovery_phase': None if pre_go else self.tracker.phase,
                'committed_duration_s': committed,
                'estop_go_reset': self._go_reset_now and self.params.resume_mode == 'go-reset' and not self.fatal}

    def held_steer(self, now, new_state, held_steer_deg, actual_steer_deg):
        """PRE_GO·PARKED 에는 모델 조향이 차에 안 갔다 → 보호 상태로 가거나 Go 뒤 HOLDING 이어도 고정값은 실제 핸들각이어야 계단이 없다."""
        if actual_steer_deg is not None and (self._prev_phase in _NO_MOTION_HOLD_ACTUAL
                                             or self.state.phase in _NO_MOTION_HOLD_ACTUAL):
            return actual_steer_deg
        return held_steer_deg

    def envelope(self, now, out, safety_state, last_auto_cmd, actual_steer_deg, dt_s, steer_limiter, acc_limiter):
        """command_for_state 결과에 경기 제한을 씌운다. 예외면 이 주기는 가속 0 + 브레이크로 보내고 다음 주기부터 ESTOP."""
        before = out
        ctx = RaceEnvelopeContext(safety_state=safety_state, actual_steer_deg=actual_steer_deg,
                                  vehicle_speed_kph=self.speed_kph, last_auto_cmd=last_auto_cmd, slow_ctrl=self.slow_ctrl,
                                  dt_s=dt_s)
        try:
            out = apply_race_envelope(out, self.limits, ctx)
            out = self._apply_resume_cap(now, out)
            self._note_clamp(now, self._clamp_reason(before, out))
        except Exception as e:   # noqa: BLE001
            self._fail(now, 'envelope', e)
            out = self._fallback_command(before, last_auto_cmd, actual_steer_deg)
        if out is not None:
            # 리미터는 "차에 실제로 나간 값"을 기준으로 — 대기 중 모델 값을 따라가 있으면 Go 순간 변화율 제한이 무의미해진다
            steer_limiter.reset(out.steer_cmd_deg)
            acc_limiter.reset(out.acc_cmd_pct)
        return out

    def _apply_resume_cap(self, now, out):
        """8.3 조건 5 — 재개 뒤 상한. 차속을 모르면 가속 0."""
        cap = self.tracker.speed_cap_kph(now)
        if cap is None or out is None:
            return out
        if self.speed_kph is None or self.speed_kph > cap:
            return replace(out, acc_cmd_pct=0.0)
        return out

    def _fallback_command(self, before, last_auto_cmd, actual_steer_deg):
        base = before if (before is not None and (before.steer_auto or before.brake_auto or before.acc_auto)) else last_auto_cmd
        if base is None:
            return None
        steer = actual_steer_deg if actual_steer_deg is not None else base.steer_cmd_deg
        return replace(base, steer_cmd_deg=steer, acc_cmd_pct=0.0,
                       brake_cmd_pct=max(base.brake_cmd_pct, self.params.suspended_brake_pct))

    def _clamp_reason(self, before, after):
        lim = self.limits
        if not lim.allow_motion:
            return f'hold_{lim.phase.value}' if after is not None else None
        if before is None or after is None:
            return None
        if lim.long_override:
            return 'slow_override'
        if after.acc_cmd_pct < before.acc_cmd_pct:
            if lim.acc_cap_pct is not None and after.acc_cmd_pct >= lim.acc_cap_pct:
                return 'slow_off_ramp'
            return 'speed_cap'
        if after.brake_cmd_pct > before.brake_cmd_pct:
            return 'speed_cap'
        return None

    def _note_clamp(self, now, reason):
        if reason == self._clamp_shown:
            self._clamp_cand = None
            return
        if reason != self._clamp_cand or self._clamp_cand_t is None:
            self._clamp_cand, self._clamp_cand_t = reason, now
        if now - self._clamp_cand_t >= CLAMP_EVENT_HOLD_S:
            self.ev('input_clamped', f'{self._clamp_shown or "none"}->{reason or "none"}', self._clamp_cand_t)
            self._clamp_shown = reason
            self._clamp_cand = None

    def after_tx(self, now, out, safety_state):
        """0x210 송신 뒤."""
        if out is not None:
            self.last_acc_pct = out.acc_cmd_pct
        try:
            self.write_status(safety_state, now)
        except Exception as e:   # noqa: BLE001
            self._fail(now, 'after_tx', e)

    def write_status(self, safety_state, now):
        lim = self.limits
        cap = 0.0 if not lim.allow_motion else lim.speed_cap_kph
        resume_cap = self.tracker.speed_cap_kph(now)
        if resume_cap is not None:
            cap = resume_cap if cap is None else min(cap, resume_cap)
        levels = self.decoder.levels
        age = self.decoder.age_s(now)
        self.status_ch.write(GuardStatus(
            race_enabled=True, guard_state=GUARD_STATE_CODE[safety_state.value], race_phase=race_phase_code(self.state),
            slow_active=self.state.slow_active, long_override=lim.long_override,
            sig_go=levels['go'], sig_slow_on=levels['slow_on'], sig_slow_off=levels['slow_off'],
            sig_finish=levels['finish'], sig_stop=levels['stop'],
            res_active=self.res.active, res_alive_ok=self.res.seen and not self.res.alive_lost, input_grace=lim.input_grace,
            speed_cap_kph=STATUS_NONE if cap is None else cap,
            speed_target_kph=STATUS_NONE if lim.speed_target_kph is None else lim.speed_target_kph,
            race_signal_age_s=STATUS_NONE if age is None else age,
            model_healthy_s=self.model_healthy_s,
            pit_lat_deg=self.pit[0] if self.pit else float('nan'), pit_lon_deg=self.pit[1] if self.pit else float('nan')))

    def status_text(self):
        return f' | 경기 {self.state.phase.value}{"+서행" if self.state.slow_active else ""}'

    # --- 고장·정리 --------------------------------------------------------------------------------------------
    def _no_motion_limits(self, phase, brake):
        return RaceLimits(phase=phase, allow_motion=False, hold_brake_pct=brake, input_grace=True)

    def _fail(self, now, where, exc):
        """race 계산 예외 → SUSPENDED + ESTOP(재시작으로만). 한 번만 기록."""
        if self.fatal:
            return
        self.fatal = True
        self.ev('race_internal_error', f'{where}: {type(exc).__name__}: {exc}', now)
        self.log(f'[can_guard] 🛑 경기 계산 예외({where}) — SUSPENDED + ESTOP 으로 전환\n{traceback.format_exc()}')
        self.state = replace(self.state, phase=RacePhase.SUSPENDED, suspend_reason='internal_error')
        self.limits = self._no_motion_limits(RacePhase.SUSPENDED, self.params.suspended_brake_pct)

    def close(self):
        self.status_ch.close()
