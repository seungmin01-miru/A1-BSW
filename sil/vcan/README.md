# sil/vcan — vcan0 SIL 환경

실물 CAN 없이 `vcan0`(가상 버스) 위에서 실차 A1 프로토콜(`DBC/A1_dbc_2_fixed.dbc`)을 흉내 내 can_guard·브리지·리프트
도구를 시험하는 도구. 2026-10-07 EAIT DBC 시절 도구(`eait_tx.py`·`eait_rx.py`)를 지우고 A1 기준으로 바꿨다(git 기록).

## 준비 (1회)

```bash
sudo apt install -y can-utils                       # candump / cansend / cansniffer / cangen
python3 -m pip install --user cantools python-can   # 설치 확인: python3 -c "import can, cantools"
```

**DBC**: `DBC/A1_dbc_2_fixed.dbc` (대회 배포 `A1_dbc_2.dbc` 의 오타만 고친 사본 — 0x210 조향 ×0.1 은 10-07 실차로 확정, 근거는
`docs/2026-10-06_a1_dbc_update_and_lift_plan.md` §0). 이 DBC 에도 `GenMsgCycleTime` 이 없어 주기는 10-07 실차 녹화
실측값(0x200·0x201·0x210 ≈ 20 ms)을 쓴다.

## 도구

| 파일 | 역할 |
|---|---|
| `vcan_up.sh` | `vcan0` 생성(멱등). `--install` 이면 부팅 시 자동 생성(systemd `vcan0.service`) |
| `roundtrip_check.sh` | `cansend` → `candump` 왕복 자동 판정 |
| `fake_a1_vehicle.py` | **가짜 실차** — 0x200·0x201 을 20 ms 로 송신(카운터 포함), 0x210 을 받으면 auto 비트가 켜진 축만 반응(조향 1차 지연 추종, 브레이크, 단순 속도 적분). `--interface kvaser --channel 1` 이면 Kvaser CANlib 가상 채널에서 동작. 경기 깃발 확장은 아래 절 |

```bash
sudo bash sil/vcan/vcan_up.sh                       # 1회(이미 enabled 면 불필요)
bash sil/vcan/roundtrip_check.sh
python3 sil/vcan/fake_a1_vehicle.py --channel vcan0 &
candump vcan0 | python3 -m cantools decode --single-line DBC/A1_dbc_2_fixed.dbc
```

이 도구들을 쓰는 자동 리허설: `tools/race_day/rehearse_lift_guard.sh`(can_guard + lift_cmd, C0~C7 재현),
`tools/race_day/rehearse_lift_tx.sh`(비상용 lift_tx). can_guard 의 P-1·P-2 SIL: `safety/can_guard/sil_tests/`.

## 경기 깃발 SIL (2026-11-06, `docs/2026-11-06_race_flag_dev_plan.md` WP7)

| 파일 | 역할 |
|---|---|
| `fake_race_server.py` | **가짜 대회 서버** — yaml 시나리오대로 0x124(깃발)·0x127(RES, alive)·0x125·0x126 재생. 신호 방식 level / pulse / flicker, 0x124·0x127 끊김, alive 고정. 형식은 파일 머리말 |
| `race_scenarios/*.yaml` | 계획서 S1~S18(변형 포함) + X01(기록 전용 0x125·0x126). `harness:` 는 `safety/can_guard/sil_tests/r_race.py` 용(차량 인자·can_guard 인자·합격 기준·S14 조합) |
| `fake_a1_vehicle.py` | 기존 기능 + 0x202 보고·0x203 반영·RES 중 0x210 무시와 자체 감속·0x128 |
| `safety/can_guard/sil_tests/r_race.py` | **S1~S18 자동 판정.** 기본 `virtual`(같은 프로세스), `--interface socketcan --channel vcan0`(대회 PC, 프로세스), `--with-ros`(실제 어댑터·/race/state). 결과 `reports/race_flag/2026-11-06_sil/` |
| `test_fake_race_server.py`, `test_fake_vehicle_race.py` | vcan 없이 도는 시험(가상 시계·`virtual` 버스). `testdata/fake_vehicle_golden.json` = 확장 전 코드의 프레임 스냅샷 |

