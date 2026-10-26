# 트랙 매뉴얼 — 제어기 출력을 실차 CAN 에 물리기 (v1, 2026-10-26)

목표: 트랙에서 **표 측정(T1~T4) → shadow 주행(T5) → 축별 arm 저속 주행(T6)** 순서로 제어기를 실차에 붙인다.
제어기는 아직 미완성 — 출력 형식만 확정(`/control/target`: 가감속 m/s² + 타이어각 °). 제어기가 없어도 T1~T5 는
할 수 있고, T6 은 `test_target_pub`(제어기 대역)로 경로만 확인할 수 있다.

```
제어기 ─/control/target─▶ a1_cmd_adapter ─공유메모리 50 Hz─▶ can_guard ─0x210 20 ms─▶ 차량
          (가감속, 타이어각)   (표 변환·arm·고장 래치)            (상태머신·한계·HOLDING)
```

차량 특성·지연·범위 근거: [`docs/a1_vehicle_characteristics.md`](../../docs/a1_vehicle_characteristics.md).
CAN 연결·녹화·원격 판정 등 기본 절차는 [`tools/race_day/VEHICLE_MANUAL.md`](../race_day/VEHICLE_MANUAL.md) 를 그대로 쓴다.

## 안전 규칙 (매 단계)

1. **0x210 송신자는 하나.** 원격조종(0x156/0x157)은 0x210 보다 우선하고, 다른 0x210 송신자가 보이면 can_guard 가 시작을
   거부한다. 원격 E-stop 담당 1명은 항상 대기.
2. **어댑터와 lift_cmd 는 동시에 못 쓴다**(같은 공유메모리). T1~T4 = `--no-adapter` + lift_cmd, T5~T6 = 어댑터.
3. **0x210 타임아웃이 없다**(10-07: 60 s 끊겨도 auto 유지). 끝낼 때는 항상 **disarm(또는 lift_cmd `auto off`) 먼저**,
   그다음 `stop.sh`. can_guard 가 STOPPED(hold) 였다면 0x200 auto 비트가 풀렸는지 눈으로 확인.
4. 매 시작 때 **조향 배율 점검(T1)** — 9/17 과 10/07 사이에 차량 배율이 10배 바뀐 적이 있다.
5. 중단 기준: 명령과 반대 방향으로 움직임 / 0x200 카운터 정지 / 에러프레임 증가 / 다른 0x210 송신자 경고 /
   can_guard 송신 실패 누적. → 즉시 disarm·`auto off`, 원격 E-stop.

## 출발 전 (이 PC, 약 3분)

```bash
cd ~/git/A1-BSW
python3 -m pytest -q -p no:cacheprovider ros2_ws/src/a1_control_adapter/test safety/can_guard/test   # 유닛테스트
bash tools/track_day/rehearse_track.sh      # 어댑터 경로 SIL 리허설 17항목, 약 1분 30초 → PASS 17 / FAIL 0
bash tools/race_day/rehearse_lift_guard.sh  # lift_cmd 경로(T1~T4) 리허설
```
챙길 것: 각도계(타이어각 T2), 줄자·마스킹테이프(핸들 0점 표시), 축거 L·조향비 업체 제원 질문지, 이 문서 출력본.

## T0. 띄우기 (10분)

```bash
# 재부팅 후 1번 — Kvaser 를 쓸 때 (VEHICLE_MANUAL "재부팅할 때마다")
sudo ip link add dev kv0 type vcan 2>/dev/null; sudo ip link set kv0 up
# 표 측정용: 브리지만 + 녹화 (lift_cmd 로 명령)
bash tools/track_day/bringup.sh --kvaser 0 --no-adapter      # PEAK 면 --peak can0
```
- `bringup.sh` 가 하는 일: 차량 0x200/0x201 수신 확인 → can_guard(격리 코어 8, SCHED_FIFO 90, sudo) → 상태 브리지
  (코어 10) → 녹화(`~/a1_race_capture/track_<시각>/`: bag + Kvaser 원본 + can_guard 로그 + 쓰인 표 사본).
