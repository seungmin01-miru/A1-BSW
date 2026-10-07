# 실차 인수 당일 매뉴얼 (v4, 2026-10-07 — 실험 3시간, 리프트 송신 시험, PEAK / Kvaser 둘 다)

내일 목표는 세 가지다.

1. **원격조종 시스템이 CAN에 직결되는지 판정** — 직결이면 그 자리에서 리버스 엔지니어링용 주행 로그를
   뜨고, 아니면 원격 시스템의 출력 형식을 기록해 와서 우리 레이어에 맞춘 뒤 리버스 엔지니어링한다.
2. **받은 DBC 검증** — 업체 DBC(`DBC/A1_dbc.dbc`, 10-06 수령)를 실차 버스로 대조하고, 오류(0x210 조향 배율)와
   빠진 것(0x004·0x204, SYS 계열의 위치)을 업체에 확인한다(아래 "B. DBC 검증").
3. **검증 체크리스트(`2026-09-14_can_verification_checklist.md`) 미결 항목 확인** — 원격이 되면 차량
   fail-safe·E-stop·우선권 등을 실제로 관측한다(아래 "체크리스트 대응표").
4. **리프트 송신 시험(C단계)** — 차량을 리프트에 올린 상태에서 can_guard 로 0x210 을 보내 기본 명령이 먹히는지
   확인한다(아래 "C. 리프트 송신 시험"). 계획·근거: `docs/2026-10-06_a1_dbc_update_and_lift_plan.md`.

**송신은 C단계에서만, 전제 조건을 전부 통과한 뒤에만 한다.** 그 외 시간은 끝까지 listen-only.

> ⚠️ **업체 DBC(`DBC/A1_dbc.dbc`)의 0x210 조향 배율 ×0.1 은 틀렸다 — 실제는 ×1 deg**(9/17 로그 1,354구간에서
> 위치/명령 비율 1.000). 그대로 쓰면 핸들이 10배로 돈다. 우리 코드·현장 디코드는 전부 `DBC/A1_dbc_fixed.dbc` 기준.
> (9월의 EAIT DBC 는 2026-10-07 저장소에서 지웠다 — 모든 도구·문서는 A1 DBC 기준.)

### CAN 장치 — PEAK(기본) 또는 Kvaser
| | PEAK PCAN-PCIe FD(내장) | Kvaser Leaf v3(USB) |
|---|---|---|
| 이 PC 에서 보이는 이름 | `can0`, `can1`(SocketCAN) | CANlib 채널 `0`, `1`… — **SocketCAN 이 아님**(6.8 커널 `kvaser_usb` 미지원, 0x0117) |
| 드라이버 | 커널 내장 | `sudo bash tools/kvaser/install_kvaser.sh`(1회) |
| 이 매뉴얼의 명령 | 그대로 | **미러를 먼저 띄우고 `can0` → `kv0`** 로 바꿔 쓴다(아래 각 단계의 "Kvaser:" 줄). 상세: `tools/kvaser/README.md` |

차량 커넥터·케이블이 Kvaser 기준이 아니면 PEAK 를 기본으로 쓴다(검증·리허설이 더 많이 된 경로).

---

## 시간표 (총 180분)

★ = 반드시, ☆ = 시간 남으면. 늦어지면 ☆부터 버린다.

| 시각 | 분 | 할 일 | 담당 |
|---|---|---|---|
| 0:00 | 15 | ★ 0단계 핵심 질문(R1·R2·R3, V1·V2·V3) — **동시에** PC 전원·케이블 준비 | 질문 담당 / PC 담당 |
| 0:15 | 15 | ★ 1단계 종단 측정 → 연결 → listen-only → 비트레이트 | PC |
| 0:30 | 5 | ★ 2단계 녹화 시작 + S0 기준선(1분) + 주기 측정 | PC |
| 0:35 | 15 | ★ A-1 원격 판정 T1~T3 | PC + 대회 측 |
| 0:50 | 70 | 갈래별 실험 — **A-직결**(★ 시나리오 위주, ☆는 시간 될 때) 또는 **A-비직결**. 질문 담당은 **동시에 B(DBC 검증)** | 전원 |
| 2:00 | 25 | ★ **C. 리프트 송신 시험** C0~C7 (업체 허락·리프트·E-stop 확인 후. 안 되면 건너뛰고 질문으로) | 전원 |
| 2:25 | 15 | ☆ 남은 질문(V4~V8, R4~R6, 규정) + 못 한 시나리오 보충 | 질문 담당 |
| 2:40 | 20 | ★ 3단계 마무리·백업 2부, answers.md 빈칸 확인 | PC |

리프트 시험을 원격 판정보다 뒤에 두는 이유: 원격이 직결이면 원격이 0x210 을 보내므로 **원격을 끈 뒤에만** 우리가
송신할 수 있다(can_guard 가 다른 0x210 송신자를 보면 시작을 거부한다). 원격 데이터를 먼저 다 받고 끈다.

**인원 2명 권장**: PC 담당(녹화·메모 `m`·cansniffer 관찰) / 질문 담당(대회 측·업체 대응, `answers.md` 작성,
사진). 1명이면 B(DBC 검증)는 실험 중 대기 시간에 끼워 넣는다 — B-1 은 S0 기준선 녹화 중에 같이 해도 된다.

---

## 재부팅할 때마다 (PC 를 켤 때마다, 1분)

재부팅하면 사라지는 것만 다시 만든다. 나머지(`vcan0`, RT 튜닝, Leaf v3 드라이버 자동 로드)는 부팅 때 저절로 된다.

