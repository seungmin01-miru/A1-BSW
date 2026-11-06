# 경기 깃발 대응 — 실차 시험 계획

작성 2026-11-06 · 상태: **계획, 미실행. 모든 항목은 사람 승인 후 실행한다**(CLAUDE.md 1절). 기준: [`docs/2026-11-06_race_flag_dev_plan.md`](../../docs/2026-11-06_race_flag_dev_plan.md)(계획서) WP8.
합격 기준은 계획서·요구사항([`docs/race_flag_requirements.md`](../../docs/race_flag_requirements.md))에 있는 것만 인용한다. 없으면 **관찰**로 적고 판정하지 않는다. 숫자를 여기서 새로 정하지 않는다. ★ = 계산값, 잠정 = 실측·답변으로 바뀌는 값.

| # | 시험 | 테스트 ID | 대회 신호(0x124) | 얻는 것 |
|---|---|---|---|---|
| ① | PRE_GO 브레이크 10/20/30 % 풀림 시간 | RACE-PREGO-01 | 1부: 불필요 / 2부: 필요 | `--pre-go-brake-pct` |
| ② | 서행 진입·추종 30→8 km/h | RACE-SLOW-01 | 필요 | R-03·R-04, 0x201 오차, 언더슈트 |
| ③ | Slow Off 램프 | RACE-SLOWOFF-01 | 필요 | `slow_off_ramp_pct_s` 근거 |
| ④ | `kill -STOP` 0.2 / 0.4 s (10 km/h 이하) | RACE-GAP-01 | 필요 | R-07, `--resume-low-speed` 판단 근거 |
| ⑤ | 0x203 반영 + 해제값 | RACE-LIMIT-01 | 필요 + **대회측 허락** | `--speed-limit-tx`, 해제값 |
| ⑥ | 브레이크 계단 | LON-DEC-03 | 불필요 | `fit_long_profiles` 입력(11-04 표본 부족 해소) |

**순서:** ⑥ → ①1부 → (신호 허락 뒤) ①2부 → ② → ③ → ④ → ⑤. ②~⑤ 는 SIL `sil_tests/r_race.py` 해당 시나리오(S5·S7·S14·S16)가 **대회 PC 에서** 통과한 뒤에 한다.

## 0. 공통

### 0-1. 사전 조건 (모든 항목)
- 00 SAF-ESTOP 통과, 첫 U턴 전 조향 부호 확인(CLAUDE.md 1.4). 물리 E-stop 담당·앱 담당 배치, 앱 En 해제는 차가 선 뒤에만.
- **원격 유닛 OFF.** 주행 구역 "비었음" 선언. 현장 매뉴얼([`FIELD_MANUAL.md`](../FIELD_MANUAL.md)) 2절 그대로.
- 대회 PC 에서 `bash ~/git/A1-BSW/tools/bsw/bsw_up.sh --sil --race --name REH1 --race-sil-server ~/git/A1-BSW/sil/vcan/race_scenarios/S02_go.yaml` 리허설 통과.
- GNSS RTX fix 녹화(TODO-5, localization 팀). 없으면 ② 의 0x201 대조와 ⑥ 의 갱신 판정은 못 한다.

### 0-2. 공통 클램프·띄우기
| 항목 | 값 |
|---|---|
| 속도 상한 | 시작 30 km/h(CLAUDE.md 1.4). 항목별로 더 낮으면 각 항목에 적는다. **can_guard 는 경기 상한 말고는 속도를 막지 않는다** — PC 담당이 상태판을 본다 |
| `--steer-limit-deg` / `--brake-limit-pct` / `--acc-limit-pct` | 시험 ①~⑤ 는 `--race-opt "--steer-limit-deg 15"`(직선만) / 100 / `--acc-limit` 값 |
| 모델 대역 | 모델 팀 노드가 없으면 `ros2 run a1_control_adapter test_target_pub --ros-args -p mode:=const -p accel_mps2:=0.5 -p steer_tire_deg:=0.0`, 값 변경 `ros2 param set /a1_test_target_pub accel_mps2 -1.0`. 직선에서 조향 0 만 쓴다 |
| 세션 이름 | `--name <테스트ID>_r<회차>` → `~/a1_race_capture/<날짜_시각>_BSW-<테스트ID>_r<회차>/` |
| 산출물 이름(CLAUDE.md 4절) | 원본 `날짜_테스트ID_회차.log`, 예 `20261118_RACE-SLOW-01_r1.log`(세션 폴더의 `kvaser_ch0.log` 를 복사), RTX fix 면 `20261118_RACE-SLOW-01_r1_bag/`, 그래프 `<원본 이름>_내용.png` |
| 결과 위치 | `reports/race_flag/<날짜>_vehicle/results.md`: 커밋 해시, 그날 상한·클램프, 원본 이름, 그래프 링크(CLAUDE.md 7절). 세션의 `SESSION.md` 경기 옵션을 옮겨 적는다 |

