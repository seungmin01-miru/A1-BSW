# sil/vcan — vcan0 SIL 환경 (Phase B)

`can_stack_development.md` §5.B / 액션 보드 Phase B의 실행 도구. 실물 CAN 없이 `vcan0` 위에서
DBC 기반 프레임을 스펙 주기로 주입하고, 디코드·주기·프레임 손실을 확인한다.

## 준비 (1회)

```bash
sudo apt install -y can-utils                       # candump / cansend
python3 -m pip install --user cantools python-can   # 설치 확인: python3 -c "import can, cantools"
```

**DBC 파일**: 팀 DBC는 저장소 `DBC/EAIT_CAN(AVANTE_CN7).dbc` (2026-09-12 반입, 보드 Phase 0-1 완료).
스크립트는 `DBC/` → `can_protocol/00. CAN 프로토콜/` 순으로 찾고, 다른 곳이면 `--dbc` 로 지정한다.

**주기**: 이 DBC 에는 `GenMsgCycleTime` 속성이 **없다**. 송신기는 DBC 속성 → 스크립트 내장 스펙표
(0x156/0x157/0x711/0x712/0x713 = 10 ms, 0x710 = 20 ms, 출처 EAIT PDF) → `--period` 순으로 주기를 정하고 출처를 표시한다.

## 순서

| 보드 | 명령 | 확인 |
|---|---|---|
| B-1 | `sudo bash vcan_up.sh` | `vcan0` up. 부팅 시 자동 생성: `sudo bash vcan_up.sh --install` (systemd `vcan0.service`) |
| B-2 | `bash roundtrip_check.sh` | `cansend`→`candump` 왕복 자동 판정 (✅/❌) |
| B-4 | 터미널 1: `python3 eait_tx.py --range 0 60` <br> 터미널 2: `python3 eait_rx.py --msg EAIT_INFO_SPD` | 0x712 `EAIT_INFO_SPD` 10 ms, 0~60 kph 사인파 → 물리값 디코드, 100 Hz·주기 편차·카운터 건너뜀 표시 |
| B-5 | `python3 eait_tx.py --msg EAIT_INFO_EPS` 등 | 메시지 이름만 바꿔 0x710/0x711/0x713 확장 (enum 신호는 0, 카운터는 자동 증가) |
| B-6 | `python3 eait_tx.py --msg EAIT_Control_01 --pattern const --value 0 --set EPS_En=1 --set ACC_En=1 --set EPS_Speed=150` + `candump vcan0` | TX 방향(0x156/0x157) 인코딩 확인 — `--set 신호=값` 으로 신호별 고정 (Phase C 인코더 완성 전 임시) |

**2026-09-12 실측 (실제 DBC, vcan0)**: B-1 ✅ (`vcan0.service` enabled) · B-2 ✅ · B-4 ✅ 1001프레임/10초 = 100.0 Hz,
카운터 건너뜀 0, 4개 휠속 물리값 디코드 일치 · 0x156 인코딩 바이트 검증 ✅ (`01 96 01 00 00 00 00 <cnt>`).

**실행 위치별 주기 정밀도 (0x712, 10 ms, 12초)** — 실제 운용 조건은 격리 코어 + RT 우선순위이므로 거기서 검증한다:

| 조건 | 송신 주기 편차 평균 / 최대 | 수신 주기 최대 |
|---|---|---|
| 비격리 코어(0–7), 일반 우선순위 | 61 / 88 µs | 10.19 ms |
| 격리 코어(tx 8, rx 10), 일반 우선순위 | 57 / 63 µs | 10.04 ms |
| 격리 코어 + 타이머 여유 1 µs (`--cpu 8`, 권한 불필요) | **17 / 27 µs** | 10.04 ms |
| 격리 코어 + SCHED_FIFO 80 (`sudo chrt -f 80 sudo -u ailab …`) | **5 / 9 µs** | (송신만 측정) |

일반 우선순위 태스크는 커널이 기본 50 µs 타이머 여유(timer slack)를 두어 `sleep` 이 늦게 깬다 — 편차 ~57 µs 의 정체.
`eait_tx.py` 는 시작 시 여유를 1 µs 로 줄이고(`prctl`, 권한 불필요), `--cpu` 로 격리 코어에 배치하고, `--rt PRIO` 로 SCHED_FIFO + `mlockall` 을 건다 (A-3/A-4 최소 구현).

```bash
python3 eait_tx.py --cpu 8  --rt 80 --range 0 60          # 격리 코어 8, FIFO 80
python3 eait_rx.py --cpu 10 --msg EAIT_INFO_SPD
```
**RT 우선순위로 측정하기** — 이 PC는 다른 작업도 같은 계정으로 돌기 때문에 계정에 rtprio 를 영구 부여하지 않는다(2026-09-12 결정).
측정할 때만 sudo 로 우선순위를 걸고, 프로그램 자체는 `ailab` 권한으로 되돌려 실행한다(우선순위는 상속됨, 설정 변경 없음):
```bash
sudo chrt -f 80 sudo -u ailab python3 eait_tx.py --cpu 8 --range 0 60 --duration 12 --quiet
sudo chrt -f 70 sudo -u ailab python3 eait_rx.py --cpu 10 --msg EAIT_INFO_SPD --duration 10
```
운용 단계(Phase C 이후)에서는 대회 스택 전용 계정(예: `bsw`)을 만들어 그 계정에만 `rtprio`/`memlock` 을 주고 systemd 서비스로
노드를 띄우는 방식을 권장한다 — 개발 계정의 다른 작업과 분리된다.

- 주기는 DBC `GenMsgCycleTime`을 따른다(없으면 `--period`, 기본 10 ms).
- `eait_tx.py`는 종료 시 실제 송신 주기 편차(평균/최대 µs)를 출력한다 — §3 통합시험 "topic hz/지연" 대조용.
  참고: 일반 우선순위·비격리 코어에서도 virtual 버스 기준 편차 최대 ~70 µs. RT 격리 코어에 올릴 때는
  `taskset -c 8-15 chrt -f 80 python3 eait_tx.py …` (A-3 규칙과 동일).
- `eait_rx.py`는 `Alive_Cnt` 류 신호의 건너뜀을 프레임 손실로 집계한다(E2E 체크의 전신, Phase C/D에서 노드로 이관).
- 시험용 인터페이스: `--interface virtual --channel t0` (같은 프로세스 안에서만 공유되는 python-can 가상 버스, DBC 로직 확인용).

## 한계 (§5.B 그대로)
vcan은 로컬 루프백이라 버스 전송지연·중재·에러프레임이 없다. 실시간성 최종 검증은 실물 CAN(`can0`, PEAK PCAN-PCIe FD)에서 재측정.