- 실패 메시지별: `0x200/0x201 이 안 보임` → 케이블·전원·비트레이트 / `시작 거부` → 다른 0x210 송신자(원격 유닛이
  0x210 을 내는지, 미확인 송신자) / `sudo 필요` → 비밀번호 입력 또는 `--no-rt`(그 세션 지연 값은 '비격리'로 기록).
- 메모는 수시로: `echo "$(date +%s.%N) $(date +%H:%M:%S) T2 왼쪽 60도" >> ~/a1_race_capture/track_latest/notes.txt`

## T1. 조향 배율·부호 (정지, 5분) ★매 세션

lift_cmd(bringup 이 출력한 명령줄 그대로)에서:
```
auto steer on
steer 1        → 0x200 조향 위치가 약 +1.1° 근처인가 (×0.1 동작). +11° 면 ×1 동작 → 중단, 업체 확인
steer 10       → 약 +11°.  이때 앞바퀴가 **왼쪽**으로 도는가?
steer 0
auto off
```
- 위치 = 명령 × 약 1.1 이면 정상(10-07 기준). 불감대 때문에 1° 는 안 움직일 수 있다 → 10° 로 판정.
- **부호**: 핸들 + 에서 바퀴가 왼쪽 → `steer_sign: 1.0`(제어기 + = 좌회전과 같음). 오른쪽이면 `-1.0`.
  세션 폴더의 `track_params.yaml` 에 적는다.

## T2. 조향비 표 (정지, 20분)

평지, 정지, 사람이 타이어 옆에서 각도계로 측정. lift_cmd 로 핸들각을 차례로:
```
auto steer on
steer 30 / steer 60 / steer 90 / steer 120 / steer 150   (각각 왼·오른 앞바퀴 타이어각 기록)
steer -30 / ... / steer -150
steer 0 / auto off
```
- 조향은 5초 뒤 자동 원위치(lift_cmd 안전 기능) → 재는 동안 같은 명령을 다시 입력해 연장.
- 실제 핸들 위치는 0x200 값(상태줄)으로 기록한다(명령 × 1.1 이라 명령값과 다르다).
- CSV(`t2_steer.csv`)로 정리 → 표 생성:
  ```
  wheel_deg,left_tire_deg,right_tire_deg
  -165,-21.0,-18.0
  ...
  python3 tools/track_day/fit_maps.py steer t2_steer.csv
  ```
  출력 `steer_map:` 조각을 `track_params.yaml` 에 붙인다. 좌우 평균을 등가 타이어각으로 쓴다.
- 축거 L 을 업체에서 받으면 T5 주행 뒤 `fit_maps.py steer-dyn <bag> --wheelbase L` 로 요레이트 역산과 대조.

## T3. 브레이크 표 (직선, 20분)

직선 구간, 사람·원격이 약 20 km/h 로 만든 뒤 원격 해제 → lift_cmd 로 브레이크 계단. 계단마다 **2초 이상 유지**:
```
auto brake on
brake 20   (2~3 s, 정지하면 다음 회차)      → 다시 가속해서
brake 40 / brake 60 / brake 80 / brake 100   (회차마다 하나씩, 가능하면 2회씩)
brake 0 / auto off
```
- 표 생성: `python3 tools/track_day/fit_maps.py long ~/a1_race_capture/track_latest/bag` → `brake_map:` 조각.
- 정지 상태 구간·1 m/s 미만은 자동으로 버린다. GNSS(`/gsof_client/odom`)가 녹화돼야 한다(0x201 은 0).

## T4. 가속 표 (직선, 20분)

정지 또는 저속에서 lift_cmd `auto acc on` → `acc 10` / `acc 20` / `acc 30`(각 2~3 s, 회차마다 하나) → `acc 0` → 브레이크.
- 가속은 2초 뒤 자동 원위치 — 2초 안에 다시 입력해 연장(`--acc-hold-s` 로 늘릴 수도 있음).
- `fit_maps.py long` → `accel_map:` 조각. **`acc_max_pct` 는 측정한 최대 % 까지만** 올린다(기본 30).
- 0x201 바퀴속도가 이때 0 이 아닌지도 확인(10-07 미해결 항목).

