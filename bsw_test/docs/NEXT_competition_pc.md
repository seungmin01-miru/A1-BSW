# 다음 할 일 — 대회 차량 PC 세션 (2026-11-04 작성, 개발 PC 에서 넘김)

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

## 1. 대회 PC 에서 할 일 (현장 출발 전)

- [x] `cd ~/git/A1-BSW && git fetch && git checkout claude/ecstatic-keller-uhtbhe && git pull` — 2026-11-04 `de5f161`
- [x] `bash bsw_test/00_SAF_estop/scripts/setup_env.sh --build-ws` → 준비됨 — 2026-11-04 `--check` 로 ✅(이미 설치·빌드됨)
- [ ] `bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto --track` → **sudo 비밀번호 필요 — 사람이 `! bash …` 로 실행** → **전부 PASS**(특히 `sil_02_session` A/B/C). FAIL 이면 현장 안 감 — 로그(`results/sil_*/sil_02_session.log`, 각 시나리오 `profile_gen_stdout.txt`)를 보고 고친다
- [ ] 결과 커밋·푸시: `git add bsw_test/00_SAF_estop/results/sil_* && git commit -m "SIL 대회 PC 결과(v2 세션)" && git push`
- [ ] `source bsw_test/field_env.sh` → `chk uturn.yaml`, `chk align_straight.yaml --var acc=8`, `chk speed_step.yaml $CTRL` 모두 ✅ (경로가 `~/git/A1-BSW` 인지)

## 2. 현장 (FIELD_CHEATSHEET "★ v2" ①~⑩ 순서)

- [ ] ① 00 통과 (기존 절차)
- [ ] ② **조향 부호 확인**: `lift> steer 30` → 앞바퀴 왼쪽? 아니면 U턴·05·07 중단, `track.yaml steer_sign`·CLAUDE.md 2.1 수정 제안
- [ ] ③ 01 보정 전 왕복 → **첫 U턴 끝 방위를 눈으로 확인**(10° 넘게 틀어지면 조향비 7.5 잠정이 틀림 → 기록, `vehicle.steer_ratio` 조정 후보)
- [ ] ④⑤ 03(a)·04(a) — "코스 끝 예측"으로 일찍 끝나는 회차는 정상
- [ ] ⑥ `speed_ctrl.yaml` 현장 값(승인) → **대회 PC `sil_session.py --only A` 재 PASS** → 커밋
- [ ] ⑦~⑩ 폐루프 03(b)·04(b), 01 보정 후, 05 원, 07 나선

## 3. 현장 뒤 (분석 — Claude 자유 작업, CLAUDE.md 1.3)

- [ ] 원본 옮기기 + sha256 (`raw/README.md`)
- [ ] 각 테스트 `analysis.py` — 아직 없음. 우선순위: 01(오프셋·횡편차) → 03/04(a)(% ↔ m/s² 표, `fit_maps.py` 재사용) → 03/04(b)(지연·오버슈트·오차) → 05/07(반경, 06 에커먼)
  - 세션 CSV(`<run>_cmd.csv`) 열: 기존 + `steer_prof_deg, steer_offset_deg, v_ref_kph, ctrl_mode, dist_m, heading_deg, item`
  - 회차 구분 = 파일 이름(`_r<N>`, U턴 `_r<N>_turn`), `notes.txt` 시작/끝 줄
- [ ] `track.yaml`(accel_map/brake_map/steer_sign), `a1_control_adapter` 인터페이스 갱신 — **승인 후**
- [ ] `results.md`(커밋 해시·상한·클램프·원본 이름·그래프 링크), `test_summary.md` 갱신

## 4. 알려진 한계 / 확인 필요

| 항목 | 내용 |
|---|---|
| 예측 정지거리 | 감속 1.5 m/s²·지연 0.3 s 잠정(`course:`). 04(a) 결과로 갱신 |
| U턴 방위 | GNSS 없이 자전거 모델(0x200 실제 핸들각 / 조향비 7.5 잠정). GNSS 토픽(TODO-5) 오면 yaw 로 바꿀 것 |
| 01 왕복 | 두 방향이 약 13 m 떨어진 두 차선 — 노면 기울기 상쇄 가정 약함, 결과에 표시 |
| 01 기준 | 0.125 m 는 남은 타이어 오프셋 ≈ 0.05° 요구 ★ — 불감대(±0.2°)면 약 0.46 m 이탈 가능. 01 analysis 에 0x200 실제 조향 위치 평균 출력 |
| 50 m 코스 | 03(b) 20→30, 04(a) 20 km/h·20 %, 30 km/h 대부분은 안 들어감 ★ |
| 속도 제어기 | SIL 가짜 차량은 실차가 아님. 03(b) 반응 지연 ≤ 0.3 s 는 실차 불감대·지연에 달림(가짜 차량 0.78 s) |
