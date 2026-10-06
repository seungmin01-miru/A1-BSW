# tools/race_day — 대회장 데이터 수집 패키지

대회장에서 실차를 인계받았을 때, 정확히 뭐가 돌고 있을지 모르는 상태에서도 **켜기만 하면** 원시 CAN·
ROS2 토픽·can_guard 흔적·GPS/INS 시리얼·시스템 상태를 전부 모아 하나의 압축 파일로 남기는 도구.
**수집만** 한다 — can_guard/ROS2 브리지/CAN 인터페이스를 이 스크립트가 띄우지 않는다(팀이 따로 기동).

> 실차 CAN 단자를 처음 연결하는 순간부터의 전체 절차(비트레이트 찾기 포함)는 **`VEHICLE_MANUAL.md`** 참고.
> 이 문서는 `collect.sh` 자체의 사용법·설계만 다룬다.

## 현장에서 할 일 (이게 전부)

```bash
sudo bash tools/race_day/collect.sh
```

화면에 "수집 중입니다..."가 뜨면 그대로 두고 주행/시험을 진행한다. **끝나면 Ctrl+C** — 자동으로 정리해서
tar.gz 하나로 묶고 경로를 화면에 출력한다. 그 파일을 USB로 복사해서 가져오면 된다.

`sudo` 없이 실행해도 대부분 동작한다(CAN·ROS2·시리얼 캡처는 root 불필요) — 커널 로그(journalctl)만 권한이
없으면 건너뛴다는 메모를 남기고 계속 진행한다. 현장에서 sudo 비밀번호를 모르면 그냥 `bash collect.sh`로
실행해도 된다.

### can_guard 로그도 같이 남기려면 (선택, 1줄)

`can_guard.py`를 띄울 때 아래처럼 stderr를 고정 경로로 리다이렉트해 두면, 수집이 끝날 때 자동으로 같이
모아준다(순서 상관없음 — collect.sh를 먼저 켜든 나중에 켜든 무방):

```bash
python3 can_guard.py --channel can0 --cpu 8 --rt-priority 90 2>~/a1_race_capture_can_guard.stderr.log
```

이 리다이렉트를 안 해도 collect.sh는 안 죽는다 — 그냥 "can_guard 프로세스가 떠 있었다/없었다"만 시작·
종료 시점에 각각 기록한다(과거 출력은 소급해서 못 잡음, 이게 유일한 한계).

## 이 스크립트가 절대 하지 않는 것

- **WiFi/네트워크/라디오를 건드리지 않는다.** 주최 측이 준비한다는 원격 조종(텔레메트리/원격 E-stop 등)이
  네트워크에 의존할 수 있어서, 이 스크립트는 의도적으로 `nmcli`/`rfkill` 등을 전혀 호출하지 않는다.
  `tools/rt/soak_guard.sh`의 "대회 조건"(`on race`, WiFi 끔)과는 **다른, 별개의 스크립트**다 — 혼동해서
  같이 쓰지 말 것.
- **CAN 버스에 아무것도 보내지 않는다.** `candump`와 `can_csv_logger.py`(python-can) 둘 다 수신 전용
  (`bus.recv()`만 호출, `bus.send()` 없음). 주최 측이나 다른 팀이 버스에 새 장치를 추가해도(원격 E-stop
  주입 등) 이름 모를 arbitration ID로 그냥 같이 기록될 뿐 충돌 없음.
- **CAN 인터페이스를 새로 올리지 않는다.** `can0`/`can1`(또는 `vcan0`)이 이미 떠 있어야 그걸 잡아서
  기록한다 — 인터페이스를 올리는 건(`ip link set can0 type can bitrate ...`) 팀이 미리 해 둬야 하는 일.

## 나오는 결과물

```
~/a1_race_capture_<타임스탬프>.tar.gz
  <타임스탬프>/
    MANIFEST.md          # 뭐가 잡혔는지 요약(파일별 줄 수/용량)
    system/start.txt, end.txt, journal_kernel.log
    can/<인터페이스>/candump-*.log   # candump -l 원본 포맷 (인터페이스별 하위 디렉터리)
    can/<인터페이스>.csv             # python-can 보조본 (wall_time,arbitration_id_hex,raw_hex)
    ros2/detect.log, capture_<타임스탬프>/   # ros2 bag (토픽이 있었을 때만)
    can_guard/detect_start.txt, detect_end.txt, can_guard.stderr.log(있으면)
    serial/<장치명>.log          # GPS/INS 등 시리얼 원문(줄마다 수신 시각)
```

