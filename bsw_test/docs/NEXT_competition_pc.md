# 다음 할 일 — 대회 차량 PC 세션 (2026-11-04 작성, 개발 PC 에서 넘김)

> **2026-11-06: 자율 U턴·v2 하루 순서 폐기.** 실차 절차는 [`docs/2026-11-06_engine_room_dev_plan.md`](../../docs/2026-11-06_engine_room_dev_plan.md) 4절. 아래 0절 표의 U턴 항목과 4절 U턴 한계는 11-04 기록으로만 남긴다. 코드(`uturn`, `until_heading`, `--turn`)는 제거됨. 개발 PC vcan 시험은 2026-11-06 통과(`00_SAF_estop/results/sil_20261106_222201_junhyeok-960QHA/`, 참고값) — 판정은 대회 PC `run_sil_check.sh --rt auto --track` + `r_race.py --interface socketcan --channel vcan0 --latency 300 --load`.

## ▶ 대회 PC 를 켜면 먼저 이것 (2026-11-07)

```bash
cd ~/git/A1-BSW && git fetch && git checkout claude/ecstatic-keller-uhtbhe && git pull
bash bsw_test/09_ENGINE_ROOM/run_verify.sh      # 약 30~40분, sudo 비밀번호 1회. 차량·실제 CAN 에 안 닿는다
```

- **Claude 세션:** 이 파일을 읽었으면 사용자에게 위 명령을 `!` 를 붙여 실행하도록 안내한다(`bsw_up`·`bsw_down` 이 들어 있어 Claude 가 직접 실행할 수 없다). 끝나면 `bsw_test/09_ENGINE_ROOM/results/verify_*/VERDICT.md` 를 읽고 실패 항목을 고친다.
- **판정:** `VERDICT.md` 제목이 "✅ 전부 통과 — **판정**" 이면 끝. 끝에 나오는 `git add`·`commit` 으로 기록한다. 내용은 [`09_ENGINE_ROOM/README.md`](../09_ENGINE_ROOM/README.md).
- **그다음:** 실차 점검표 [`09_ENGINE_ROOM/docs/checklist.md`](../09_ENGINE_ROOM/docs/checklist.md). 아래 1절의 개별 SIL 명령은 이 묶음에 들어갔다.

> 대회 PC 의 Claude 세션은 **이 파일을 먼저 읽고** 1절부터 차례로 진행한다. 끝난 항목은 지우지 말고 `[x]` + 날짜.
> 기준: [`CLAUDE.md`](../../CLAUDE.md), 현장 순서 [`FIELD_MANUAL.md` 5-0절](../FIELD_MANUAL.md), 입력 [`FIELD_CHEATSHEET.txt` "★ v2"](../FIELD_CHEATSHEET.txt).

## 0. 오늘 바뀐 것 (개발 PC, 커밋 해시는 `git log -1`)

| 항목 | 내용 | 파일 |
|---|---|---|
| 결정(사용자) | 직선 **50 m**(앱 신호 거리), 01 기준 50 m 횡편차 ≤ **0.125 m**(제곱 환산, 불감대 주의 — 01 goal.md), 왕복 = **자율 U턴**(늘 좌), 05 원 유지, 하루 안에 00~07 | `CLAUDE.md` 1.4·6절, 각 `goal.md` |
| 세션 모드 | 한 세션 여러 회차 `--reps` / `--sweep 이름=값,…` / `--first-rep`, 회차 사이 `--turn uturn.yaml`. 회차 사이에도 브레이크 100 % 명령을 계속 써서 can_guard 는 ACTIVE 유지 | `bsw_test/common/scripts/profile_gen.py` |
| 새 구간 | `speed`(폐루프 목표 차속), `until_dist`, `until_heading`, `until_stop`, `uturn` 매크로, `course:`(이동 + 예측 정지거리 ≥ 길이면 끝내기) | `profile_lib.py` |
| 속도 제어기 | `SpeedController` — 0x201 후륜 차속 PI + 정속 유지 % 표 + accel/brake_map 피드포워드, 가속·브레이크 동시 0, 정수 %. **파라미터 전부 잠정**(SIL 가짜 차량 기준) | `profile_lib.py`, `02_LON_profile/ctrl/speed_ctrl.yaml` |
| 안전 추가 | 출발 안 함 5 s → 그 회차 끝 / can_guard 개입(ESTOP·HOLDING: 0x210 이 명령과 다름 5주기) → 세션 중단 / `--ctrl` 없이 폐루프면 경고 | `profile_gen.py` |
| 조향 오프셋 | `--steer-offset`(핸들 °), field_env `ofs <°>` → `gen`·`chk` 가 자동으로 붙임. U턴 lock 은 150 − 오프셋으로 자동 축소 | `field_env.sh` |
| 새 프로파일 | `uturn`, `align_straight`, `speed_step`, `decel_ramp`, `steer_circle_speed`, `steer_spiral_speed`. 기존 `acc_step`·`brake_step`·`steer_sine` 에 `course: 50` | `02_LON_profile/profiles/` |
| 가짜 차량 | `--physics`(변환표 기반 가감속·불감대 5 %·저항·지연, 자전거 모델 자세 `--pose-log`). 기본 동작은 그대로 | `sil/vcan/fake_a1_vehicle.py` |
| SIL | `sil_session.py` A(폐루프+U턴)·B(개루프 50 m+U턴)·C(출발 안 함) — `run_sil_check.sh` 에 `sil_02_session` 추가 | `02_LON_profile/scripts/sil_session.py` |