```bash
# ① Kvaser 를 쓸 때만 — kv0 다시 만들기 (재부팅하면 사라짐. 없으면 미러가 시작 즉시 종료 → CAN 로그가 안 남는다)
sudo ip link add dev kv0 type vcan 2>/dev/null; sudo ip link set kv0 up
ip -br link show kv0                                      # 상태가 UP 또는 UNKNOWN 이면 OK

# ② Kvaser 리허설을 돌릴 때만 — 가상 채널 모듈 (부팅 때 자동 로드 안 됨. 실제 Leaf v3 는 꽂으면 자동)
sudo modprobe kvvirtualcan

# ③ 확인 (아무것도 안 바꿈)
ip -br link show vcan0                                    # 리허설용 가상 버스 — 부팅 때 자동 생성
bash ~/git/A1-BSW/tools/kvaser/install_kvaser.sh check    # Kvaser 를 쓸 때: libcanlib 설치됨, 채널 목록
```

| 재부팅 후 | 할 일 |
|---|---|
| `kv0` | **사라짐 → ①** (Kvaser 를 쓸 때만) |
| Kvaser 가상 채널(`kvvirtualcan`) | **자동 로드 안 됨 → ②** (리허설 때만) |
| `can0`/`can1` 비트레이트·listen-only | 사라짐 → 매뉴얼 **1단계**에서 다시 설정(현장 절차에 이미 있음) |
| 터미널의 `D`·`m` | 터미널마다 사라짐 → "현장 폴더·메모 명령" 세 줄을 **새 터미널마다** 붙여 넣기 |
| `vcan0`, RT 튜닝(`a1-bsw-rt-tune.service`), Leaf v3 드라이버(`mhydra`) | 자동 — 할 일 없음 |

PEAK 카드만 쓴다면 ①·② 는 필요 없다.

## 아침 확인 (출발 전 5분, 이 PC에서)

어젯밤 만든 도구가 전부 정상인지 한 번에 확인한다. 전부 vcan0(가상 버스)에서 돌고 실차와 무관하다.
```bash
cd ~/git/A1-BSW
python3 -m pytest -q -p no:cacheprovider safety/can_guard/test tools/race_day/test_lift_tx.py   # 유닛테스트
bash tools/race_day/rehearse_lift_guard.sh      # C단계 리허설(can_guard + lift_cmd), 약 1분 30초
bash tools/race_day/rehearse_lift_tx.sh         # 비상용 lift_tx 리허설, 약 1분
bash tools/kvaser/rehearse_kvaser.sh            # (Kvaser 를 쓸 때만) CANlib 가상 채널 리허설, 약 40초
```
마지막 줄이 각각 `PASS n / FAIL 0` 이면 OK. FAIL 이 있으면 **C단계는 하지 말고** 듣기만 한다.

## 출발 전 (오늘 밤)

### 준비물
- [ ] 멀티미터(종단저항 측정용)
- [ ] CAN 케이블과 변환 커넥터 — PC 쪽 단자(CAN1/CAN2)는 D-Sub 9핀(CiA 303-1 표준: **7번 CAN_H, 2번 CAN_L,
      3번 GND** — PEAK 카드 매뉴얼로 재확인). 차량 하네스 쪽 모양은 모르므로 **브레이크아웃(나사 단자) DB9**과
      점퍼선
- [ ] 120Ω 저항 1~2개
- [ ] (Kvaser 를 쓸 경우) Kvaser Leaf v3 + 케이블, 드라이버 설치 확인 `bash tools/kvaser/install_kvaser.sh check`,
      재부팅했으면 위 "재부팅할 때마다" ① 실행
- [ ] USB 메모리 2개, 휴대폰(사진·시각 확인)
- [ ] PC 전원 방안(차량 12V로는 직접 못 켬 — 업체 콘센트/인버터 확인)
- [ ] **이 매뉴얼의 시간표·S 시나리오 표, `answers_template.md` 출력본**(대회 측 조작자에게 시나리오 표를
      보여 주며 부탁하면 설명 시간이 준다)
- [ ] 시계 맞추기: `timedatectl`에서 `System clock synchronized: yes` 확인(메모 시각과 로그 시각 정합)

### 이 PC에서 미리 해 볼 것 (5분, sudo 필요)
listen-only 설정이 이 카드에서 실제로 되는지 확인(버스 연결 없이 가능).
```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
ip -details link show can0 | grep -o 'LISTEN-ONLY'      # "LISTEN-ONLY" 가 출력되면 OK
sudo ip link set can0 down
```
`Operation not supported`가 나오면 내일 전에 반드시 알릴 것.

### 현장 폴더·메모 명령 미리 만들어 두기
내일 아침 PC를 켜면 터미널에 이것만 붙여 넣는다(폴더 생성 + 답변 양식 복사 + 메모 함수).
```bash
D=~/a1_race_capture/vehicle_$(date +%Y%m%d); mkdir -p "$D"; cd "$D"
[ -f answers.md ] || cp ~/git/A1-BSW/tools/race_day/answers_template.md answers.md
m(){ echo "$(date +%s.%N) $(date +%H:%M:%S) $*" | tee -a "$D/notes.txt"; }
```

---

## 0. 도착하면 핵심 질문부터 (15분)

답에 따라 갈래가 갈린다. 답은 `answers.md`에 바로 적는다. **★만 먼저**, 나머지는 2:20 이후.

### 대회 측 — 원격조종
| # | | 질문 | 왜 |
|---|---|---|---|
| R1 | ★ | 원격 수신기는 **어디에 꽂히나?** 차량 CAN 하네스 / 우리 PC(USB·LAN·WiFi) / PC와 차 사이 박스 | 직결 판정의 1차 근거 |
| R2 | ★ | 원격이 CAN에 보내는 **ID는? 0x210인가?** | 우리 역추적 명령 메시지와 같은지 |
| R3 | ★ | 원격과 자율주행(우리 PC)이 **동시에 명령하면 누가 이기나?** 전환 방법은? | 체크리스트 §9.1 C11, H7 |
| R4 | ☆ | 원격 E-stop 반응 시간·방식(전원 차단 / 브레이크 명령)? | §9.1 C10 — S7에서 관측도 함 |
| R5 | ☆ | 원격 신호가 끊기면 차는? | §9.1 B5 — S7에서 관측도 함 |
| R6 | ☆ | 대회 당일에도 같은 원격 시스템인가? | 오늘 데이터 유효성 |