### 0-3. 대회 서버 신호가 필요한 시험 (②~⑤, ①2부)
**0x124 흉내 송신은 현장 허락을 받은 뒤에만 한다.** 규정에 없는 것은 허가되지 않는 것이 원칙이다(규정집 1쪽). 허락받은 내용(누가·언제·범위)을 결과에 적는다.

| 방법 | 언제 | 비고 |
|---|---|---|
| 실제 대회 서버 신호 | 6차·최종 테스트(계획서 12.1, 11-23~24) | 우선. 첫 수신 때 실제 프레임을 DBC 정의와 대조(open item O-2) |
| 사람이 별도 CAN 장치로 송신 | 상시 테스트, 허락 후 | **이 저장소에 실차용 송신 도구는 없다**(`fake_race_server.py` 는 vcan 외 채널을 거부 — 일부러 그렇게 만듦). 별도 장치(PEAK 등 `can0`) + `cansend`. 같은 Kvaser 채널에 핸들을 더 열지 않는다(11-03 송신 실패) |

```bash
# 허락 받은 뒤, 별도 장치에서 손으로. 바이트 0 = GO 80 / STOP 40 / FINISH 20 / SLOW_ON 10 / SLOW_OFF 08 (계획서 1.2절)
while :; do cansend can0 124#0000000000000000; sleep 0.02; done   # 먼저 0 을 흘린다 — 해제 방향 깃발은 0 을 본 뒤의 상승만 인정
while :; do cansend can0 124#8000000000000000; sleep 0.02; done   # Go. 앞 루프를 끊고 시작. 2프레임 이상 필요(D12)
```
확인할 것: 차량이 0x124 자체에 반응하는지 모른다 — 첫 송신은 정지 상태에서 하고 0x200 auto 비트·브레이크 위치 변화를 본다.

### 0-4. 공통 중단 조건
현장 매뉴얼 2절 7번 + 아래 중 하나면 "정지" → 앱 / `estop.py` / 물리 E-stop, 그 항목 중단, 원인 전 재개 금지.
- 명령과 반대 방향 움직임, 0x200·0x201 끊김, `foreign_0x210`, `race_internal_error`.
- 항목의 속도 상한 초과.
- 시험 대역이 아닌 다른 송신자의 0x124(대회 서버와 섞임).

끝내기는 항상 차가 선 뒤 `bash ~/git/A1-BSW/tools/bsw/bsw_down.sh`(auto 000 없음). 앞의 ①1부·⑥ 는 시험 도구 경로라 매뉴얼 4-1절 끝내기(`brake 100` 유지 → `q` → `down`).

---

## ① PRE_GO 브레이크 10 / 20 / 30 % 풀림 시간 — RACE-PREGO-01

| 항목 | 내용 |
|---|---|
| **목적** | `--pre-go-brake-pct`(잠정 30 %)를 "차가 안 구르는 최소값"으로 정한다. 값이 작을수록 Go 뒤 풀림이 빠르다(계획서 10절) |
| **사전 조건** | 0-1. 평지(경사 기록). 1부는 대회 신호 불필요 |
| **상한·클램프** | 1부: 차 정지, `--steer-limit-deg 15 --brake-limit-pct 100 --acc-limit-pct 10`. 2부: 10 km/h, `--acc-limit 100`(정지 출발 문턱 70~80 %, 11-04) |
| **합격 기준** | **R-01**: Go 전 움직임 없음(0x201 = 0, `pre_go_motion` 0회). **R-02**: BSW 지연 ≤ 1주기(Go 확인 → 0x210 변화). 풀림 시간·최소값 자체는 **관찰** |
| **산출물** | `20261118_RACE-PREGO-01_r<N>.log`, `_brake_release.png`(명령 % vs 0x200 위치, Go 시각), 표(%, 풀림 시간, 첫 움직임까지 시간, 구름 여부) |

