# tools/kvaser — Kvaser Leaf v3 로 실차에 붙기

근거: `tools/race_day/Practice_04_CAN.pdf`(Kvaser 드라이버·CANlib 실습), `04_CAN.pdf`(CAN 일반).
이 PC 의 기본 CAN 장치는 내장 PEAK PCAN-PCIe FD(`can0`/`can1`, SocketCAN)다. Kvaser 는 **USB 로 따로 꽂아 쓰는
두 번째 선택지**다(차량 쪽 커넥터·케이블이 Kvaser 기준일 때 등).

## 왜 그냥 꽂아서는 안 되나 — 핵심 사실 (2026-10-07 확인)

| 사실 | 결과 |
|---|---|
| Kvaser Leaf v3 의 USB 제품 번호는 **0x0117**(linuxcan 소스 `mhydra/mhydraHWIf.c`) | |
| 이 PC 커널(6.8.1-1059-realtime)의 SocketCAN 드라이버 `kvaser_usb` 지원 목록에 **0x0117 이 없다**(`modinfo kvaser_usb`) | 꽂아도 `canX` 인터페이스가 안 생긴다 |
| Kvaser 공식 드라이버 linuxcan 5.52 는 이 RT 커널에서 **빌드된다**(`mhydra.ko` vermagic = 6.8.1-1059-realtime, Secure Boot 꺼짐) | 설치하면 동작 |
| linuxcan 을 쓰면 장치는 SocketCAN 이 아니라 **CANlib 채널(0, 1, …)**로 보인다. 설치 시 SocketCAN `kvaser_usb`/`kvaser_pciefd`/`kvaser_pci` 를 blacklist 한다 | PEAK(`peak_pciefd`)는 영향 없음. 우리 도구는 python-can 의 `kvaser` 백엔드로 쓴다 |

## 설치 (1회, sudo, 약 3분)

```bash
sudo bash tools/kvaser/install_kvaser.sh          # 내려받기 → 빌드 → 설치 → 모듈 로드 → pip canlib → 확인
bash tools/kvaser/install_kvaser.sh check          # 상태만 확인(아무것도 안 바꿈)
sudo bash tools/kvaser/install_kvaser.sh uninstall # 되돌리기(blacklist 도 제거)
```

실습 자료와 **다르게 하는 것**(이 PC 에서 그대로 따라 하면 위험):
- `sudo apt remove dkms` ✗ — 이 PC 의 NVIDIA 470(`nvidia-dkms-470-server`)이 dkms 에 의존한다. 지우면 GPU 드라이버가 같이 제거된다.
- `sudo apt-get install linux-headers-generic` ✗ — 필요한 건 지금 커널 헤더(이미 있음). generic 메타패키지는 다른 커널 헤더를 끌어온다.
- 모듈은 **지금 커널용으로만** 설치된다. generic 커널로 부팅하면 Kvaser 가 안 보인다 → 그 커널에서 스크립트 재실행.

설치 확인(실습 자료와 같음): `/usr/doc/canlib/examples/listChannels`, `python3 -c "from canlib import canlib; print(canlib.prodversion())"`.
가상 채널(`kvvirtualcan`, 채널 0·1 이 서로 연결된 가상 버스)이 같이 로드되므로 장치 없이도 리허설할 수 있다.

## 실차에서 쓰는 법 — PEAK 명령과 1:1 대응

Kvaser 는 비트레이트·listen-only 를 `ip link` 가 아니라 **프로그램이 채널을 열 때** 정한다.

| 하는 일 | PEAK(`can0`) | Kvaser(CANlib 채널 0) |
|---|---|---|
| 장치 확인 | `ip -details link show can0` | `/usr/doc/canlib/examples/listChannels` |
| **듣기 전용 + 비트레이트** | `sudo ip link set can0 type can bitrate 500000 listen-only on` | 미러를 `--listen-only --bitrate 500000` 로 실행(아래) |
| 매뉴얼의 candump·cansniffer·cantools | `can0` | **`kv0`**(미러가 만든 복사본) — 명령에서 `can0` → `kv0` 만 바꾼다 |
| 원본 녹화 | `candump -l can0` | 미러의 `--log`(원본 시각) + 원하면 `candump -l kv0`(2차) |
| can_guard | `--channel can0` | `--interface kvaser --channel 0 --bitrate 500000` |
| lift_cmd | `--channel can0` | `--interface kvaser --channel 0 --bitrate 500000` |
| 비상용 lift_tx | `--channel can0` | `--interface kvaser --channel 0 --bitrate 500000` |