**분석은 이 저장소가 있는 컴퓨터로 가져와서 진행** — 이 패키지는 수집까지만 담당한다. `can/*.csv`는
`TalkFile_kf-1600_CAN_GPSINS_CSV.zip.zip`의 `can.csv`와 같은 컬럼(`wall_time,arbitration_id_hex,raw_hex`)
이라 그때 쓴 방식(raw_hex ↔ 실차 프로토콜 대조)을 그대로 이어서 쓸 수 있다.

## 다른 PC(남의 노트북 등)에서 급하게 돌려야 할 때

우리 PC가 고장나서 다른 학교 팀 PC를 빌려 진단해야 하는 상황 대비 — USB 등 외부 저장장치로 `tools/race_day/`
디렉터리만 통째로 복사해 가도 된다(`collect.sh`/`can_csv_logger.py`에 이 계정·이 저장소를 가리키는 절대
경로가 없음, 확인됨). 다만 아래는 실제로 막힐 수 있는 지점이라 알아 둘 것 — **소프트웨어로 못 고치는
것들이라 미리 확인이 필요**:

- **Linux 전용이다.** CAN 캡처는 SocketCAN(리눅스 커널 기능)에 의존한다 — 빌리는 PC가 Windows/macOS면
  이 패키지 자체가 안 돌아간다. 다른 팀 PC가 어떤 OS인지 미리 알아두거나, 최악의 경우 대비용 우분투
  부팅 USB를 하나 같이 챙기는 걸 권장(`tools/deploy/`의 부팅 USB가 원래 이 용도로 만들어 둔 것 — "우리
  PC에 새로 설치"가 아니라 "남의 PC를 급하게 리눅스로 띄우는" 이 시나리오에 그대로 쓸 수 있다).
- **`candump`(권장)가 없으면 `python-can`이라도 있어야 한다.** 둘 다 없으면 원시 CAN을 전혀 못 잡는다
  (다른 모듈은 정상 진행됨). `python-can`이 없을 때는 트레이스백 대신 한 줄 메시지로 조용히 건너뛰도록
  고쳐 놨다(`can_csv_logger.py`) — `MANIFEST.md`에 ⚠️ 표시로도 뜬다.
- **파일을 Windows에서 한 번이라도 열었다 저장하면 깨진다.** 메모장 등으로 `collect.sh`를 열었다 저장하면
  줄바꿈이 CRLF로 바뀌어 스크립트가 아예 안 돈다(실제 재현 확인 — `set: - : invalid option` 등 즉시 오류).
  의심되면 실행 전에 한 번 `sed -i 's/\r$//' collect.sh can_csv_logger.py`(리눅스에서) 돌려서 원복.
  **단순 복사(USB 드래그, `cp`, `rsync`)만으로는 이 문제가 생기지 않는다** — 파일을 열어서 편집·저장했을
  때만 문제.
- **실행 파일 비트(+x)가 없어도 상관없다.** exFAT/FAT32 USB는 원래 실행 권한 비트를 저장 못 하는데,
  이 패키지는 어차피 `bash collect.sh`/`python3 can_csv_logger.py`처럼 인터프리터에 파일을 넘겨서 돌리는
  방식이라(`./collect.sh` 아님) 영향 없음(실제로 실행비트 제거하고 재현 테스트함).
- **권한·그룹 문제**는 그 PC 계정이 CAN/시리얼 장치를 읽을 권한이 있는지에 달려 있다 — 없으면 그 모듈만
  조용히 건너뛰고 나머지는 계속된다(기존 best-effort 설계 그대로).

## 미리 한 번 돌려보기 (실제 대회장 가기 전)

```bash
# vcan0 로 리허설 — 아무 CAN 트래픽 없이도 안 죽는지, 끝나고 tar.gz 가 제대로 나오는지 확인
sudo ip link show vcan0 || sudo bash ~/git/A1-BSW/sil/vcan/vcan_up.sh
python3 ~/git/A1-BSW/sil/vcan/eait_tx.py --channel vcan0 &   # 트래픽 흘려주기(선택)
sudo bash tools/race_day/collect.sh
# 10초 정도 기다렸다가 Ctrl+C, 결과 tar.gz 를 풀어서 can/vcan0.log 에 프레임이 찍혔는지 확인
```