### 업체 — CAN/DBC
| # | | 질문 |
|---|---|---|
| V1 | ★ | 받은 `A1_dbc.dbc` 가 **이 차량의 최신 버전인가?** 더 새 버전·변경 이력·신호 설명 문서가 있나? |
| V2 | ★ | 비트레이트(DBC 에 없음), classic CAN인지 CAN-FD인지, 종단저항 위치 |
| V3 | ★ | **0x210 `steer_command` 배율이 DBC 는 ×0.1 인데 실측은 ×1 이다 — 어느 쪽이 맞나?** 브레이크·가속 % 의 실제 의미(페달 개도? 압력?) |
| V4 | ☆ | auto 비트(0x210 bit40~42, 0x200 bit32~34)를 켜는 데 다른 조건(키·스위치·모드)이 있나? 0 을 보내면 즉시 수동? |
| V5 | ☆ | 명령이 끊기면 몇 ms 뒤 무엇을 하나? alive counter가 필요한가? 범위·변화율 검증을 하나? |
| V6 | ☆ | DBC 에 없는 0x004·0x204 는 무엇인가? SYS 계열(0x100·0x110~0x114·0x301)은 어느 버스에 있나, 0x301 은 언제 쓰나? |
| V7 | ☆ | KF-1600 출력 설정 변경 가능한가? — 지금 로그는 **1 Hz, 위치·속도(GGA/VTG)뿐**이라 제어 모델에 부족. 10 Hz 이상 + 자세/각속도 문장 요청. 보레이트 |
| V8 | ☆ | 방향지시등·AEB·레이더 — A1 DBC 에 없는데 이 차량엔 없는 건가, 다른 버스에 있나? |

---

## 1. 연결 + listen-only + 비트레이트 (15분)

### 1-1. 종단저항 확인 (차 전원 끈 상태, 2분)
우리가 꽂을 지점에서 CAN_H–CAN_L 사이 저항을 멀티미터로 잰다.

| 측정값 | 의미 | 할 일 |
|---|---|---|
| 약 60Ω | 양 끝에 종단 있음(정상) | 그대로 꽂는다(우리는 종단 추가 안 함) |
| 약 120Ω | 한쪽 끝만 있음 | 우리 쪽에 120Ω 추가 여부를 업체와 상의 |
| 무한대 | 종단 없음 또는 측정 지점이 틀림 | 업체에 확인 |

### 1-2. 인터페이스 확인
```bash
ip -details link show can0
ip -details link show can1
```
차량 커넥터가 두 개(버스 두 개)면 can0·can1에 하나씩. PC의 "CAN1/CAN2"가 can0/can1 중 어느 쪽인지는
트래픽이 뜨는 쪽으로 확인한다.

### 1-3. listen-only로 비트레이트 찾기
**정상 모드로 먼저 올리지 않는다.**
```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
ip -details link show can0 | grep -o 'LISTEN-ONLY'      # 반드시 출력돼야 함. 없으면 즉시 down
candump can0                                             # 확인 후 Ctrl+C
```
순서: `500000`(V2 답 / 9월 스펙 PDF §5.B) → `250000` → `1000000` → `125000`. 한 값당 30초 이상 쓰지 않는다.

**정상 판단**: `0x200` 또는 `0x201`이 계속(초당 수십 개) 들어온다.
⚠️ `0x210`(명령)은 **누군가 보내고 있을 때만** 보인다 — 원격이 꺼져 있으면 없어도 정상.

**아무것도 안 뜰 때** — 다른 터미널에서 `ip -details -statistics link show can0`:
| 보이는 것 | 의미 |
|---|---|
| RX packets가 늘어남 | 정상(candump 창 다시 확인) |
| `berr-counter rx`가 오르거나 state가 ERROR-WARNING / ERROR-PASSIVE | **비트레이트가 틀림** → 다음 값 |
| 둘 다 그대로 | **배선·단자 문제**(H/L 뒤바뀜, 다른 단자, 차량 전원 꺼짐) → 비트레이트 말고 배선부터 |

맞는 것 같은데 에러가 섞이면: `sudo ip link set can0 type can bitrate 500000 sample-point 0.8 listen-only on`

확정되면 **끝까지 listen-only 그대로.**

**Kvaser:** `ip link` 대신 미러를 silent 모드로 띄운다(터미널 0, 계속 둔다). 1초마다 "프레임 n, 에러프레임 m" 이 찍힌다 —
프레임이 0 이고 에러만 늘면 비트레이트가 틀린 것 → Ctrl+C 후 다음 값으로.
```bash
python3 ~/git/A1-BSW/tools/kvaser/kvaser_mirror.py --channel 0 --bitrate 500000 --listen-only --log "$D/kvaser_ch0.log"
```
이후 이 매뉴얼의 `candump`·`cansniffer`·cantools 명령은 `can0` 을 **`kv0`** 로 바꿔 그대로 쓴다.

---

## 2. 녹화 시작 + 기준선 (5분)

시나리오마다 녹화를 켰다 껐다 하지 않는다. **한 번 켜서 계속 녹화**하고, 한 일은 **시각 메모**로 남긴다.

### 터미널 1 — 녹화 (건드리지 않음)
```bash
cd ~/a1_race_capture/vehicle_$(date +%Y%m%d)
candump -l can0            # can1 도 연결했으면: candump -l can0 can1
                           # Kvaser: 원본은 미러의 --log 가 기록 중. 2차로 candump -l kv0
```

