# BSW 실차 주행 테스트 — 현장 매뉴얼 (v2.1, 2026-11-04)

> **킬스위치 (언제든, 위에서 안 서면 아래)** — ① 앱 비상 정지 버튼(00 통과 후 1순위) ② PC `es` = `python3 ~/git/A1-BSW/safety/can_guard/estop.py`,
> 안 먹으면 `pkill -USR1 -f can_guard.py` ③ 물리 E-stop(00 통과 전 1순위, ESTOP 인데 브레이크 위치가 안 오르면 즉시).
> **하지 말 것**: `auto off`/auto 000(페일세이프 브레이크 풀림), `pkill -9 can_guard`(0x210 끊김 동작 미확인 TODO-2), 움직이는 중 `esr`.
> 메모장 [`FIELD_CHEATSHEET.txt`](FIELD_CHEATSHEET.txt) 맨 위에 같은 내용.

계획·합격 기준은 [`docs/test_plan.md`](docs/test_plan.md)와 각 테스트 `0X_*/docs/goal.md`에 있다. 이 문서는 **현장에서 누가 언제 무엇을 입력하는지**만 다룬다.
**자율 주행(모델 명령으로 달리기)은 `tools/bsw/bsw_up.sh` / `bsw_down.sh` 하나로 한다(2026-11-05, 자동 arm·CAN 로그 자동 녹화 — `tools/bsw/README.md`).** 아래 시험(00~07)은 기존 도구를 그대로 쓴다: `tools/track_day/bringup.sh`·`stop.sh`(띄우기·끝내기), `safety/can_guard/lift_cmd.py`(명령 입력), `safety/can_guard/estop.py`(비상 정지).
배경 절차는 [`tools/track_day/TRACK_MANUAL.md`](../tools/track_day/TRACK_MANUAL.md) T0~T4, [`tools/signal_audit/AUDIT_MANUAL.md`](../tools/signal_audit/AUDIT_MANUAL.md) 2단계.
**현장에서는 입력 순서만 모은 [`FIELD_CHEATSHEET.txt`](FIELD_CHEATSHEET.txt)(메모장)를 띄워 두고 복사·붙여넣기한다.** 터미널마다 `source ~/git/A1-BSW/bsw_test/field_env.sh` 하면 `up`/`down`/`lc`/`gen`/`es`/`m` 단축 명령을 쓴다. 이 문서는 이유·판단 기준용.
모든 명령은 **대회 차량 PC 기준**(Ubuntu 22.04, `~/git/A1-BSW`, 시스템 `python3`, Kvaser Leaf 채널 0). 다른 PC 결과는 판정에 쓰지 않는다.
2026-11-04: 02 프로파일 생성기(`bsw_test/common/scripts/profile_gen.py`) 추가 — 03(a)·04(a)·05·07 을 손 입력 대신 yaml 로 반복 입력.
**2026-11-04 v2: 하루 안에 00~07 — 50 m 직선 + 회차 사이 자율 U턴, 한 세션 여러 회차, 속도 제어기(폐루프). 순서는 5-0절.**
**2026-11-04 v2.1: 맨 위 킬스위치, 11-04 오전 원격 주행 녹화로 바뀐 사실(0-1절), 대회 PC 준비 상태(3-1절).**

---

## 0. 지금 할 수 있는 것 / 없는 것

| ID | 테스트 | 현장 가능? | 도구 | 막힌 것 |
|---|---|---|---|---|
| 00 | 비상 정지 | ✅ | can_guard ESTOP, `estop.py`, 앱, lift_cmd | 앱 버튼 프레임 확인(4-1) |
| 01 | 타이어 정렬 | △ | lift_cmd(조향 0, 가속 % 고정) | 횡편차는 GNSS(TODO-5) — 없으면 줄자 |
| 02 | 프로파일 생성기 | ✅ | `profile_gen.py` + `02_LON_profile/profiles/*.yaml` | 대회 PC SIL PASS 후 실차 저속 1회(02-1) |
| 03 (a) | 가속 % 계단 | ✅ | 생성기 `acc_step.yaml` (또는 lift_cmd `acc`) | m/s² 정밀도는 GNSS 필요 |
| 03 (b) | 속도 추종 | △ (v2) | 생성기 `speed_step.yaml` + 속도 제어기(`--ctrl`) | 03(a)·04(a) 로 `speed_ctrl.yaml` 맞춘 뒤 SIL A 재통과 |
| 04 (a) | 브레이크 % 계단 | ✅ | 생성기 `brake_step.yaml` (또는 lift_cmd `brake`) | m/s² 정밀도는 GNSS 필요 |
| 04 (b) | 감속 추종 | △ (v2) | 생성기 `decel_ramp.yaml` + 속도 제어기 | 〃 |
| 05 | 최대 조향 원선회 | ✅ | 생성기 `steer_circle.yaml` (또는 lift_cmd `steer ±150`) | 반경은 GNSS 필요(없으면 바닥 표시 + 줄자) |
| 06 | 에커먼 비교 | 분석만 | — | 05·07 데이터, TODO-4 |
| 07 | 원 / 나선 / 사인 | ✅ | 생성기 `steer_circle` / `steer_spiral` / `steer_sine.yaml` | 02-1 실차 확인 후 |