T1~T4 끝: lift_cmd `auto off` → `q` → `bash tools/track_day/stop.sh`.

## T5. shadow 주행 (사람·원격 운전, 15분)

```bash
bash tools/track_day/bringup.sh --kvaser 0 --params ~/a1_race_capture/<T1~T4 세션>/track_params.yaml
```
- 어댑터는 **disarm 으로 시작**(auto 0 송신 = 차량 무시). 제어기를 띄우고 사람/원격이 주행.
- 확인: `ros2 topic echo /control/adapter/status` — `input_age_s` 가 0.15 미만으로 유지, `out_*` 가 그럴듯한지,
  `rejected_count` 0. 제어기 주기가 10 Hz 이상이면 된다(어댑터가 50 Hz 로 다시 쓴다).
- 제어기가 없으면 `ros2 run a1_control_adapter test_target_pub` 로 경로만 확인.
- 녹화로 나중에 제어기 지령 타이어각 vs 실제(0x200 핸들각 → 표 역변환)를 비교한다.

## T6. 축별 arm 저속 (원격 대기, 시간 남는 만큼)

순서: **종방향만 → 조향만 → 둘 다**, 속도 상한은 제어기 쪽에서 20 km/h 부터.
```bash
ros2 service call /control/adapter/arm_long  std_srvs/srv/SetBool "{data: true}"
ros2 service call /control/adapter/arm_steer std_srvs/srv/SetBool "{data: true}"   # 0x200 조향 위치가 보여야 수락
ros2 service call /control/adapter/disarm    std_srvs/srv/Trigger "{}"            # 즉시 해제(둘 다)
```
- arm 은 제어기 입력이 신선할 때만 수락. 조향 arm 순간 핸들은 현재 위치에서 120 °/s 로 목표까지 간다.
- **제어기가 150 ms 이상 끊기거나 NaN·범위 밖 값** → 어댑터 고장 래치(기록 중단) → can_guard 50 ms 뒤 HOLDING
  (조향 고정·가속 0·브레이크 30 % 램프) → 2 s 뒤 또는 정지 시 STOPPED. 입력이 돌아와도 자동 복귀하지 않는다.
  복귀: `disarm` 호출 → can_guard 가 STOPPED 면 `stop.sh` 후 다시 `bringup.sh`.
- 지연 확인(정지 상태에서만): `python3 tools/track_day/measure_latency.py --channel kv0 --vehicle-stationary --csv <세션>/latency_rt.csv`
  → `docs/a1_vehicle_characteristics.md` 4절의 '격리 코어' 값으로 기록.

## 끝내기

1. `disarm`(T5~T6) 또는 lift_cmd `auto off`(T1~T4) → 0x200 auto 비트 000 확인
2. `bash tools/track_day/stop.sh`
3. 세션 폴더 백업 2부(외장·다른 PC). 고친 `track_params.yaml` 은 저장소 `ros2_ws/src/a1_control_adapter/config/track.yaml`
   에 반영하고 근거(세션 폴더·날짜)를 주석으로 남긴다.

## 도구 목록 (`tools/track_day/`)

| 파일 | 역할 | 버스 송신 |
|---|---|---|
| `bringup.sh` / `stop.sh` | 띄우기·정리(disarm → 녹화 → 노드 → can_guard → 미러) | can_guard 만 |
| `record.sh` | bag(mcap) + candump. 보고서 형식 재구성에 필요한 토픽 | ❌ |
| `fit_maps.py` | 녹화·실측 CSV → `steer_map`/`brake_map`/`accel_map` YAML 조각 | ❌ |
| `measure_latency.py` | `/control/target` → 0x210 종단 지연(브레이크 계단). vcan 아니면 `--vehicle-stationary` 필요 | 어댑터 경유 |
| `rehearse_track.sh` | vcan0 SIL 리허설 17항목 | 가상 버스만 |
| `../../ros2_ws/src/a1_control_adapter` | `cmd_adapter`(변환·arm·고장 래치), `test_target_pub`(제어기 대역), `config/track.yaml`(표) | ❌(공유메모리) |