### 터미널 2 — 메모 + 실시간 확인
"출발 전"의 세 줄(폴더·양식·`m` 함수)을 붙여 넣은 뒤:
```bash
m "녹화 시작, can0 500k listen-only"
```
이후 무언가 할 때마다 `m "내용"`. 시나리오는 **`m "S3 시작"` / `m "S3 끝"`** 형식을 지킨다(나중에 자동으로 자름).

### 실시간 확인 도구
```bash
# ID별 개수 (10초 세서 ÷10 = Hz, ÷ 대신 주기 = 10000/개수 ms)
timeout --foreground 10 candump can0 | awk '{print $2}' | sort | uniq -c

# 조작할 때 어떤 ID의 어떤 바이트가 바뀌나 (바뀐 바이트가 색으로 표시)
cansniffer -c can0          # 끝내려면 Ctrl+C
```

### S0 기준선 (★, 1분 + 기록 2분)
차량 키온, 아무 조작 없이 1분. `m "S0 시작"` … `m "S0 끝"`. 그 사이 위 ID별 개수 명령을 한 번 돌려
`answers.md` "연결 기본"을 채운다:
- 메시지별 주기 → 체크리스트 §9 "status 메시지 실제 주기(10 vs 20 ms)"
- ID 개수, OEM 차량 ID가 섞였는지 → §9.1 A3(DBW 버스가 OEM 버스와 분리됐나)
- 0x124~0x129(KIAPI)가 보이는지 → §9.1 C9

### 꼭 지킬 것
- **차나 PC 전원을 끄기 전에 터미널 1에서 Ctrl+C.** candump는 버퍼에 모았다가 쓰기 때문에 강제로 꺼지면
  마지막 부분이 사라진다(실험으로 확인).
- `timeout` 으로 candump 를 끊어 파이프로 집계할 때는 **반드시 `timeout --foreground`**. 빼면 시간이 다 됐을 때
  `sort`·`uniq` 까지 같이 죽어 **아무것도 안 나온다**(10-07 확인 — "트래픽이 없다"로 오판하기 쉽다).
- 내일은 `collect.sh`를 쓰지 않는다 — LTE 모뎀 포트를 GPS로 착각해 읽는 문제, sudo 실행 시 저장 위치가
  `/root`로 바뀌는 문제가 아직 안 고쳐졌다.

---

## A. 원격조종 — CAN 직결 판정 (15분)

### A-1. 판정 시험 (ROS·can_guard는 아무것도 안 띄운 상태)

| 시험 | 방법 | 기록 |
|---|---|---|
| T1 | 대회 측이 원격으로 차를 움직여 본다(정지 상태 조향만이라도) | `m "T1 원격 조향 O/X"` |
| T2 | T1 동안 터미널 2에서 `cansniffer -c can0` | 조이스틱 따라 바뀌는 ID는? |
| T3 | (R1 답이 "우리 PC에 꽂힘"일 때만) 우리 PC CAN 케이블을 뽑고 원격 조작 | 그래도 움직이나? |

### A-2. 판정표
| 원격으로 차가 움직임 | 조이스틱 따라 변하는 명령이 우리 버스에 보임 | 판정 | 다음 |
|---|---|---|---|
| O | O | **직결** | **A-직결** |
| O | X | 원격이 우리가 못 듣는 곳(다른 버스·게이트웨이 뒤)으로 명령 | can1·다른 커넥터로 옮겨 듣기(5분 제한), 업체에 배선도 요청 → 보이면 A-직결, 아니면 A-비직결 |
| X | — | **비직결** | **A-비직결** |

변하는 ID가 0x210이면 `m "원격 명령 ID = 0x210"` — 9/17 KF-1600 로그의 0x210 송신자가 이 원격 시스템이었을
가능성이 높다.

---

## A-직결 — 주행 로그 + 체크리스트 확인 (70분)

대회 측 조작자에게 아래 표를 보여 주며 순서대로 부탁한다. **정지 상태 시험을 먼저**(안전하고 빠름),
주행 시험은 공간이 있을 때만. 각 시나리오의 시작·끝에 `m`.

| 순서 | # | | 분 | 상태 | 조작 | 얻는 것 / 체크리스트 |
|---|---|---|---|---|---|---|
| 1 | S1 | ★ | 4 | 정지 | 조향 천천히 좌끝 → 중앙 → 우끝 → 중앙, **1회** | 조향 명령·조향각 스케일과 한계 |
| 2 | S2 | ★ | 4 | 정지 | 조향 스텝 0 → 우 절반 → 0 → 좌 절반 → 0, 각 **2초** 유지 | 명령 → 조향 응답 지연(제어 모델, §9.1 A1 중간 장치 지연) |
| 3 | S3 | ★ | 4 | 정지 | 브레이크 0 → 50 → 100 % → 0, 각 2초 | 브레이크 명령·위치 관계 |
| 4 | S6 | ★ | 8 | 정지 | ① 자동/수동/원격 모드 전환 ② 원격 조향 중 **사람이 핸들을 잡거나 브레이크 페달을 밟음** | is_auto 비트 의미(V4), override 조건(§9.1 B8), 개입 시 PC가 받는 것(§9.1 C12) |
| 5 | S7a | ★ | 8 | 정지, **대회 측 동의 시** | 원격 조향·브레이크를 약간 건 상태에서 **원격 송신기 끄기** → 10초 관찰 → 다시 켜기 | 차량 fail-safe 타임아웃(§7.1 "1000 ms"가 맞나), 그때 브레이크·조향 동작(§9.1 B6), 재개 시 자동 복귀인지 래치인지(§9.1 B5). 체크리스트 P-6(선 절단) 차량 쪽 동작과 같음 |
| 6 | S7b | ★ | 6 | 정지, **대회 측 동의 시** | 원격 **E-stop** 누르기 → 해제 | E-stop 방식·버스에 뜨는 메시지(§9.1 C10·C12), 버스가 조용해지면 "전원 차단형" |
| 7 | S4 | ☆ | 10 | 저속 직진 | 가속 약 / 강 2단계 → 놓기 → 제동 | 가속 명령 → 속도 응답, 가속 단위 |
| 8 | S5 | ☆ | 6 | 일정 저속 | 작은 진폭 좌우 조향 반복(천천히 → 조금 빠르게) | 횡방향 동특성 |
| 9 | S8 | ☆ | 10 | 주행 | 자유 주행 | 실사용 분포 |
| 10 | S9 | ☆ | 3 | 정지 | (방향지시등이 원격에 있으면) 좌 → 우 | Turn_Signal 매핑(§9) |