**하루 순서(권장)**: 준비 → **00(관문)** → 01 → **02-1(생성기 확인)** → 03(a) → 04(a) → 05 → 07. 00 을 통과하지 못하면 아래로 내려가지 않는다.
02-1 이 안 되면 03(a) 이후는 lift_cmd 손 입력으로 한다(아래 각 절의 lift_cmd 순서).

### 0-1. 11-04 오전 원격 주행 녹화로 바뀐 것 (잠정)

출처: [`measurements/2026-11-04_signal_audit/`](../measurements/2026-11-04_signal_audit/) `results.md`·`limits.md`(judge.py), 녹화 11:40:47 ~ 12:12:46, 프레임 1,113,179, Kvaser ch0 듣기만.
원격 유닛이 0x210 을 직접 보낸 주행(우리 can_guard 아님) — 0x210 auto 비트 내내 111.

| 관측 | 값 | 현장 절차에 미치는 것 |
|---|---|---|
| 브레이크 100 % → 0x200 위치 | 정착 중앙값 **61 %**, 최대 68 % (100 % 0.75 s 이상 유지 표본 107개) | 11-03 의 "82 % 포화" 와 다름 → 끝내기 확인은 **"브레이크 위치가 0 이 아니고 유지되는지"** 로 보고 그날 첫 값을 기록. 0 근처면 중단. 원인(주행 중·하중·업체 설정)은 V26 으로 업체 질문 |
| 원격 주행 중 0x156/0x157 | 10 ms 주기로 계속 옴, **값 고정**(EPS_En·ACC_En·AEB_En 0, EPS_Cmd·ACC_Cmd 0) | 원격(앱) 조작이 0x156/0x157 값에 안 나타남 → **앱 정지 버튼 프레임은 여전히 미확인**. 00-0 에서 `estop_request` 가 안 찍히면 앱 정지는 못 쓰는 것으로 보고 물리 E-stop 을 1순위로 |
| 차속 (0x201 좌우 평균) | 최대 31.6 km/h | 30 km/h 상한 근처까지 원격으로 달렸음 — 상한 감시 그대로 |
| 조향 0x200 | −156.4 ~ +154.5 핸들 °, 조향 속도 p99 371 °/s | 끝각 ±150 그대로 |
| 에러 프레임 | 2 개 / 32 분 | 무시 가능 |

## 1. 역할 (최소 4명)

| 역할 | 하는 일 |
|---|---|
| PC | bringup·lift_cmd 입력·메모·상태줄 감시(속도·브레이크 위치·auto 비트) |
| 안전 | 물리 E-stop 앞 대기, 주행 구역 출입 통제, 출발 전 **"비었음"** 선언, 중단 기준 보면 "정지" |
| 앱 정지 | **핸드폰 앱 비상 정지** 담당. 주행 중 손에서 놓지 않는다. "정지" 들리면 즉시 누름 |
| 기록·측정 | 회차·시각 기록(results 양식), 줄자·각도계·콘, 사진 |

## 2. 안전 규칙 (모든 테스트)

1. **0x210 송신자는 can_guard 하나.** 원격 유닛은 켜져 있으면 0x210 을 직접 보낸다 → 원격 유닛은 끈다(켜져 있으면 can_guard 시작 거부).
2. **정지 수단 3겹**(맨 위 킬스위치): ① 앱 비상 정지(0x156/0x157 → can_guard ESTOP) ② PC `estop.py`(안 먹으면 `pkill -USR1 -f can_guard.py`) ③ 물리 E-stop. 00 통과 전에는 ③을 1순위로 본다.
3. **auto 000 을 보내지 않는다** — 차량 페일세이프 브레이크가 풀린다(11-03). can_guard 는 이제 auto 000 명령을 송신하지 않지만(침묵),
   **끝낼 때는 항상 `brake 100` 유지한 채 `q` → `stop.sh`**.
4. **속도 상한 30 km/h**(첫날은 테스트별 상한이 더 낮다, 아래 표). **can_guard 에는 속도 제한이 없다** — PC 담당이 상태줄 속도를 보고,
   상한에 닿으면 `acc 0` + `brake 30`. 가속 % 는 `--acc-limit` 으로 묶는다.