```bash
python3 sil/vcan/fake_a1_vehicle.py --channel vcan0 --physics &              # 0x202 = 30.0 보고만, 제한 없음
python3 sil/vcan/fake_a1_vehicle.py --channel vcan0 --physics --speed-limit-kph 9.3 --speed-limit-latency 0.05 &   # 잔류 제한
python3 sil/vcan/fake_race_server.py sil/vcan/race_scenarios/S02_go.yaml     # vcan0 에 재생, 끝나면 종료
python3 -m pytest -q sil/vcan/test_fake_race_server.py sil/vcan/test_fake_vehicle_race.py
```

vcan 이 없는 PC 에서는 같은 프로세스에서 쓴다. `step(now)` 은 now 까지 밀린 주기를 모두 처리하므로 가상 시계로도, 호출 빈도와 상관없이 같은 프레임 열이 나온다.

```python
server = RaceServer(can.Bus(channel='r1', interface='virtual'), 'sil/vcan/race_scenarios/S09_res_then_release.yaml')
car = FakeVehicle(can.Bus(channel='r1', interface='virtual'), period_s=0.021, physics=True)
server.step(now); car.step(now)      # now 는 호출자가 준다. car.kph, car.res_active, car.speed_limit_kph 로 읽기
```

주의:
- `fake_race_server.py` 는 `vcan*`·`virtual` 만 받는다. 다른 채널은 종료 코드 2 로 거부한다(실차 CAN 금지).
- **0x202 = 30.0 은 표시값이다.** 11-04 실측이지만 원격 조종 제한이고 자율 상한이 아니다(2026-11-06 사용자 확인). `--speed-limit-kph` 를 생략하면 0x203 을 받기 전까지 `--physics` 에서도 제한을 걸지 않는다(기존 `--physics` 동작 그대로). 값을 주면 처음부터 제한한다.
- 제한보다 빠를 때는 구동만 끊겨 관성으로 내려온다. 차량이 브레이크를 거는지, 해제값이 100 인지 0 인지는 업체 답변 전이다 — 0x203 raw 0 은 말 그대로 0 km/h 제한으로 처리한다.
- RES 감속 0.87 m/s²·브레이크 보고 82 % 는 **잠정**(앱 인수 11-04 실측을 빌린 값, RES 실측 아님). 0x127 이 끊기면 마지막 값을 유지한다(끊김 동작 정의 없음).
- yaml 의 `off`(따옴표 없음)는 False 로 읽히며, 로더가 같은 뜻으로 받는다.
- 확장 뒤 가짜 차량은 0x202·0x128 도 보낸다(11-04 실차 버스와 같음). 0x200·0x201 은 프레임 내용·순서 그대로다.
- `--physics` 기본 표는 실측보다 강하다(75 % 에서 2.4 m/s², 실측은 약 0). 실측에 맞춘 조건은 `--accel-map`·`--brake-map`·`--drag`·`--drive-delay`, 값은 `r_race.py` 의 `MEASURED`(★ 잠정).

## RT 실행 조건 실측 (2026-09-12, EAIT 시절 기록 — 레시피는 지금도 유효)

당시 송신기(`eait_tx.py`, 삭제됨)로 잰 송신 주기 편차. 같은 레시피(격리 코어 + 타이머 여유 1 µs + SCHED_FIFO +
mlockall)가 `safety/can_guard/rt_setup.py`, `ros2_ws/.../rt_utils.py` 에 그대로 들어 있다.

| 조건 | 송신 주기 편차 평균 / 최대 |
|---|---|
| 비격리 코어(0–7), 일반 우선순위 | 61 / 88 µs |
| 격리 코어, 일반 우선순위 | 57 / 63 µs |
| 격리 코어 + 타이머 여유 1 µs | 17 / 27 µs |
| 격리 코어 + SCHED_FIFO 80 | **5 / 9 µs** |

일반 우선순위 태스크는 커널이 기본 50 µs 타이머 여유를 두어 `sleep` 이 늦게 깬다 — 편차 ~57 µs 의 정체.
**RT 우선순위로 측정할 때**: 이 PC는 계정에 rtprio 를 영구 부여하지 않는다(2026-09-12 결정). 측정할 때만
`sudo chrt -f 90 sudo -u ailab python3 …` 처럼 한 번씩 올린다.

## 한계
vcan은 로컬 루프백이라 버스 전송지연·중재·에러프레임이 없다. 실시간성·버스 동작 최종 검증은 실물 CAN(PEAK `can0`/`can1`
또는 Kvaser)에서 재측정.