합계 ★ 34분 + ☆ 29분 = 63분. 남는 27분은 설명·차량 재배치·재시도 여유(실제로는 대부분 여기서 쓰인다).

**S7a·S7b 메모 요령**: 시각을 정확히 남기려고 애쓰지 않아도 된다 — 반응 시간은 집에서 로그로 잰다
(마지막 0x210 프레임 시각 → 0x200 브레이크 위치가 변하기 시작한 시각). 메모는 "대략 언제 했는지"만.

**사람이 직접 운전 가능하면**: S1·S8 중 하나를 수동으로 한 번 — 센서 메시지(0x200·0x201)만 따로 확인.

### 이 갈래에서 꼭 물어볼 것 (관측으로 안 되는 것)
- R3 원격 ↔ 자율주행 전환 방법 — 나중에 우리 can_guard가 0x210을 보낼 때 원격과 충돌하지 않으려면 필수
- V5 차량이 명령 범위·변화율을 검증하나(§9.1 B7) — 우리가 송신하기 전엔 관측 불가

---

## A-비직결 — 원격 인터페이스 기록 + 수동 역추적 (70분)

오늘은 **연결 방식과 데이터 형식만** 확보한다. 우리 PC가 대신 CAN으로 보내는 건 can_guard 실차 전환 후.

| | 분 | 할 일 |
|---|---|---|
| ★ | 20 | 원격이 PC에 붙는 방식 확인·기록(아래 표) + 대회 측 연동 문서·샘플 코드 요청 |
| ★ | 30 | 사람이 직접 조작하며 차량 메시지 역추적 — S1(핸들 손으로), S3(페달), S6②(키·모드 전환), 가능하면 S8(수동 주행) |
| ★ | 20 | 업체에 V3(0x210 조향 배율 ×0.1 vs ×1)·V4·V5 집중 질문 — 원격이 0x210 을 안 보내면 배율은 C단계(C3)에서만 확인 가능 |
| ☆ | 20 | 업체가 자기 장비로 차를 움직여 줄 수 있으면 그동안 녹화(0x210 관측 기회) |

| 원격이 PC에 붙는 방식 | 확인 | 기록 |
|---|---|---|
| USB | 꽂기 전/후 `lsusb` 비교, `ls /dev/input/js* /dev/serial/by-id/` | 장치 이름, 축·버튼 수 |
| LAN / WiFi | IP·포트·프로토콜을 대회 측에 질문 | `sudo tcpdump -i <인터페이스> -w "$D/remote.pcap"`(수동 캡처, 네트워크 설정 변경 없음) |
| ROS2 | `ros2 topic list -t`, `ros2 interface show <타입>` | `ros2 bag record -o "$D/remote_bag" <토픽>` |

받아 올 것: 메시지 형식(필드·단위·주기), 원격 우선 전환 방법(R3), 원격 쪽 안전 동작(R4·R5).
이후 계획: 원격 입력 → (새) 원격 브리지 → 공유메모리 → can_guard → CAN(0x210) 경로를 만든 뒤 S1~S8을 우리
명령으로 다시 수행.

---

## B. DBC 검증 (질문 담당이 A와 동시에, 약 20분)

10-06 에 업체 DBC 를 받았다(`DBC/A1_dbc.dbc`). 9/17 로그와 대조해 이미 아는 것:
- 0x200·0x201·0x210 비트 배치는 9/17 로그 22만 프레임과 **오류 0**으로 맞음
- **0x210 `steer_command` 배율 ×0.1 은 틀림 → 실제 ×1**(우리 코드·`A1_dbc_fixed.dbc` 는 ×1), 0x200 `steer_is_auto` 배율 오타
- DBC 에 **없는 것**: 0x004·0x204(9/17 로그엔 있음), 비트레이트, AEB·방향지시등
- DBC 에 **있지만 9/17 로그엔 없는 것**: SYS 계열 0x100·0x110~0x114·0x301 → 다른 버스로 추정. **0x301(엔코더 설정)은 절대 송신 금지**

그래서 현장에서는 "확보"가 아니라 **"오늘 버스에서도 맞는지 + 업체 확인"**만 하면 된다.
현장 디코드는 항상 **수정본** 으로: `F=~/git/A1-BSW/DBC/A1_dbc_fixed.dbc`

### B-1. 오늘 버스와 대조 (5분, S0 기준선 녹화 중에 같이)
```bash
F=~/git/A1-BSW/DBC/A1_dbc_fixed.dbc
# (1) DBC 에 없는 ID 목록 — 0x004·0x204 외에 새 ID 가 있으면 사진·메모 후 업체 질문
timeout --foreground 10 candump can0 | python3 -m cantools decode "$F" \
  | grep -o 'Unknown frame id [0-9]* (0x[0-9a-f]*)' | sort | uniq -c
# (2) 해석값이 말이 되는지 — 정지·핸들 중앙에서 조향≈0, 속도 0, 카운터 증가
candump can0 | python3 -m cantools decode --single-line "$F" | grep -E 'USER_control_info|USER_right_wheel' | head -20
# (3) SYS 계열이 이 단자에 보이나(0x100·0x110~0x114·0x301) — 보이면 이 버스가 차량 내부 버스일 수 있다
timeout --foreground 5 candump can0 | awk '{print $2}' | grep -E '^(100|11[0-4]|301)$' | sort | uniq -c
```
| 결과 | 의미 | 할 일 |
|---|---|---|
| Unknown 이 0x004·0x204 뿐, 값 정상 | 9/17 과 같은 버스·같은 프로토콜 | 그대로 진행 |
| 새 Unknown ID 가 있음 | 9/17 이후 바뀌었거나 다른 장치 추가(원격조종 등) | ID·주기 메모 → 업체·대회 측 질문(R2·V1) |
| 0x200/0x201 값이 이상(정지인데 속도≠0 등) | 버전이 다르거나 다른 차량 | **C단계 보류**, 업체에 최신 DBC 요청 |
| SYS 계열이 보임 | 우리 PC 가 차량 내부 버스에 붙어 있음 | 단자·하네스 확인(사용자용 버스로 옮겨야 할 수 있음), **C단계 보류** |