5. 자율은 **전진만**. 후진·시작선 복귀는 사람이 직접(원격을 쓸 거면 bringup 전에, 또는 `stop.sh` 후).
   v2(11-04): 왕복의 방향 전환은 **자율 U턴(전진, 핸들 ±150 고정 저속)** 으로 한다 — 후진이 아니므로 이 규칙 안. 첫 U턴 전 조향 부호 확인(5-0 ②).
6. 주행 중 비상 정지 브레이크는 **서서히**(30 % → 40 %/s → 82 %, 약 1.3 s) — 정지 거리가 생긴다. 앞 공간을 넉넉히.
7. **중단 기준** (하나라도 → "정지"): 명령과 반대 방향 움직임 · 상태줄 0x200 갱신 멈춤 · can_guard 송신 실패 누적 · 다른 0x210 송신자 경고 ·
   명령 0 인데 차가 굴러감 · 브레이크 위치가 명령 없이 변함 · 이상음 · 경계선 접근 · GNSS fix 끊김(GNSS 쓰는 테스트).

| 테스트 | 속도 상한 | `--acc-limit` | lift_cmd 한계 (`--steer/--brake/--acc-limit`) |
|---|---|---|---|
| 00 | 10 km/h | 10 → 필요 시 15 | 15° / 100 % / 같은 값 |
| 01 | 20 km/h | 03(a) 결과 전 10 | 15° / 100 % / 〃 |
| 03(a)·04(a) | 30 km/h | 10 → 30 (계단) | 15° / 100 % / 〃 |
| 05·07 | 10 km/h | 정속에 필요한 만큼 | 150° / 100 % / 〃 |

## 3. 준비

### 3-1. 전날 (대회 PC)
```bash
cd ~/git/A1-BSW && git fetch && git checkout claude/ecstatic-keller-uhtbhe && git pull
bash bsw_test/00_SAF_estop/scripts/setup_env.sh --build-ws        # vcan0·python-can·cantools·pytest·ros2_ws
bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto --track   # 단위·E1~E6·P-1/P-2·02 생성기·트랙 리허설 → 전부 PASS
git add bsw_test/00_SAF_estop/results/sil_* && git commit -m "00 SIL 대회 PC 결과" && git push
```
- `setup_env.sh --check` 로 먼저 보면 아무것도 안 바꾼다. "✅ 준비됨" 이면 `--build-ws` 는 건너뛰어도 된다.
- `run_sil_check.sh --rt auto` 는 **sudo 비밀번호를 묻는다**(can_guard 를 격리 코어 8 · SCHED_FIFO 90 으로). Claude 세션에서는 `! bash …` 로 사람이 직접 입력.
- [x] 대회 PC `setup_env.sh --check` ✅ 준비됨 — vcan0 UP, python-can 4.6.1, cantools 44.0.0, ros2_ws 빌드됨, `de5f161` (2026-11-04 15:11)
- [ ] SIL 전부 PASS (FAIL 이면 현장 가지 않는다)
- [ ] localization 팀: GNSS 스택 실행 PC·`ROS_DOMAIN_ID`·fix 판정 기준(TODO-5) 받기
- [ ] 각 테스트 `goal.md` 의 담당자·예정일·비상정지 담당자 채우기
- [ ] 준비물: Kvaser Leaf v3·케이블, 핸드폰(앱)·충전, 콘·마스킹테이프·**30 m 줄자**, 각도계, 고임목, 타이어 공기압계, 이 문서 출력본

### 3-2. 현장 도착 (20분)
- [ ] 노면·날씨·기온·**타이어 공기압 4개** 기록(results.md "주행 조건")
- [ ] 주행 구역 표시: 01 직선(시작선·100 m·여유), 03/04 직선(가속 구간 + 제동 구간 + 여유), 05/07 원(지름 15 m 이상 + 여유)
- [ ] 원격 유닛 OFF, 차량 전원 ON, 후륜구동 ON, 핸들 0, 고임목
- [ ] 커밋 해시 기록: `git -C ~/git/A1-BSW log --oneline -1`

## 4. 공통 절차

### 4-0. 띄우기 (매 세션, 5분)
```bash
# 재부팅했으면 1번 (Kvaser 미러용)
sudo ip link add dev kv0 type vcan 2>/dev/null; sudo ip link set kv0 up
# (GNSS 쓰는 테스트, localization 팀 PC 가 아니면) 다른 터미널에서
#   source ~/A1_localization/install/setup.bash && ros2 launch a1_gnss_bringup gnss.launch.py     ← TODO-5 확정 전
# 띄우기 — 세션 폴더 이름에 테스트 ID
bash ~/git/A1-BSW/tools/track_day/bringup.sh --kvaser 0 --no-adapter --acc-limit 10 --estop-required \
     --out ~/a1_race_capture/$(date +%Y%m%d)_SAF-ESTOP-01_s1
```
- bringup 이 출력한 **lift_cmd 명령줄을 복사**해 다른 터미널에서 실행하되, 한계(`--steer-limit-deg` 등)는 2절 표 값으로,
  유지 시간(`--steer/--acc/--brake-hold-s`)은 5절 테스트별 값으로 고친다. **lift_cmd 를 `q` 로 끄면 can_guard 가 HOLDING → STOPPED** 로
  가서 그 세션은 끝난다 → 옵션을 바꾸려면 4-1 끝내기 → 4-0 다시 띄우기.
