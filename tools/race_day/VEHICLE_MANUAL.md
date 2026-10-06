# 실차 연결 매뉴얼 — 대회장에서

대회장에서 처음 실차를 받았을 때, CAN 단자를 연결한 직후부터 뭘 해야 하는지 순서대로. 급할 때 위에서
아래로 그대로 따라가면 된다. 명령어는 전부 그대로 복붙 가능.

> 관련 문서: `README.md`(collect.sh 상세), `tools/deploy/`(우리 PC가 고장났을 때 비상용 재설치/부팅 USB)

---

## 출발 전 체크(한 번만)

- [ ] `sudo bash tools/race_day/collect.sh`를 vcan0로 리허설해 본 적 있는지(`README.md` 맨 아래 참고)
- [ ] `tools/deploy/` 번들 저장장치 + 우분투 22.04.5 부팅 USB 챙겼는지(우리 PC가 고장나서 남의 PC를 빌려야
      할 때 대비 — `README.md`의 "다른 PC에서 급하게 돌려야 할 때" 참고)
- [ ] 이 PC를 차량에 실제로 어떻게 고정·전원 연결할지(데스크톱용 ATX 파워서플라이라 차량 12V/24V를 바로
      못 받는다 — 인버터/전원 방안이 현장에서 막히면 안 됨)

---

## 0. 아직 모르는 값 — 추측하지 말 것

- **비트레이트**: EAIT 스펙(PDF) 기준 **500kbps**로 문서화돼 있다(`can_stack_development.md` §5.B). 다만
  ① 체크리스트 §9.1-A2에 "500kbps 맞는지" 주최 측 확인 질문으로 아직 남아 있고, ② 실차 로그(KF-1600)의
  프로토콜이 EAIT DBC와 다르게 나왔으므로 그대로 믿지 말고 아래 2단계에서 **500k부터** listen-only로 확인한다.
- **CAN vs CAN-FD**: 지금까지 본 로그가 전부 8바이트(=classic CAN 길이)라 아마 classic일 가능성이 높지만
  확정 아님.
- **종단저항(120Ω)**: 우리 어댑터가 버스 끝이 아니라 중간에 탭하는 거라면, 우리 쪽 종단저항은 **꺼져
  있어야 한다**(버스 양 끝에만 120Ω 2개 — 우리가 세 번째로 추가되면 안 됨). PEAK PCIe FD 카드가 소프트
  웨어/점퍼로 종단을 켜고 끌 수 있는지 먼저 확인. 모르면 기본(꺼짐) 상태로 시작.

---

## 1. 연결 직후 — 인터페이스 확인

```bash
ip -details link show can0
ip -details link show can1
```
둘 다 `state DOWN`, `peak_canfd` 드라이버로 보이면 정상(카드 자체는 PCIe 내장이라 케이블 유무와 무관하게
항상 보인다). "CAN1"/"CAN2"라고 적힌 물리 단자가 각각 `can0`/`can1` 중 뭔지는 라벨만으로 확신 못 한다 —
2단계에서 트래픽이 뜨는 쪽으로 바로 확인된다.

---

## 2. 비트레이트 안전하게 찾기 — 반드시 listen-only로