### B-2. 조향 배율 확인 (★ 가장 중요, A-직결이면 S1 에서 / C단계면 C3 에서)
명령(0x210 raw)과 위치(0x200)를 비교한다 — **위치 ≈ 명령 raw** 면 ×1(우리 가설), **위치 ≈ 명령 raw × 0.1** 이면 DBC 가 맞음.
```bash
candump can0 | python3 -m cantools decode --single-line "$F" | grep -E 'USER_control_command|USER_control_info'
```
원격이 0x210 을 보내는 A-직결이면 S1(조향 좌끝→우끝) 동안 바로 판정된다. 결과와 업체 답(V3)을 `answers.md` 에 기록.
**×1 이 아니면 C단계를 하지 않는다**(코드는 ×1 로 인코딩한다 — 집에서 수정 후 재시험).

### B-3. 업체에 물어볼 것 (V1·V3·V4·V6·V8, 질문 담당)
- 더 새 DBC·변경 이력·신호 설명 문서가 있으면 **파일로 받기**(USB 로 바로 복사 → `$D/dbc/`, 원본 이름 그대로)
- 받은 게 지금 것과 다르면: `diff ~/git/A1-BSW/DBC/A1_dbc.dbc "$D/dbc/<새 파일>"` 로 차이만 메모하고, 위 B-1 을
  새 파일로 다시 돌린다. 코드 반영은 집에 와서(현장에서 코드 수정 금지)

### 혹시 업체가 "그 DBC 말고 다른 프로토콜을 쓴다"고 하면
- 사진 촬영 허락 → 신호표(ID, 주기, 시작 비트, 길이, 바이트 순서, 부호, 배율, 오프셋, 단위, 범위)를 받아 온다
- **C단계는 하지 않는다**(우리 코드는 지금 DBC 기준). S1~S6 동안 `cansniffer -c can0` 로 바이트 변화만 기록

---

## C. 리프트 송신 시험 (25분) — can_guard 로 0x210 송신

우리 PC 가 처음으로 차량에 명령을 보내는 단계. 구조: `lift_cmd.py`(사람이 한 줄 명령 → 가짜 제어 노드 + 가짜 인지
하트비트) → 공유메모리 → **can_guard**(상태머신·한계·안전 동작) → 0x210 20 ms → 차량. lift_cmd 는 버스에
아무것도 보내지 않는다 — 차로 나가는 프레임은 전부 can_guard 가 만든다.

### 전제 조건 (하나라도 X 면 송신하지 않는다 — `answers.md` C절에 체크)
- [ ] 아침 확인 리허설 FAIL 0
- [ ] **B-1 대조 정상**(값 이상 없음, SYS 계열 안 보임) — 아니면 C단계 보류
- [ ] **업체 송신 허락**(누가, 몇 시)
- [ ] 구동륜이 공중(리프트), 차량 주변 사람 없음
- [ ] 업체 담당 입회, **E-stop 위치와 누를 사람** 정함
- [ ] 원격조종 송신기 꺼짐(다른 0x210 송신자 없음 — C0 에서 자동 확인)
- [ ] 비트레이트 확정, 녹화(`candump -l`) 중, `m` 준비

### C0. 듣기 상태에서 확인 (2분)
```bash
m "C0 시작"
timeout --foreground 3 candump can0 | awk '{print $2}' | sort | uniq -c     # 210 이 없어야 함. 200·201 은 있어야 함
```
`210` 이 보이면 누가 보내는지 확인하고 끌 때까지 진행하지 않는다.

### C1. listen-only 해제 + can_guard 시작 (3분)
```bash
m "C1 listen-only 해제"
sudo ip link set can0 down
sudo ip link set can0 type can bitrate <확정값> listen-only off
sudo ip link set can0 up
ip -details link show can0 | grep -o 'LISTEN-ONLY' || echo "정상 모드 OK"

# 터미널 3 — can_guard (리프트 한계, 1초마다 상태 표시). 이 터미널이 차로 나가는 유일한 송신자.
cd "$D" && python3 ~/git/A1-BSW/safety/can_guard/can_guard.py --channel can0 \
    --steer-limit-deg 30 --brake-limit-pct 60 --acc-limit-pct 10 --status-interval-s 1 2>&1 | tee -a can_guard.log
```
- 시작하면 1초 동안 듣고 **다른 0x210 이 있으면 스스로 거부**(종료 코드 3)한다.
- 그 뒤 `INIT` 상태로 **auto 0·명령 0** 을 20 ms 마다 보낸다 — 차량은 반응이 없어야 한다.
- 확인: `ip -details -statistics link show can0` 의 `berr-counter`·state 가 그대로(ERROR-ACTIVE)인지, 1분 뒤 다시.