- **생성기를 쓰는 회차**는 lift_cmd 대신 `profile_gen.py run …`(5절). 둘은 동시에 못 띄운다. `--acc-limit` 은 그 회차 yaml 의 `acc` 와 같게.
  생성기는 **한 회차 = 한 세션**: 끝내기(브레이크 100 %) → `q` → `stop.sh` → 차 복귀 → 다시 띄우기.
- `--estop-required`: 앱(0x156/0x157)이 안 보이면 시작 거부 → 앱 연결 후 다시. **00 이후 모든 테스트에 붙인다.**
- 확인: `can_guard.log` 첫 줄 "INIT — 첫 유효 명령 전까지 0x210 송신 안 함", 상태줄에 0x200 값·차속이 보임.
- 메모(모든 터미널에서 쓰게): `m() { echo "$(date +%s.%N) $(date +%H:%M:%S) $*" >> ~/a1_race_capture/track_latest/notes.txt; }`
- **회차 표시**는 메모로: `m "r1 시작"` … `m "r1 끝 정지거리 4.2m"`.

### 4-1. 끝내기 (매 세션)
```
(lift_cmd)  auto brake on
            brake 100        ← 상태줄 브레이크 위치 확인(11-03 82 % / 11-04 61~68 %, 0-1절 — 그날 첫 값 기록)
            q
```
```bash
bash ~/git/A1-BSW/tools/track_day/stop.sh
```
- 그다음 상태줄/`candump kv0` 로 0x200 브레이크 위치가 유지되는지 확인. 풀리면 즉시 물리 E-stop, 기록.

### 4-2. 비상 정지 후 다시 시작
ESTOP·STOPPED 는 can_guard 를 재시작해야 빠져나온다(자동 복귀 없음).
1. 차가 완전히 섰는지 확인 → `python3 ~/git/A1-BSW/safety/can_guard/estop.py --reset` (차속 ≤ 3 km/h 일 때만 받아 STOPPED, 브레이크 유지)
2. 4-1 끝내기 → 사람이 시작선 복귀 → 4-0 띄우기(같은 테스트면 `_s2` 등 새 세션)

### 4-3. 데이터 옮기기 (세션마다, 그날 안에)
```bash
T=~/git/A1-BSW/bsw_test/00_SAF_estop/raw          # 테스트 폴더
cp -a ~/a1_race_capture/20261105_SAF-ESTOP-01_s1 "$T/"
( cd "$T" && du -sh 20261105_SAF-ESTOP-01_s1 && find 20261105_SAF-ESTOP-01_s1 -type f -exec sha256sum {} + > 20261105_SAF-ESTOP-01_s1.sha256 )
```
- 세션 폴더 = 원본(Kvaser 로그 + bag + `can_guard.log` + `can_guard_events.csv` + `lift_cmd.log` + `notes.txt`). **수정 금지.**
- `raw/README.md` 표에 한 줄(세션, 크기, sha256 파일, GNSS fix 여부, 메모). `raw/` 는 git 에 안 들어간다 → 외장 백업 2부.
- 회차(r1, r2…)는 `notes.txt` 의 `r? 시작/끝` 으로 나눈다. 결과는 각 `docs/results.md` 에 회차별로.

---

## 5-0. v2 하루 순서 (2026-11-04 결정)

**결정(사용자, 11-04)**: 직선 100 m → **50 m**(앱 신호가 100 m 끝까지 닿지 않을 수 있음). 왕복은 **자율 U턴**. 하루 안에 1·2단계(개루프 → 폐루프)를 이어서 한다.
입력 순서는 [`FIELD_CHEATSHEET.txt`](FIELD_CHEATSHEET.txt) "★ v2" 절. 여기는 이유와 판단 기준.