**1부 — 풀림 시간만(시험 도구, 신호 없음, 바퀴 안 굄·앞 공간 비움):**
```
[T1] source ~/git/A1-BSW/bsw_test/field_env.sh; up RACE-PREGO-01_s1 10
[T2] lc --steer-limit-deg 15 --acc-limit-pct 10 --brake-hold-s 60
lift> auto brake on
lift> brake 30           ← 5 s 유지, 위치 안정 + 차 안 구르는지(0x201 = 0)
lift> brake 0            ← 이 시각부터 0x200 위치 0 까지 = 풀림 시간. m r<N> brake 30 풀기
```
30 → 20 → 10 % 각 3회. 끝내기: `lift> brake 100` → `q` → `down`.
- 풀림 시간 = 명령 0 시각 ~ 0x200 위치 0 (Kvaser 로그). 참고: 브레이크 지연 중앙 120 ms·p90 196 ms(11-04 잠정).
- 평지에서 구르는 % 는 후보에서 뺀다. 경사가 있으면 그 경사에서 다시.

**2부 — 실제 Go(0-3 허락 뒤, 후보 % 만):**
1. `bash ~/git/A1-BSW/tools/bsw/bsw_up.sh --race --name RACE-PREGO-01_r<N> --acc-limit 100 --race-opt "--pre-go-brake-pct <%> --steer-limit-deg 15"`
2. 모델 대역 발행(`accel_mps2:=0.5`, Go 전에 큰 값을 내도 막히는지 함께 본다) → `bsw_status` "✅ 출발 준비됨".
3. 30 s 대기: 차 안 움직임, 0x210 가속 0 확인.
4. 0 흘림 → Go → 10 km/h 에서 `ros2 param set /a1_test_target_pub accel_mps2 -2.0` → 정지 → `bsw_down`.

**중단 조건:** 0-4 + Go 전 조금이라도 움직임(0x201 > 0, 눈으로 굴러감) → 그 % 는 탈락, 즉시 정지.

---

## ② 서행 진입·추종 30 → 8 km/h — RACE-SLOW-01

| 항목 | 내용 |
|---|---|
| **목적** | 진입 감속·추종 실측. **0x201 이 정속에서 4.2 % 낮게 읽는지 GNSS 로 확인**(11-04 잠정, ÷0.958), 진입 언더슈트(모델 최저 3.62 km/h ★, 잠정) 측정 |
| **사전 조건** | 0-1, 0-3. RTX fix. 가속 구간 + 진입 + 추종 길이 확보. 앱 신호 거리(50 m, 11-04) 안인지 먼저 확인 |
| **상한·클램프** | 단계 **15 → 20 → 30 km/h** 진입. 이번 회차 상한 = 진입 속도 + 3 km/h, `--acc-limit 100`, `--steer-limit-deg 15` |
| **합격 기준** | 계획서 S5: **9.3 km/h 이하 도달 시간 기록, 이후 10 km/h 초과 0회**(0x201 원값·÷0.958·GNSS 셋 다 표기). R-03·R-04. 시간 자체는 **관찰**("일정 시간" 값이 오면 판정 — 질의서 3). 6.3절 단계 2 조건(서행 로그 3회 이상, 정속 흔들림 p99.9 ≤ 0.5 km/h, 휠-GNSS ≤ 2 %)은 **관찰**, 단계 변경은 사람 승인. 언더슈트·진입 시간은 **관찰** |
| **산출물** | `20261118_RACE-SLOW-01_r<N>.log` + `_bag/`, `_speed.png`(0x201·÷0.958·GNSS, 8.0/8.8/9.3/10 선), `_cmd.png`(가속·브레이크), 표(진입 속도, 도달 시간, 최고·최저 속도, 휠-GNSS %) |

