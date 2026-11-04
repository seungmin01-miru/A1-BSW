# BSW 실차 주행 테스트 — 현장 매뉴얼 (v1, 2026-11-04)

계획·합격 기준은 [`docs/test_plan.md`](docs/test_plan.md)와 각 테스트 `0X_*/docs/goal.md`에 있다. 이 문서는 **현장에서 누가 언제 무엇을 입력하는지**만 다룬다.
기존 도구를 그대로 쓴다: `tools/track_day/bringup.sh`·`stop.sh`(띄우기·끝내기), `safety/can_guard/lift_cmd.py`(명령 입력), `safety/can_guard/estop.py`(비상 정지).
배경 절차는 [`tools/track_day/TRACK_MANUAL.md`](../tools/track_day/TRACK_MANUAL.md) T0~T4, [`tools/signal_audit/AUDIT_MANUAL.md`](../tools/signal_audit/AUDIT_MANUAL.md) 2단계.

---

## 0. 지금 할 수 있는 것 / 없는 것

| ID | 테스트 | 현장 가능? | 도구 | 막힌 것 |
|---|---|---|---|---|
| 00 | 비상 정지 | ✅ | can_guard ESTOP, `estop.py`, 앱, lift_cmd | 앱 버튼 프레임 확인(4-1) |
| 01 | 타이어 정렬 | △ | lift_cmd(조향 0, 가속 % 고정) | 횡편차는 GNSS(TODO-5) — 없으면 줄자 |
| 02 | 프로파일 생성기 | ❌ | — | **생성기 미구현**. 그 전까지 lift_cmd 손 입력으로 대체 |
| 03 (a) | 가속 % 계단 | ✅ | lift_cmd `acc` | m/s² 정밀도는 GNSS 필요 |
| 03 (b) | 속도 추종 | ❌ | — | 속도 제어기 미완성 |
| 04 (a) | 브레이크 % 계단 | ✅ | lift_cmd `brake` | 〃 |
| 04 (b) | 감속 추종 | ❌ | — | 〃 |
| 05 | 최대 조향 원선회 | ✅ | lift_cmd `steer ±150` | 반경은 GNSS 필요(없으면 바닥 표시 + 줄자) |
| 06 | 에커먼 비교 | 분석만 | — | 05·07 데이터, TODO-4 |
| 07 | 원 / 나선 / 사인 | 원만 ✅ | lift_cmd | 나선·사인은 02 생성기 필요 |

**하루 순서(권장)**: 준비 → **00(관문)** → 01 → 03(a) → 04(a) → 05 → 07(원). 00 을 통과하지 못하면 아래로 내려가지 않는다.

## 1. 역할 (최소 4명)

| 역할 | 하는 일 |
|---|---|
| PC | bringup·lift_cmd 입력·메모·상태줄 감시(속도·브레이크 위치·auto 비트) |
| 안전 | 물리 E-stop 앞 대기, 주행 구역 출입 통제, 출발 전 **"비었음"** 선언, 중단 기준 보면 "정지" |
| 앱 정지 | **핸드폰 앱 비상 정지** 담당. 주행 중 손에서 놓지 않는다. "정지" 들리면 즉시 누름 |
| 기록·측정 | 회차·시각 기록(results 양식), 줄자·각도계·콘, 사진 |

## 2. 안전 규칙 (모든 테스트)

1. **0x210 송신자는 can_guard 하나.** 원격 유닛은 켜져 있으면 0x210 을 직접 보낸다 → 원격 유닛은 끈다(켜져 있으면 can_guard 시작 거부).
2. **정지 수단 3겹**: ① 앱 비상 정지(0x156/0x157 → can_guard ESTOP) ② PC `estop.py` ③ 물리 E-stop. 00 통과 전에는 ③을 1순위로 본다.
3. **auto 000 을 보내지 않는다** — 차량 페일세이프 브레이크가 풀린다(11-03). can_guard 는 이제 auto 000 명령을 송신하지 않지만(침묵),
   **끝낼 때는 항상 `brake 100` 유지한 채 `q` → `stop.sh`**.
4. **속도 상한 30 km/h**(첫날은 테스트별 상한이 더 낮다, 아래 표). **can_guard 에는 속도 제한이 없다** — PC 담당이 상태줄 속도를 보고,
   상한에 닿으면 `acc 0` + `brake 30`. 가속 % 는 `--acc-limit` 으로 묶는다.
5. 자율은 **전진만**. 방향 전환·시작선 복귀는 사람이 직접(원격을 쓸 거면 bringup 전에, 또는 `stop.sh` 후).
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
bash bsw_test/00_SAF_estop/scripts/run_sil_check.sh --rt auto --track   # 단위·E1~E6·P-1/P-2·트랙 리허설 → 전부 PASS
git add bsw_test/00_SAF_estop/results/sil_* && git commit -m "00 SIL 대회 PC 결과" && git push
```
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
- `--estop-required`: 앱(0x156/0x157)이 안 보이면 시작 거부 → 앱 연결 후 다시. **00 이후 모든 테스트에 붙인다.**
- 확인: `can_guard.log` 첫 줄 "INIT — 첫 유효 명령 전까지 0x210 송신 안 함", 상태줄에 0x200 값·차속이 보임.
- 메모(모든 터미널에서 쓰게): `m() { echo "$(date +%s.%N) $(date +%H:%M:%S) $*" >> ~/a1_race_capture/track_latest/notes.txt; }`
- **회차 표시**는 메모로: `m "r1 시작"` … `m "r1 끝 정지거리 4.2m"`.

### 4-1. 끝내기 (매 세션)
```
(lift_cmd)  auto brake on
            brake 100        ← 상태줄 브레이크 위치 8.2 확인
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

### 03 (a) LON-ACC — 가속 % 계단 (약 45분)

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

### 07 LAT-PROF — 원 (약 40분, 나선·사인은 02 생성기 이후)

05 와 같은 방식으로 핸들 ±30 / ±60 / ±90 / ±120 / ±150 °, 5 km/h, 각 2회. 작은 각도는 원이 커진다(±30° ≈ 반경 34 m, 잠정 조향비 7.5) — 구역이 모자라면 반 바퀴만.

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
| `stop.sh` 뒤 브레이크가 풀림 | 물리 E-stop, 시각 기록 → TODO-2 증거. 다음부터 끝내기 전에 고임목 |
| 속도가 상한을 넘음 | `acc 0` + `brake 30`, 안 서면 `estop.py` / 앱 / 물리 E-stop |
| 0x201 속도가 0 인데 차가 움직임 | 가감속 시험 중단(속도 감시 불가) |