| 순서 | 내용 | 생성기 | 끝나면 나오는 것 |
|---|---|---|---|
| ① | 00 비상 정지 (관문) | 안 씀(lift_cmd, 5절 00) | 통과 판정 |
| ② | 01-1 정지 측정 + **조향 부호 확인** | lift_cmd | 앞바퀴 좌·우 각, `steer +30` = 왼쪽인지 |
| ③ | 01 보정 전 왕복(10 km/h, 6 편도) | `align_straight.yaml --reps 6 $TURN` | 오프셋(타이어 °·핸들 °) → `ofs` |
| ④ | 03(a) 불감대 → % 계단 | `acc_step.yaml --sweep acc=…` | 불감대, 가속 % ↔ m/s² |
| ⑤ | 04(a) 브레이크 % (10·20 km/h) | `brake_step.yaml --sweep brake=…` | 브레이크 % ↔ m/s², 정지 거리 |
| ⑥ | 속도 제어기 맞추기 | `fit_maps.py` → `ctrl/speed_ctrl.yaml`(승인) → **SIL A 재실행** | 현장 값 + 커밋 해시 |
| ⑦ | 03(b)·04(b) 폐루프 | `speed_step.yaml` / `decel_ramp.yaml` + `$CTRL` | 반응 지연·오버슈트·정속 오차·감속 오차 |
| ⑧ | 01 보정 후 왕복 | `align_straight.yaml` (오프셋 자동) | 보정 전·후 비교 |
| ⑨ | 05 최대 조향 원 좌 3 · 우 3 | `steer_circle_speed.yaml` | 최소 회전 반경 |
| ⑩ | 07 나선 좌 2 · 우 2 (시간 남으면 원·사인) | `steer_spiral_speed.yaml` | 조향각–반경 관계 |
| 그날 밤 | 06 분석, 변환표, `track.yaml`·어댑터 갱신(승인) | — | TODO-3·4 |

**코스 배치**
- 직선 50 m 두 차선, 간격 = U턴 지름 ≈ 2R ≈ 13 m ★(핸들 150°, 조향비 7.5 잠정). 양 끝 U턴 구역: 끝선에서 15 m 이상 비움.
- **U턴은 늘 같은 방향(좌, +150, 반시계)** → 타원 코스. 좌우를 번갈면 차가 매번 13 m 씩 옆으로 밀려 나간다.
- 앱 담당: 직선 가운데, 타원 **바깥쪽** 10 m 이상. 타원 안쪽(두 차선 사이)에는 사람이 서지 않는다.
- 05·07 원: 타원과 겹치지 않는 별도 구역(지름 20 m 이상 + 여유).

**세션 모드가 하는 일 / 판단 기준**
- 회차 사이에도 생성기가 계속 브레이크 100 % 명령을 쓴다 → can_guard 는 ACTIVE 그대로(stop.sh·재기동 없음). 다음 회차는 **Enter 를 눌러야** 시작.
- **코스 끝 예측**: 후륜 적분 이동거리 + 예측 정지거리(지연 0.3 s + 끝내기 램프 절반 + v²/(2·1.5 m/s²), 감속도 잠정) ≥ 50 m 이면 그 회차 끝내기.
  50 m 에서는 03(a) 센 % 와 30 km/h 는 거의 다 일찍 끝난다 ★ — 정상. 03(b) 는 0→10·10→20 만.
- **U턴**: 정지 → 핸들 lock → 출발(개루프 acc % 또는 폐루프 5 km/h) → 자전거 모델 방위(0x200 실제 조향, 조향비 잠정)가
  "180° − 서는 동안 더 돌 각도"에 닿으면 정지 → 핸들 0. 방위 추정이 틀려도 거리 상한(반원의 1.6 배)·60 s 에서 멈춘다.
  **조향비가 틀리면 U턴 끝 방위가 어긋난다** → 첫 U턴 뒤 차 방향을 눈으로 보고, 10° 넘게 틀어지면 사람이 바로잡고 기록(TODO-4 근거).
- **출발 안 함**: 가속 > 0 인데 5 s 정지면 그 회차만 끝(불감대 아래). 세션은 다음 회차로.
- **세션 중단**(남은 회차 안 함): 0x201 끊김, 차속 상한, can_guard 개입(0x210 브레이크가 명령보다 5 % 넘게 셈·가속 0 — ESTOP/HOLDING), U턴 정지 실패, Ctrl+C.
- 비상 정지는 그대로: 앱·`es`·물리. ESTOP 이면 생성기가 개입을 보고 세션을 끝낸다 → `q` → `esr` → `down`. 이어서 할 땐 `--first-rep` 로 번호를 이어간다.
- 조향 오프셋: `ofs <핸들°>` 를 한 뒤 `gen` 은 모든 조향 명령에 더한다(로그 `steer_cmd_deg` = 더한 값, `steer_prof_deg` = 더하기 전). U턴 lock 은 150 을 넘지 않게 자동으로 줄인다.
  05 원은 `deg=150−OFS`(좌)·`deg=−150−OFS`(우)로 직접 준다(넘으면 `chk` 가 거부).
