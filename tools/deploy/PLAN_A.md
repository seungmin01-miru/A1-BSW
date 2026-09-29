# PLAN A — 대회 PC 가 Ubuntu 20.04 + ROS1 Noetic (포맷)

예상 소요: 설치 1.5~2 h + 스크립트 1 h + soak 30 분.
**시작 전 확인**: ① 포맷해도 되는지, 백업할 데이터가 없는지 ② 부팅 USB(22.04.5)와 번들 저장장치 ③ 전원 어댑터·키보드·마우스 ④ 이 PC 의 원래 상태 기록(다음 절).

## A-0. 포맷 전 기록 (5분)
Noetic 상태에서 `bash survey.sh` (번들 저장장치 `scripts/` 에서) → 결과가 `a1_bundle/results/survey_*.txt` 로 저장됨. 실패해도 진행 가능(기록용). BIOS 진입해서 **Secure Boot = Disabled** 확인, C-state/터보/전력 제한(PL1) 설정을 사진으로 남김(우리 PC 와 비교용).

## A-1. Ubuntu 22.04.5 설치
1. 부팅 USB 로 부팅 → *Install Ubuntu*. **네트워크는 연결하지 않음**(업데이트 확인으로 멈추는 것 방지).
2. 「Normal installation」, **"Download updates while installing"·"Install third-party software" 체크 해제**, 디스크 전체 삭제 후 설치.
3. 사용자 계정 생성(이후 `sudo` 가능해야 함). 설치 후 재부팅, USB 제거.
4. 22.04.5 ISO 는 6.8 HWE 커널을 기본 탑재 — `uname -r` 확인(6.8.0-xx-generic 이면 정상).

## A-2. 번들 스크립트 (순서대로, 각 단계 통과 기준 확인)
번들 저장장치 연결 후 `cd /media/$USER/<장치>/a1_bundle/scripts` (또는 `A1_BUNDLE=/경로` 지정).
| 순서 | 명령 | 통과 기준 |
|---|---|---|
| 1 | `sudo bash 00_local_repo.sh` | "번들 체크섬 통과", "번들 저장소 동작 확인(RT 커널 해석됨)" |
| 2 | `sudo bash 10_base.sh` → `sudo reboot` | 재부팅 후 `uname -r` = `6.8.0-138-generic`(또는 번들의 최신 6.8 generic) |
| 3 | `sudo bash 20_nvidia.sh` → `sudo reboot` | `nvidia-smi` 에 RTX A5000 / 470.256.02 |
| 4 | `sudo bash 30_ros2.sh` | "ROS2 Humble: /opt/ros/humble", pip 3종 ✅ |
| 5 | `sudo bash 40_rt.sh stage1` → `sudo reboot` | 재부팅 후 `uname -r` = `6.8.1-1059-realtime`, `cat /sys/kernel/realtime` = 1 |
| 6 | `sudo bash 40_rt.sh stage2` → `sudo reboot` | stage2 가 GPU 확인 후 tune/finalize 수행. 재부팅 후 `cat /proc/cmdline` 에 `isolcpus=…8-15` |
| 7 | `sudo bash 40_rt.sh stage3` | verify 에 격리 코어 8-15, "운용 런타임 튜닝 적용됨", GPU·WiFi·CAN ✅ |
| 8 | `bash 50_workspace.sh` (**sudo 없이**) | colcon test 통과, can_guard 유닛테스트 통과 |
| 9 | `sudo bash 90_validate.sh` | soak 30 최악값 기록, SIL P-1/P-2 PASS, 회수 파일 목록 출력 |

> 5→6 사이에 RT 부팅에서 GPU 가 안 뜨면 stage2 는 finalize 하지 않고 중단한다(기본 부팅이 바뀌지 않으므로 재부팅하면 generic 으로 안전 복귀). `dkms status`, `tail -40 /var/lib/dkms/nvidia-srv/470.256.02/build/make.log` 확인 후 `sudo env IGNORE_PREEMPT_RT_PRESENCE=1 dkms install nvidia-srv/470.256.02 -k 6.8.1-1059-realtime`.

## A-3. 마무리
- `90_validate.sh` 끝의 파일 목록 확인 → `sync` 됐으니 저장장치 분리.
- 문제가 생겨 현장에서 코드를 고쳤다면 커밋해 둘 것(`changes.bundle` 로 회수됨).
- **BIOS 설정이 우리 PC 와 다르면**(soak 100 µs 초과) 사진을 대조하고 C-state/터보/전력 제한을 맞춰 `soak` 재실행.

## 막혔을 때
| 증상 | 조치 |
|---|---|
| 00 에서 "디스크 여유 부족" | 설치 시 파티션 확인 (`/var/local` 에 ≥ 12 GB) |
| 10 에서 `apt` 가 다른 소스를 찾음 | 반드시 `a1-apt` 경유 — 00 을 다시 실행 |
| WiFi 없음 | 6.8 generic/RT 에서 `rtw88_8822bu` 로드 확인 (`lsmod | grep rtw88`), 동글 재연결 |
| Secure Boot 경고 | BIOS 에서 끄고 재부팅 후 20 재실행 |
| 아무 것도 안 될 때 | 부팅 메뉴에서 generic 선택 → `sudo bash 40_rt.sh undo` |