**절대 먼저 정상 모드로 올리지 않는다.** 비트레이트를 틀리면 카드가 에러 프레임을 버스에 흘릴 수 있는데,
`listen-only on`을 켜면 그 가능성이 차단된다(수신만 하고 ACK/에러프레임을 전혀 안 보냄 — 실차 버스에 안전).

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
candump can0
```

- 정상 메시지(예: `0x200`/`0x201`/`0x210` 같은 ID에 멀쩡한 8바이트)가 보이면 **비트레이트 확정**.
- 아무것도 안 뜨거나 깨진 값이면 `down` → 다음 비트레이트로 `type can bitrate ... listen-only on` → `up`
  → `candump can0` 반복. 순서: `500000` → `250000` → `1000000` → `125000`.
- can0에서 안 되면 **can1**에도 같은 절차 반복(물리 단자 매핑이 반대일 수 있음).

---

## 3. 확정되면 — listen-only 유지 권장

트래픽이 보이면 이미 모든 메시지를 다 볼 수 있다(CAN은 브로드캐스트라 듣는 데는 `listen-only`든 아니든
차이 없음). 아직 우리가 송신할 준비가 안 됐으니(6단계 참고), **당분간 `listen-only on`을 계속 켜 둔 채로
진행**하는 걸 권장 — 안전하고 데이터 수집엔 지장 없음.

양쪽 다 연결돼 있다면 같은 방식으로 다른 인터페이스도 올려 둔다.

---

## 4. 전체 캡처 — `collect.sh`

인터페이스가 이미 떠 있는 상태에서:

```bash
cd ~/git/A1-BSW
sudo bash tools/race_day/collect.sh
```

"candump 시작: can0" / "can1"이 뜨는지 확인(떠 있는 인터페이스를 자동으로 잡음 — 따로 지정할 필요 없음).
테스트 주행/동작 진행 → **끝나면 Ctrl+C** → tar.gz 경로가 화면에 출력된다. 그 파일을 USB로 복사.

`can_guard.py`를 같이 띄운다면 아래처럼 로그를 리다이렉트해 두면 자동으로 같이 수거된다(`README.md` 참고):
```bash
python3 safety/can_guard/can_guard.py --channel can0 2>~/a1_race_capture_can_guard.stderr.log
```

---

## 5. 컴퓨터로 가져와서 분석

```bash
tar -xzf a1_race_capture_<타임스탬프>.tar.gz
cat */MANIFEST.md          # 뭐가 잡혔는지 요약부터
```
`can/<인터페이스>/candump-*.log`와 `can/<인터페이스>.csv`를 raw_hex ↔ 알려진 프로토콜(0x200/0x201/0x210,
`can_stack_development.md` §5.D 참고) 대조로 분석. **새로운 arbitration ID**가 보이면(원격조종/KIAPI류
등 미확인 메시지일 수 있음) 따로 표시해 둘 것.

---

## 6. ⚠️ 지금 하면 안 되는 것

**`can_guard.py`를 이 실차 버스에 "송신 모드"로 연결하지 않는다 (아직).** can_guard는 아직 옛 DBC 기준
(0x156/0x157)으로 송신하도록 돼 있고, 실차 프로토콜(0x210)로는 아직 안 바꿨다 — 지금 연결하면 차량이
이해 못 하는 메시지를 버스에 쏘게 된다. 수신/캡처(1~5단계)는 전혀 문제없음 — **송신만 금지**.
코드 전환 작업이 끝나기 전까지는 `candump`/`collect.sh`로 듣기만 할 것.

---

## 문제가 생기면

- **우리 PC가 고장남** → `README.md`의 "다른 PC에서 급하게 돌려야 할 때" 참고. `tools/race_day/`
  디렉터리만 USB로 복사해 가면 됨(절대경로/계정 의존 없음, 확인됨). 빌리는 PC가 Linux가 아니면 이 방법
  자체가 안 통하니 OS를 미리 알아두거나 `tools/deploy/`의 부팅 USB를 챙긴다.
- **원격조종이 되는지 안 되는지 불확실** → 연결만 하면 어쨌든 모든 메시지는 다 잡힌다(브로드캐스트
  특성). 원격조종이 실제로 우리 PC/소프트웨어를 거치는지는 아직 미확인 항목(`2026-09-14_can_verification_checklist.md`
  §7.4, §9) — 현장에서 주최 측이 시연할 때 `collect.sh`를 돌려 두면 새 메시지가 뜨는지로 바로 확인 가능.

---

## 요약 (한눈에)

```
① 인터페이스 확인 → ② listen-only로 비트레이트 스캔(500k→250k→1M→125k) →
③ 확정되면 listen-only 유지 → ④ collect.sh 로 전체 캡처 →
⑤ 가져와서 raw_hex 대조 분석 → ⑥ can_guard 송신은 아직 금지
```