- 속도 제어기: 후륜 차속(0x201) PI + 정속 유지 % 표 + 가속/감속 변환표 피드포워드. 가속·브레이크 동시 0. 기본값은 SIL 가짜 차량 기준 **잠정** —
  ⑥ 에서 `speed_ctrl.yaml` 을 고치면 **대회 PC 에서 `sil_session.py --only A` 다시 PASS** 해야 ⑦ 로 간다.

## 5. 테스트별 절차

### 00 SAF-ESTOP — 비상 정지 (관문, 약 60분)

**00-0. 앱 버튼 확인 (정지, 차량 버스 듣기만, 10분)** — 판정 조건을 확정하는 단계
bringup 후 lift_cmd 는 **아직 띄우지 않는다**(can_guard 는 침묵 상태라 아무것도 안 보냄).
```bash
candump -ta kv0,156:7FF,157:7FF | tee ~/a1_race_capture/track_latest/app_frames.txt    # 다른 터미널
```
앱 담당: 대기 10 s → **비상 정지 버튼** 3회(각 3 s, 사이 5 s) → 다른 조작(전진·조향 등) 있으면 각각 1회. PC 가 `m "앱 정지 버튼 1"` 식으로 메모.
- 확인: 버튼 때 0x156 바이트0/2(En)·0x157 바이트3~4(ACC_Cmd)가 어떻게 바뀌나, 평소에 10 ms 로 계속 오나, 버튼 놓으면 원래대로 돌아오나.
- `can_guard_events.csv` 에 `estop_request remote …` 가 버튼마다 찍혔는가 → **안 찍히면 00-1 이후 진행 금지**, 프레임 값을 가져와 판정 조건을 고친다.
- 계속 온다면 데드맨(`--estop-can-timeout 0.3`) 사용 여부를 정한다(현재 끔).

**00-1. S0 정지 시험 (바퀴 굄, 15분)** — goal.md S0
```
(lift_cmd)  auto on            ← 상태줄 auto 111, can_guard ACTIVE
```
| 회차 | 트리거 | 하는 법 | 확인 |
|---|---|---|---|
| r1~r3 | 명령 끊김 | lift_cmd `q` | HOLDING → 브레이크 30 % → 서서히 60 %, 2 s 뒤 STOPPED |
| r4~r6 | 앱 버튼 | 앱 담당 | ESTOP, 브레이크 30 → 82 %, 가속 0, auto 111 유지 |
| r7~r9 | PC | `python3 ~/git/A1-BSW/safety/can_guard/estop.py` | 〃 |
| r10 | can_guard kill | `pkill -9 -f can_guard.py` | 0x210 끊김 → 차량 페일세이프 브레이크(TODO-2 확인), 시간 측정 |

매 회차 끝: 4-2 다시 시작. 정지 상태라 `estop.py --reset` 이 받아져야 한다(해제 동작도 확인).

**00-2. S1 5 km/h → S2 10 km/h (각 15분)**
```
(lift_cmd)  auto on
            acc <%>            ← 03(a) 전이므로 3 % 부터 올려 5 km/h 근처를 찾는다. 2 s 뒤 자동 0 → 다시 입력해 유지
```
- 원하는 속도에서 안전 담당 "지금" → 트리거(명령 끊김 / 앱 / PC 각 3회). **트리거 순간 PC 가 `m "r? 트리거 속도 5.2"`**.
- 정지 후: 정지 거리(줄자), 휠락 여부(바퀴 자국·소리), 그다음 4-2.
- S1 전부 OK → S2. 하나라도 이상하면 S2 하지 않는다.

**00 판정 자료**: `can_guard_events.csv`(감지 지연), Kvaser 로그 0x200 브레이크 위치 상승 시각(제동 시작), 후륜 속도(정지 시간·거리).

### 01 LAT-ALIGN — 타이어 정렬 (약 40분)

lift_cmd 는 처음부터 `--steer-hold-s 60 --acc-hold-s 30` 으로 띄운다.

**01-1. 정지 측정**: lift_cmd `auto steer on` → `steer 0` → 앞바퀴 방향을 실/레이저로 측정(좌·우 타이어 °) + 상태줄 0x200 조향 위치 기록.
**01-2. 보정 전 직진**:
```
auto on
steer 0
acc <%>       ← 10 km/h 근처에서 고정. 속도가 계속 오르면 낮추고, 상한 20 km/h 넘으면 acc 0 + brake 30
(직선 끝) acc 0 → brake 30 → 정지 → brake 100 → q → stop.sh → 사람이 차를 돌려 반대 방향 → 다시 띄우기
```
- 왕복 × 3 (10 km/h), 여유 있으면 20 km/h 왕복 × 3. 매 회차 `m "r? 정방향/역방향 시작/끝"`.
- GNSS 없으면: 시작선 중앙에 표시, 100 m(또는 가능한 거리) 지점에서 차 중심의 좌우 이탈을 줄자로.
**01-3. 오프셋 적용 후**: 계산한 오프셋(핸들 °)을 `steer <값>` 으로 넣고 왕복 × 3. (자동 보정 코드는 아직 없다 — 손으로 넣는다.)