**입력 순서**
1. `bash ~/git/A1-BSW/tools/bsw/bsw_up.sh --race --name RACE-SLOW-01_r<N> --acc-limit 100 --race-opt "--steer-limit-deg 15"`
2. 모델 대역 `accel_mps2:=0.6`(서행 중에도 가속을 계속 내 BSW 가 막는지 본다, S6) → `bsw_status` 통과 → 0 흘림 → Go.
3. 진입 속도(15 / 20 / 30, 각 3회) 도달 → Slow On(`124#1000000000000000`), 추종 **10 s 이상**.
4. 끝: Slow Off 없이 `ros2 param set /a1_test_target_pub accel_mps2 -2.0` 은 서행 중 무시되므로(종방향을 BSW 가 쥠) **`estop.py` 로 세운 뒤** `bsw_down`. 다음 회차는 새 세션.

참고 ★: 가속 0.6 m/s²(11-04 잠정 100 % 0.64)이면 30 km/h 까지 약 14 s·약 58 m + 진입 약 15 m(a_slow 2.0) + 추종. 15·20 km/h 부터 한다.

**중단 조건:** 0-4 + **10 km/h 초과(÷0.958 기준)가 추종 구간에서 한 번이라도** → 그 단계에서 멈추고 `race_flag.yaml` 재검토(승인 절차). 언더슈트로 차가 멈춤 → 기록 후 중단.

---

## ③ Slow Off 램프 — RACE-SLOWOFF-01

| 항목 | 내용 |
|---|---|
| **목적** | 복귀 램프(`slow_off_ramp_pct_s` 50 %/s, 잠정) 실측. 계획서 6.4 지표: "Slow Off → 레이싱 속도 도달 시간", "최대 슬립률" |
| **사전 조건** | ② 통과, 0-3. RTX fix(슬립률) |
| **상한·클램프** | Slow Off 뒤 20 km/h 에 닿으면 끝. `--acc-limit 100`, `--steer-limit-deg 15` |
| **합격 기준** | 계획서 S7: **램프대로 가속 상한 증가, 입력 유예 1 s**. R-04: Slow Off 전 10 km/h 초과 0회. 도달 시간·슬립률은 **관찰**(램프를 줄일 근거, 7절 도구로 추천 → 사람 승인) |
| **산출물** | `20261118_RACE-SLOWOFF-01_r<N>.log` + `_bag/`, `_ramp.png`(가속 상한 이론선 vs 0x210 가속 %), `_slip.png`(후륜 vs GNSS) |

**입력 순서**
1. ② 1~2 와 같게 띄움(세션 이름만 다름), 모델 대역 `accel_mps2:=0.6`.
2. Go → 15 km/h → Slow On → 8 km/h 추종 5 s → Slow Off(`124#0800000000000000`).
3. 20 km/h 도달 → `ros2 param set /a1_test_target_pub accel_mps2 -2.0` → 정지 → `bsw_down`. 3회.

**중단 조건:** 0-4 + 0x210 가속 % 가 이론 램프보다 빠르게 오름, 입력 유예 뒤 정상 입력에 래치됨(`input_clamped` 와 함께 안전 축이 HOLDING).

---

## ④ `kill -STOP` 0.2 / 0.4 s — RACE-GAP-01

| 항목 | 내용 |
|---|---|
| **목적** | 짧은 끊김 복구 실측(R-07, D3). 이벤트의 끊긴 시간·이탈이 계산값과 맞는지 본다. `--resume-low-speed` 를 켤지 판단하는 재료(계획서 8.3: SIL·저속 실차 통과 후 사람이 켠다) |
| **사전 조건** | ② 통과, 0-3. **세우는 대상은 모델 대역(또는 모델 노드)이다. can_guard·어댑터에 하지 않는다** |
| **상한·클램프** | **10 km/h 이하**(계획서 WP8), `--acc-limit 100`, `--steer-limit-deg 15`. 직선만, 조향 0 |
| **합격 기준** | 계획서 S14: **예산 안 = 복구, 밖 = 정지 확정**. 8.2 표: 복구 구간 브레이크 0·가속 0, 신선한 입력 3주기 뒤 ACTIVE / 정지 확정 브레이크 30 → 60 %, ≤ 3 km/h 에서 STOPPED. 어느 쪽이 되는지는 아래 기대값과 비교 |
| **산출물** | `20261118_RACE-GAP-01_r<N>.log`, `_gap.png`(명령·0x201·안전 축, STOP/CONT 시각), 표(설정, STOP 길이, `recover`/`commit` 의 gap·dev, 정지 거리) |