### 미러 (`kvaser_mirror.py`)
```bash
sudo ip link add dev kv0 type vcan && sudo ip link set kv0 up        # 부팅마다 한 번
python3 tools/kvaser/kvaser_mirror.py --channel 0 --bitrate 500000 --listen-only --log "$D/kvaser_ch0.log"
```
- `--listen-only` = Kvaser **silent 모드**(ACK·에러 프레임을 버스에 전혀 안 냄). 비트레이트 스캔·녹화·원격 판정은 반드시 이 모드로.
- 1초마다 "프레임 n, 에러프레임 m" 을 찍는다 — **에러만 늘고 프레임이 0 이면 비트레이트가 틀렸다**(다음 값으로 미러 재시작).
- **C단계(can_guard 송신) 전에는 미러를 `--listen-only` 없이 다시 띄운다** — silent 핸들이 열려 있으면 같은 채널의 송신이
  막힐 수 있다. normal 모드 미러는 ACK 만 하고 아무것도 보내지 않는다.
- 미러는 같은 채널 다른 핸들(can_guard)의 송신도 받아 `kv0` 에 복사한다(로컬 TX 에코) → C단계에서도 `kv0` 로 감시 가능.

### 비트레이트 스캔 (Kvaser)
미러를 `--bitrate 500000 --listen-only` 로 띄우고 1초 표시를 본다 → 프레임이 안 나오면 Ctrl+C 후
`250000` → `1000000` → `125000` 순으로 다시. 정상이면 `candump kv0` 에 0x200·0x201 이 20 ms 로 보인다.

## 알려진 문제와 처리 (2026-10-07 설치·리허설에서 발견)

| 문제 | 처리 |
|---|---|
| linuxcan 의 `make install` 이 `missing 'System.map' … Skipping depmod` 로 **모듈 목록 갱신을 건너뜀** → 파일은 있는데 `modprobe` 가 못 찾음 | 설치 스크립트가 `depmod` 를 직접 실행하고, 모듈이 안 잡히면 🛑 로 멈춘다 |
| **python-can 4.6.1 이 Kvaser 채널을 못 엶** — `canIOCTL_SET_LOCAL_TXACK` 를 1바이트로 넘겨 CANlib 5.52 가 "Error in parameter [-1]" | `safety/can_guard/kvaser_compat.py` 가 그 한 호출만 4바이트로 바꿔 넘긴다. 우리 도구는 Kvaser 를 열 때 자동 적용. python-can 을 직접 쓰는 새 코드도 `kvaser_compat.apply()` 를 먼저 부를 것 |
| 가상 채널에서 "canSetAcceptanceFilter … Not implemented [-32]" 경고 | 가상 드라이버가 하드웨어 필터를 지원하지 않아 나는 경고 — 동작에 영향 없음(실제 Leaf v3 는 지원) |

## 리허설 (설치 후, 장치 없이 — CANlib 가상 채널 0·1)
```bash
bash tools/kvaser/rehearse_kvaser.sh       # 가짜 실차(채널 1) ↔ can_guard(채널 0) + 미러 → kv0 확인
```
2026-10-07 결과: **9/9 PASS** — 미러(silent) kv0 복사·원본 기록, 다른 0x210 송신자 시 can_guard 시작 거부(코드 3),
can_guard(kvaser) INIT→ACTIVE, lift_cmd 가 can_guard 송신값 확인(로컬 TX 에코), 0x210 주기 19.9 ms, 조향 10° 명령 →
가짜 실차 위치 10.0°(배율 1), can_guard kill -9 뒤 송신 0.