**개발 PC 결과(참고만, 판정에 안 씀)**: python 3.10.12 · python-can 4.6.1 · cantools 44.0.0 venv 에서 단위 38 PASS, `run_sil_check.sh` 전체 PASS
(E1~E6, sil_02_profile, sil_02_session A/B/C). 가짜 차량에서 0→10 km/h 최고 9.9 km/h(오버슈트 없음), 마지막 3 s 평균 오차 0.8 km/h, U턴 174~191°.

## 0-1. 2026-11-05 추가 (개발 PC — 앱 비상정지 재정의·도구 정비, 보고 `reports/2026-11-05_bsw_update_report/`)

| 항목 | 내용 | 파일 |
|---|---|---|
| 00 재정의 | 앱 = 원격 인수(En 동안 0x210 무시), 경로 A(우리)/B(앱)/C(원격 유닛) 로 기준 적용 분리. 업체 질문 Q1~Q5 | `00_SAF_estop/docs/goal.md` 0절 |
| can_guard | ESTOP 중 앱 En 동안 조향 고정값 = 0x200 실제값(`--estop-remote-steer follow` 기본), 실행 중 다른 0x210 → 이벤트 `foreign_0x210`, `--help` `%` 오류 | `safety/can_guard/` |
| SIL | E2 신호를 실제 can_guard 파이썬에 보냄(`--rt` FAIL 원인), **E7 추가**(원격 우선 조향 인계) | `sil_tests/e_estop.py` |
| bringup | `sudo -n setsid -f chrt …` — sudo 를 setsid 밖에서(11-04 비밀번호 재요구), 기동 뒤 SCHED_FIFO·CPU 를 커널에서 확인해 출력 | `tools/track_day/bringup.sh` |
| 브리지 | spin_once 0.2 s 마다, 커널 ID 필터 `ids`(launch 기본 0x200/0x201/0x210), 5 s 마다 밀림 경고 | `a1_can_bridge` (**colcon build 필요**) |
| 분석 | `fit_maps.py` 기본 헤더 stamp(`--time bag` = 예전), `extract.py` 열 `t`=헤더·`t_bag`. 00 `analysis.py` 새로 | — |

**개발 PC 결과(참고):** can_guard 단위 98 PASS, profile_lib 38 PASS, in-process(virtual 버스) E7 로직 확인(follow +5.0° 유지 / hold −49.8° 고정),
bench_bridge 프레임당 30 → 1.8 µs. vcan·sudo 없어 E1~E7 프로세스 시험·bringup RT·bag 지연은 **못 돌림 → 아래 1절에서 대회 PC 로**.

## 1. 대회 PC 에서 할 일 (현장 출발 전)

- [ ] (11-05) `git pull` → `cd ros2_ws && colcon build --packages-select a1_can_bridge` (브리지 수정 반영)
- [ ] (11-05) `! bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto --track` → **E1~E7 전부 PASS**(E2 는 11-04 FAIL 하던 것, E7 새것)
- [ ] (11-05) `! bash tools/track_day/bringup.sh --sil` → 출력에 `✅ can_guard RT: pid … SCHED_FIFO CPU 8` 확인 → `stop.sh`
- [ ] (11-05) 같은 SIL 세션에서 2 분 녹화 뒤 `python3 tools/drive_log/hdr.py <bag> /control/status/control_info,/control/status/wheel_info` → **수신 − 헤더 최대 < 1 s**
- [ ] (11-05) **주행 날 패키지** `! bash tools/bsw/bsw_up.sh --sil --name TEST` → `bash tools/bsw/bsw_status.sh` 모두 ✅ + **상태판 창이 뜨고 수신 칸이 초록**(11-06; 안 뜨면 `sudo apt install python3-tk`) →
      `ros2 topic pub -r 20 /control/target a1_can_msgs/msg/ControllerOutput "{accel_mps2: -3.0}"` 로 자동 arm 확인 → 멈추고 `bsw_down.sh --force`(SIL 은 차속 0)
      → 세션 폴더에 `*_kvaser*/candump*.zst`·`can_logs.sha256`·`SESSION.md` 확인 (`tools/bsw/README.md`)