**기대값 ★** (이탈 예산 0.30 m, 지연 0.25 s, `recovery.budget_gap_s`)

| 설정 | 복구 허용 끊김 | STOP 0.2 s | STOP 0.4 s |
|---|---|---|---|
| 기본(`--track-min-radius-m` 없음, a_err 3.0) | 0.197 s | **경계** — 복구/정지 확정 둘 다 가능, 이벤트 gap 으로 판정 | 정지 확정 |
| `--track-min-radius-m 20` **(가정값, 모델 팀 5-1 답이 오면 그 값으로)** 10 km/h | 0.997 s | 복구 | 복구 |

주의: 끊김 ≤ 0.15 s ★ 는 어댑터 유지(`hold_after_s` 0.10)와 watchdog(0.05)에 가려 이벤트가 없다.

**입력 순서**
1. `bash ~/git/A1-BSW/tools/bsw/bsw_up.sh --race --name RACE-GAP-01_r<N> --acc-limit 100 --race-opt "--steer-limit-deg 15"`
2. 모델 대역 `accel_mps2:=0.3` → Go → 8~10 km/h 에서 일정해지면 `accel_mps2` 를 낮춰 유지, 넘으면 `-1.0`.
3. 안전 담당 "지금" → 다른 터미널:
   ```bash
   pkill -STOP -f test_target_pub; sleep 0.2; pkill -CONT -f test_target_pub
   ```
4. 복구면 그대로 정속 → `accel_mps2 -2.0` 으로 정지. 정지 확정이면 차가 설 때까지 기다림(자동 복귀 없음).
5. `bsw_down`. 0.2 s 3회, 0.4 s 3회, 회차마다 새 세션.

**중단 조건:** 0-4 + 조향이 고정값에서 벗어남, 정지 확정인데 브레이크 위치가 안 오름, `kill -CONT` 가 안 먹힘(모델 대역이 멈춘 채면 어차피 정지 확정 — 차가 설 때까지 킬스위치 대기), 10 km/h(÷0.958) 초과.

---

## ⑤ 0x203 반영 + 해제값 — RACE-LIMIT-01

| 항목 | 내용 |
|---|---|
| **목적** | 0x203 → 0x202 반영 확인(계획서 1.3: 실제로 제한됨, 송신을 끊어도 유지). **해제값**(100 인지 0 인지, 업체 답), 제한 초과 때 차량이 브레이크를 거는지, 0x202 = 30.0 과의 관계(O-1) |
| **사전 조건** | **대회측 허락(질의서 9-1) + 업체 해제값 답. 없으면 하지 않는다**(`--speed-limit-tx` 금지, 계획서 13절). 0-3. 끝낼 때 값이 차량에 남으므로 **해제 확인 전에 차를 넘기지 않는다** |
| **상한·클램프** | 정지 → 15 km/h, `--acc-limit 100`, `--steer-limit-deg 15` |
| **합격 기준** | 계획서 9.3·S16: **3프레임 연속 반영, 해제, 어긋남 재송신**. 시작 점검 S17: 잔류 제한 때 tx off 거부(종료 코드 5) / tx on 해제 후 통과. 해제 지연 1 s 는 기록용(D14). 제한 초과 때 차량 동작은 **관찰** |
| **산출물** | `20261118_RACE-LIMIT-01_r<N>.log`, `_limit.png`(0x203 목표 vs 0x202, 차속), 표(반영 ms, 해제 ms, 재송신 횟수, `speed_limit_*` 이벤트) |