**Kvaser:** listen-only 해제 = 미러를 silent 없이 다시 띄우는 것(silent 핸들이 남아 있으면 송신이 막힐 수 있다).
```bash
# 터미널 0: 미러 Ctrl+C →
python3 ~/git/A1-BSW/tools/kvaser/kvaser_mirror.py --channel 0 --bitrate <확정값> --log "$D/kvaser_ch0_c.log"
# 터미널 3: can_guard
cd "$D" && python3 ~/git/A1-BSW/safety/can_guard/can_guard.py --interface kvaser --channel 0 --bitrate <확정값> \
    --steer-limit-deg 30 --brake-limit-pct 60 --acc-limit-pct 10 --status-interval-s 1 2>&1 | tee -a can_guard.log
```
버스 상태는 미러의 1초 줄(에러프레임 수)로 본다.

### C2~C5. 명령 (12분)
```bash
# 터미널 4 — lift_cmd (가짜 제어 노드). 명령은 한 줄씩. 여러 줄 붙여넣어도 됨.
cd "$D" && python3 ~/git/A1-BSW/safety/can_guard/lift_cmd.py --channel can0
# Kvaser: ... lift_cmd.py --interface kvaser --channel 0 --bitrate <확정값>
```
| 단계 | 입력 | 확인 (can_guard 1초 상태 줄 또는 `status`) |
|---|---|---|
| C2 | `auto on` | 차량 0x200 auto 비트가 `111` 로, 핸들은 그대로 |
| C3 | `steer 5` → `steer 0` → `steer -5` → `steer 0`, 이어서 ±15, ±30 | 차량 **조향 위치 ≈ 명령**(같은 크기). 10배로 움직이면 즉시 `zero` |
| C4 | `brake 20` → `brake 50` → `brake 0` | 브레이크 위치가 따라감 |
| C5 | `acc 5` (2초 뒤 자동 0) → `acc 10` → `brake 20` | 바퀴 속도 상승·하강(0x201) |

- 입력 한계는 조향 ±30°, 브레이크 60 %, 가속 10 % — 넘는 값은 **거부**(자르지 않음). can_guard 도 같은 한계로
  한 번 더 자른다(이중).
- **자동 원위치**: 가속 2초, 조향·브레이크 5초가 지나면 0. 계속 유지하려면 다시 입력.
- 가속과 브레이크는 동시에 못 건다(브레이크 우선).
- 단계마다 `m "C3 시작"` / `m "C3 끝"`, 숫자는 `answers.md` C절 표에.

### C6. 안전 동작 (6분) — 체크리스트 §7.1·§9.1 B5·B6·P-6 를 실차에서 처음 확인
| | 하는 것 | 기대 동작 |
|---|---|---|
| (a) | `steer 10` 건 상태에서 **lift_cmd 창에서 Ctrl+C** (제어 노드 죽음) | 50 ms 뒤 can_guard: 조향 10° 고정, 가속 0, 브레이크 30 % 까지 서서히 → STOPPED(바퀴가 서 있으면 바로 STOPPED). **STOPPED 는 can_guard 재시작으로만 풀림** |
| (b) | can_guard 재시작(Ctrl+C 후 C1 명령 다시) → lift_cmd 재시작 → `auto on` → `perception off` | 0.5초 뒤 DEGRADED(또는 정지 상태면 바로 STOPPED), 같은 안전 동작 |
| (c) | can_guard 재시작 → `auto on`, `steer 10`, `brake 10` → **다른 터미널에서 `pkill -9 -f can_guard.py`** | 우리 0x210 이 끊긴다 → **차량 자체 타임아웃 동작 관측**(몇 초 뒤 무엇을 하나, auto 가 풀리나). 시간은 나중에 로그로 잰다 |
| (d) | (업체 동의 시) can_guard 송신 중 **E-stop** | can_guard 로그에 "송신 실패"가 뜨면 전원 차단형, 안 뜨면 차량 쪽 처리. 버스에 새 ID 가 뜨는지 |

### C7. 복귀 (2분)
```bash
# lift_cmd 에서: auto off → (1초 기다림) → q        ← auto 0 을 먼저 보내 수동으로 돌려놓고 끈다
# can_guard 창: Ctrl+C
sudo ip link set can0 down
sudo ip link set can0 type can bitrate <확정값> listen-only on
sudo ip link set can0 up
ip -details link show can0 | grep -o 'LISTEN-ONLY'      # 반드시 출력
m "C7 listen-only 복귀"
# Kvaser: 미러 Ctrl+C → --listen-only 붙여 다시 실행
```

### 문제가 생기면
| 증상 | 할 일 |
|---|---|
| can_guard 가 "다른 0x210 송신자" 로 시작 거부 | 원격조종 송신기 끄기. 그래도 보이면 업체에 문의, C단계 중단 |
| 송신 실패 / berr-counter 증가 / bus-off | 비트레이트·배선 재확인. can_guard 중단 후 listen-only 복귀 |
| `auto on` 해도 0x200 auto 비트가 안 바뀜 | 차량 쪽 다른 조건(키·스위치·모드)이 있는지 업체에 질문 — 우리 쪽 강제 시도 금지 |
| 조향이 명령의 10배 등 크기가 다르게 움직임 | 즉시 `zero` → `auto off`. 실제 배율을 `answers.md` 에 기록하고 중단 |
| can_guard 에 문제가 있을 때(비상용) | `tools/race_day/lift_tx.py --channel can0`(Kvaser: `--interface kvaser --channel 0 --bitrate <값>`) — 가드 없이 0x210 을 직접 보내는 단독 도구(같은 한계·자동 원위치·시작 전 점검, 상태머신 없음). **can_guard 와 동시에 띄우지 말 것** |
| Kvaser 가 `listChannels` 에 안 보임 | `bash tools/kvaser/install_kvaser.sh check` — 모듈 미로드면 `sudo modprobe mhydra`. 그래도 안 되면 PEAK 로 전환 |

## 체크리스트 대응표 — 내일 무엇으로 확인하나

`2026-09-14_can_verification_checklist.md` 항목 중 내일 실차로 확인 가능한 것. 결과는 `answers.md`에.