- [ ] (11-05) A-3 재측정(A1 DBC 코드): `cd safety/can_guard && python3 sil_tests/measure_a3_latency.py --channel vcan0 --duration 5` (기준선) →
      `… --cpu 8 --rt-priority 90 --duration 5`(A-3, sudo 비밀번호 직접 입력) → 결과를 `safety/can_guard/README.md` "실측" 표에
- [ ] (11-05) 새 코드 8 h soak(밤, tmux): `python3 sil_tests/soak_8h.py --channel vcan0 --hours 8 --cpu 8 --rt-priority 90` → 09-21 결과(최대 44.6 µs, 스푸리어스 0)와 비교

- [x] `cd ~/git/A1-BSW && git fetch && git checkout claude/ecstatic-keller-uhtbhe && git pull` — 2026-11-04 `de5f161`
- [x] `bash bsw_test/00_SAF_estop/scripts/setup_env.sh --build-ws` → 준비됨 — 2026-11-04 `--check` 로 ✅(이미 설치·빌드됨)
- [ ] `bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto --track` → **sudo 비밀번호 필요 — 사람이 `! bash …` 로 실행** → **전부 PASS**(특히 `sil_02_session` A/B/C). FAIL 이면 현장 안 감 — 로그(`results/sil_*/sil_02_session.log`, 각 시나리오 `profile_gen_stdout.txt`)를 보고 고친다
- [ ] 결과 커밋·푸시: `git add bsw_test/00_SAF_estop/results/sil_* && git commit -m "SIL 대회 PC 결과(v2 세션)" && git push`
- [ ] `source bsw_test/field_env.sh` → `chk align_straight.yaml --var acc=8`, `chk speed_step.yaml $CTRL` 모두 ✅ (경로가 `~/git/A1-BSW` 인지)

## 2. 현장 (FIELD_CHEATSHEET "★ 11-14" ①~⑥ 순서)

- [ ] ① 00 통과, RTX fix, **조향 부호 확인**: `lift> steer 30` → 앞바퀴 왼쪽? 아니면 중단, `track.yaml steer_sign`·CLAUDE.md 2.1 수정 제안
- [ ] ② 원 돌기(WP3 도구 필요) ③ 조향 최대각
- [ ] ④ 직선 계단: 가속 75/85/100 %, 브레이크 50/80/100 %, 각 2회
- [ ] ⑤ 현장 1차 표(WP3 도구 필요, 자동 반영 금지) ⑥ 서킷 15 → 20(모델): 직선 횡편차, 가속 전환 수

## 3. 현장 뒤 (분석 — Claude 자유 작업, CLAUDE.md 1.3)

- [ ] 원본 옮기기 + sha256 (`raw/README.md`)
- [ ] 각 테스트 `analysis.py` — 아직 없음. 우선순위: 01(오프셋·횡편차) → 03/04(a)(% ↔ m/s² 표, `fit_maps.py` 재사용) → 03/04(b)(지연·오버슈트·오차) → 05/07(반경, 06 에커먼)
  - 세션 CSV(`<run>_cmd.csv`) 열: 기존 + `steer_prof_deg, steer_offset_deg, v_ref_kph, ctrl_mode, dist_m, item`
  - 회차 구분 = 파일 이름(`_r<N>`), `notes.txt` 시작/끝 줄
- [ ] `track.yaml`(accel_map/brake_map/steer_sign), `a1_control_adapter` 인터페이스 갱신 — **승인 후**
- [ ] `results.md`(커밋 해시·상한·클램프·원본 이름·그래프 링크), `test_summary.md` 갱신

## 4. 알려진 한계 / 확인 필요

| 항목 | 내용 |
|---|---|
| 예측 정지거리 | 감속 1.5 m/s²·지연 0.3 s 잠정(`course:`). 04(a) 결과로 갱신 |
| 01 기준 | 0.125 m 는 남은 타이어 오프셋 ≈ 0.05° 요구 ★ — 불감대(±0.2°)면 약 0.46 m 이탈 가능. 01 analysis 에 0x200 실제 조향 위치 평균 출력 |
| 50 m 코스 | 03(b) 20→30, 04(a) 20 km/h·20 %, 30 km/h 대부분은 안 들어감 ★ |
| 속도 제어기 | SIL 가짜 차량은 실차가 아님. 03(b) 반응 지연 ≤ 0.3 s 는 실차 불감대·지연에 달림(가짜 차량 0.78 s) |