### 02 LON-PROF — 생성기 실차 확인 (02-1, 약 15분)

03 이후에 생성기를 쓰기 전에 **저속 1회**로 생성기가 대회 PC·Kvaser 에서 맞게 도는지 본다. 00 S1 에서 찾은 5 km/h 근처 % 를 `acc` 로.
```bash
G=~/git/A1-BSW/bsw_test/common/scripts/profile_gen.py; P=~/git/A1-BSW/bsw_test/02_LON_profile/profiles
python3 $G check $P/gen_check.yaml --var acc=6                     # ✅ 검사 통과 + 시간표 확인
python3 $G run $P/gen_check.yaml --var acc=6 --run $(date +%Y%m%d)_LON-PROF-01_r1 --interface kvaser --channel 0 --bitrate 500000
#   대기(브레이크 30 %, auto 111) → 안전 "비었음" → Enter → 사다리꼴 가속 2 s 오름·4 s 유지·2 s 내림 → 끝내기(브레이크 100 %) → q → stop.sh
```
- 확인: 상태줄 `[gen] run` 의 명령 = `0x210` 값(같은 줄 오른쪽), 차속 표시가 나옴, 끝내기에서 브레이크 위치가 오르고 유지(4-1 값).
- 아무 줄 Enter / Ctrl+C = 즉시 끝내기. 차속 > yaml `max_speed_kph`, 0x201 0.3 s 끊김이면 자동 끝내기.
- 기록: 세션 폴더에 `<회차>_cmd.csv`·`_gen.log`·`_profile.yaml`, `notes.txt` 에 시작/끝 자동 기록(메모 `m` 따로 안 해도 됨).

### 03 (a) LON-ACC — 가속 % 계단 (약 45분)

**생성기**: `acc_step.yaml --var acc=<%> --var t=10` — 브레이크 풀고 acc % 를 t 초 → 끝내기. 30 km/h(`vmax`) 닿으면 자동 끝내기. 불감대는 `--var acc=3 --var t=5` 부터 1 % 씩.
아래는 lift_cmd 로 할 때.

lift_cmd `--acc-hold-s 10`. 시작선 정지, 앞 구간 비었음.
```
auto on
acc 3 / 4 / 5 …       ← 불감대: 차가 처음 움직이는 % (1 % 씩, 회차마다 정지 후)
acc 10                ← r1~r3: 정지 출발, 10 s 또는 30 km/h 도달 → acc 0 → brake 30 → 정지
acc 15 / 20 / 25 / 30 ← 각 3회. --acc-limit 을 bringup 재시작으로 한 계단씩 올림
```
- 각 회차: `m "r? acc 15 시작"`, 끝 `m "r? 최고 속도 22 km/h"`.
- 30 km/h 에 닿으면 즉시 acc 0. 제동 구간이 모자라면 그 % 는 짧게(5 s).
- 현장 확인: 상태줄 속도가 0 → 움직이는 % (불감대), 같은 % 3회 최고 속도 차이.
- 표 생성(현장 PC): `python3 ~/git/A1-BSW/tools/track_day/fit_maps.py long <세션>/bag` → `accel_map:` (GNSS 녹화 필요).

### 04 (a) LON-DEC — 브레이크 % 계단 (약 45분)

**생성기**: `brake_step.yaml --var acc=<03(a) 에서 찾은 %> --var v=<10|20|30> --var vmax=<v+5, 최대 30> --var brake=<%>` — v km/h 도달 순간 가속 0 + 브레이크 % 를 8 s → 끝내기(100 %).
20 s 안에 v 에 못 가면 자동 끝내기(acc 를 올려 다시). 아래는 lift_cmd 로 할 때.

시작 속도 10 / 20 / 30 km/h 를 03(a) 로 찾은 가속 % 로 만든 뒤 `acc 0` 과 **동시에** `brake <%>`, 정지까지 유지(`--brake-hold-s 10`).
| 시작 속도 | 브레이크 % (회차마다 하나, 각 3회) |
|---|---|
| 10 km/h | 20, 40, 60, 82, 100 |
| 20 km/h | 20, 40, 60, 82, 100 |
| 30 km/h | 40, 60, 82, 100 |
- 약한 % 부터. 정지 거리가 제동 구간을 넘을 것 같으면 그 조합은 건너뜀.
- 매 회차: 브레이크 시작 지점(콘)~정지 위치 줄자, `m "r? 20kph brake 40 정지거리 6.1m"`.
- 표: `fit_maps.py long` → `brake_map:` (상한 82 %).