| 체크리스트 | 내용 | 내일 방법 | 조건 |
|---|---|---|---|
| §9.1 A1 | PC가 차량 제어기와 같은 버스인가, 중간 장치 있나 | 배선 관찰 + T1~T3 + S2 응답 지연 | 공통 |
| §9.1 A2 | 커넥터, 500 kbps, 종단 | 1단계 측정·스캔 | 공통 |
| §9.1 A3 | DBW 버스가 OEM 버스와 분리됐나 | S0 ID 목록 | 공통 |
| §9.1 A4 | can1 listen-only 부착 허용 | 질문 | ☆ |
| §9.1 B5 | 명령 끊김 조건·래치·복구 | **S7a** + V5 | 원격 직결 |
| §9.1 B6 | 끊김 시 감속·조향·정지 후 상태 | **S7a** | 원격 직결 |
| §9.1 B7 | 차량이 명령을 검증하나 | 질문 V5(관측 불가) | 공통 |
| §9.1 B8 | override 조건 | **S6②** | 원격 직결 |
| §9.1 C9 | KIAPI 노드(0x124~0x129) 역할 | S0에 보이는지 + 질문 | 공통 |
| §9.1 C10 | 원격 E-stop 방식·반응 시간 | **S7b** + R4 | 원격 직결 |
| §9.1 C11 / H7 | 원격 vs 자율주행 우선권, 모드 전환 메시지 | R3 + **S6①** | 공통(관측은 직결) |
| §9.1 C12 | 개입 시 PC가 받는 것 | **S6②·S7b**에서 새 ID·바이트 변화 | 원격 직결 |
| §9.1 D13·D14 | 노드 추가, 전원·재부팅 규정 | 질문 | ☆ |
| §9 | status 메시지 실제 주기(10 vs 20 ms) | S0 주기 측정 | 공통 |
| §9 | Turn_Signal 매핑, 레이더 장착 | S9 + V8 | ☆ |
| §7.1 | 벤더 fail-safe "1000 ms 후 AEB"가 실차에서도 맞나 | **S7a** 로그로 측정 | 원격 직결 |
| §7.3 P-6 | CAN 선 절단 시 차량 동작 | S7a로 차량 쪽 동작만 대체 확인(우리 can_guard 쪽은 나중에) | 원격 직결 |

내일 **확인 불가**(우리가 송신해야 함): P-3~P-5, P-10, P-11, 차량의 명령 검증 실측(B7) → 프로토콜 전환 후.

---

## GPS 기록 (KF-1600이 우리 PC에 연결될 때만, ☆)

⚠️ 이 PC의 `/dev/ttyUSB0~2`는 **LTE 모뎀**(Sierra EM7430)이다. 건드리지 말 것.
```bash
ls -l /dev/serial/by-id/          # 꽂기 전 → KF-1600 연결 후 다시 → 새로 생긴 장치가 GPS
sudo stty -F /dev/ttyUSBx <업체가 알려준 보레이트> raw -echo
sudo cat /dev/ttyUSBx | while IFS= read -r l; do echo "$(date +%s.%N) $l"; done > "$D/gps.log"
```

---

## 3. 마무리 (20분)

1. 터미널 1 Ctrl+C(녹화 종료), GPS 기록도 Ctrl+C
2. `m "종료"`
3. `answers.md` 빈칸 훑기 — 비어 있는 칸은 "못 물어봄"이라도 적는다(나중에 다시 물을 목록이 됨)
4. 폴더 확인
   ```
   ~/a1_race_capture/vehicle_<날짜>/
     candump-*.log      # CAN 원본
     notes.txt          # 시각 메모(S 시작·끝)
     answers.md         # 질문 답변·관측 기록
     dbc/               # 현장에서 새 DBC·문서를 받았으면
     gps.log            # 연결했으면
     remote.pcap 등      # A-비직결이면
   ```
5. 압축해서 USB 2개에 복사:
   `tar -czf ~/vehicle_$(date +%Y%m%d).tar.gz -C ~/a1_race_capture vehicle_$(date +%Y%m%d)`
6. 사진(하네스·커넥터·원격 장치·문서)은 휴대폰에서 같은 USB로

---

## 하지 말 것

- C단계 밖에서 listen-only를 끄는 것(정상 모드로 버스에 붙는 것)
- C단계 전제 조건을 다 채우기 전에 `can_guard.py` / `lift_tx.py` 실행
- `cansend` 등 손으로 하는 송신, **SYS 계열(0x100·0x110~0x114·0x301) 송신** — 특히 0x301 은 엔코더 설정을 바꾼다
- `can_guard.py` 와 `lift_tx.py` 동시 실행(둘 다 0x210 을 보낸다)
- `collect.sh` — 2단계의 알려진 문제 때문
- 녹화 중 PC 전원 강제 차단
- S7a·S7b를 대회 측 동의 없이, 또는 주행 중에 하는 것

---

## 집에 와서 (분석 계획 — 내일 할 일 아님)

1. `notes.txt`의 "S? 시작/끝"으로 로그를 시나리오별로 자른다.
2. 수정본 DBC(또는 현장에서 받은 새 버전)로 해석 → 전 프레임 검증(범위·연속성 위반 0), B-2 배율 판정 확정.
3. S7a에서 차량 fail-safe 시간 측정 → 체크리스트 §7.1·§7.4 잔여 위험 갱신.
4. 제어 모델용 데이터셋 — 시나리오별, 일정 시간 간격으로 맞춘 표:
   시각, 조향 명령, 조향 위치, 브레이크 명령, 브레이크 위치, 가속 명령, 좌·우 바퀴 속도, GPS 위치·속도·방향, 모드
5. 원격 직결이면 R3 답을 바탕으로 can_guard와 원격의 전환 방식 설계. 비직결이면 원격 브리지 설계.
6. 체크리스트 §9.1 표에 답 반영.