**입력 순서**
1. 정지 상태: `bash ~/git/A1-BSW/tools/bsw/bsw_up.sh --race --name RACE-LIMIT-01_r<N> --acc-limit 100 --race-opt "--speed-limit-tx --speed-limit-release-kph <업체 답> --steer-limit-deg 15"` → 이벤트 `speed_limit_startup` 확인, `bsw_status` 표의 0x202.
2. 정지 상태 Slow On → 0x202 = 9.3 (3프레임) → Slow Off → 0x202 = 해제값.
3. 모델 대역 → Go → 15 km/h → Slow On → 진입 → 8 km/h 추종 → Slow Off → 20 km/h 전에 정지.
4. `bsw_down` 뒤 **0x202 를 읽어 해제값인지 확인**. `bash ~/git/A1-BSW/tools/bsw/bsw_up.sh --race --name RACE-LIMIT-01_r<N>b`(tx off)가 통과하는지 확인 후 바로 `bsw_down`.

**중단 조건:** 0-4 + 0x202 가 목표로 안 감(1 s 넘게 어긋남이 지속), 해제가 안 됨 → **차량에 제한이 남은 것으로 보고 주행 중단, 업체 확인**, `foreign_0x203`.

---

## ⑥ 브레이크 계단 — LON-DEC-03

| 항목 | 내용 |
|---|---|
| **목적** | 11-04 는 표본 부족으로 갱신 판정이 막혔다. `tools/race_flag/fit_long_profiles.py` 입력을 모은다(구간별 ≥ 3 이벤트: 브레이크 1~20·20~40·40~60·60~82·82~100, 가속 75~85·85~95·95~100). 참고 11-04 잠정: 브레이크 100 % 감속 2.95 m/s²(표 4.0 대비 낙관) |
| **사전 조건** | 0-1, **RTX fix + bag**(계획서 7.2 GNSS 품질 ≥ 90 %), 평지(피치 ≤ 2°), 앱·원격 조작 없음. `--race` 아님 |
| **상한·클램프** | 30 km/h 안, `v=20`, `vmax=25`. `up … 100`(가속 한계 100 %, 정지 출발 문턱 때문), yaml limits 브레이크 100, 조향 15° |
| **합격 기준** | 시험 자체 합격 없음. `fit_long_profiles.py` 종료 코드 0 또는 표본 부족 해소가 목표(**관찰**). 값 반영은 도구 통과 + 사람 승인 + `docs/race_flag_profile_history.md` 기록(D9) |
| **산출물** | `20261118_LON-DEC-03_r<N>.log` + `_bag/`, `reports/race_flag/20261118_profile_fit/`(`report.md`, `summary.json`, 그래프, 막히면 `UPDATE_BLOCKED.md`) |

**입력 순서**
```
[T1] source ~/git/A1-BSW/bsw_test/field_env.sh; up LON-DEC-03_s1 100
[T2] chk brake_step.yaml --var acc=100 --var v=20 --var vmax=25 --var brake=10
[T2] gen brake_step.yaml LON-DEC-03 --var acc=100 --var v=20 --var vmax=25 --sweep brake=10,30,50,70,90 --reps 3 $TURN --turn-var acc=<5 km/h %>
[T2] gen acc_step.yaml LON-DEC-03 --first-rep 16 --sweep acc=80,90,100 --reps 3 $TURN --turn-var acc=<5 km/h %>
q → down
```
분석(차 세운 뒤, CAN 안 닿음):
```bash
python3 ~/git/A1-BSW/tools/race_flag/fit_long_profiles.py --can <세션 폴더> --bag <bag> --out ~/git/A1-BSW/reports/race_flag/20261118_profile_fit
```
- 50 m 코스면 약한 브레이크는 "코스 끝 예측" 으로 일찍 끝난다(정상). 이벤트는 유지 1 s 이상 + 3 km/h 이상이면 쓰인다.
- 종료 코드 3(⛔ 갱신 중단)이면 `UPDATE_BLOCKED.md` 를 보고 원인 해결 전까지 값을 바꾸지 않는다.

**중단 조건:** 0-4 + 휠락(소리·자국) → 그 % 이상 건너뜀, 정지 거리가 제동 구간을 넘을 것 같음 → 그 조합 건너뜀.