### 05 LAT-MAXSTEER — 최대 조향 원선회 (약 30분)

**생성기**: `steer_circle.yaml --var deg=150`(좌) / `--var deg=-150`(우) `--var acc=<%> --var t=40` — 정지 상태에서 60 °/s 로 핸들 → 3 s → 출발 → t 초 → 끝내기. 아래는 lift_cmd 로 할 때.

lift_cmd 한계 `--steer-limit-deg 150 --steer-hold-s 120 --acc-hold-s 60`. 원 중심에 콘, 지름 15 m 이상 비움.
```
auto on
steer 150         ← 정지 상태에서 끝까지 먼저 돌린다(5 s 기다림)
acc <%>           ← 5 km/h 근처 정속. 2바퀴 이상
acc 0 → brake 30 → 정지 → steer 0
```
- 좌(+150) 3회, 우(−150) 3회. 매 회차 `m "r? 좌 시작/끝"`.
- GNSS 없으면: 한 바퀴 궤적에 바퀴 자국 대신 콘 4개 표시 → 지름을 줄자로 두 방향.
- 상태줄 0x200 조향 위치(실제 핸들각) 기록 — 11-03 정지 때 −149.3 / +150.0.

### 07 LAT-PROF — 원 / 나선 / 사인 (약 60분, 02-1 통과 후)

- **원**: `steer_circle.yaml --var deg=±30/60/90/120/150 --var t=<반 바퀴~2바퀴>`, 5 km/h, 각 2회. 작은 각도는 원이 커진다(±30° ≈ 반경 34 m, 잠정 조향비 7.5) — 구역이 모자라면 반 바퀴만.
- **나선**: `steer_spiral.yaml --var deg=150`(좌) / `-150`(우), 10 °/s, 5 km/h, 각 2회. 15 s 동안 원이 점점 작아진다 — 시작점에서 바깥쪽 여유.
- **사인**: `steer_sine.yaml --var freq=0.1|0.2|0.5`, 진폭 30°, 10 km/h, 각 2회. `freq=0.1` 은 `--var cycles=2`(20 s) 로 줄여도 됨. 진폭×2π×f > 120 °/s 는 `check` 가 막는다.
- 속도는 개루프(acc % 고정)라 차속은 회차마다 `--var acc` 로 맞춘다.

---

## 6. 끝난 뒤 (그날 안에)

- [ ] 4-3 데이터 옮기기 + 외장 백업 2부
- [ ] 각 `docs/results.md`: 주행 조건, 커밋, 회차표(세션·`notes.txt` 회차), 문제점
- [ ] `docs/test_summary.md` 상태·최근 테스트일 갱신
- [ ] 분석(`analysis.py`)은 아직 없다 — 다음 작업으로 00 판정 스크립트부터(이벤트 로그 + Kvaser 0x200)
- [ ] 바뀐 사실(앱 버튼 프레임, 0x210 끊김 동작 TODO-2, 불감대 등)은 `CLAUDE.md` 2·8절과 can_guard 기본값에 반영

## 7. 문제가 생기면

| 증상 | 할 일 |
|---|---|
| bringup "시작 거부 — 다른 0x210" | 원격 유닛이 켜져 있다 → 끄고 다시 |
| bringup "핸드폰 앱 … 안 보임" | 앱 연결·버스 확인. 00-0 전이면 `--estop-required` 빼고 띄워 프레임부터 본다 |
| 앱 버튼을 눌렀는데 `estop_request` 없음 | 판정 조건 불일치 → 그날 주행 중단, `app_frames.txt` 로 조건 수정 |
| lift_cmd 를 띄워도 0x210 이 안 나감 | `auto on` 을 안 했다(auto 000 은 침묵) — 정상 |
| ESTOP 인데 브레이크 위치가 안 오름 | 즉시 물리 E-stop. `can_guard.log`·`events.csv` 보관, 그날 주행 중단 |
| `estop.py --reset` "거부" | 차가 아직 움직임(> 3 km/h) 또는 앱 버튼이 아직 눌림, 또는 0x201 차속 없음 → 재시작(4-1) |
| 브레이크 100 % 인데 위치 60 % 대 | 11-04 오전에도 61~68 %(0-1절). 그날 첫 값 기록하고 진행. 0 근처면 중단 |
| `stop.sh` 뒤 브레이크가 풀림 | 물리 E-stop, 시각 기록 → TODO-2 증거. 다음부터 끝내기 전에 고임목 |
| 속도가 상한을 넘음 | `acc 0` + `brake 30`, 안 서면 `estop.py` / 앱 / 물리 E-stop |
| 0x201 속도가 0 인데 차가 움직임 | 가감속 시험 중단(속도 감시 불가) |
