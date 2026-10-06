# PLAN B — 대회 PC 가 Ubuntu 22.04 + ROS2 Humble

예상 소요: 판정별로 30 분 ~ 1 h + soak 30 분. **포맷하지 않는다** — 기존 상태에 필요한 것만 추가.

## B-0. 조사
```bash
cd /media/$USER/<장치>/a1_bundle/scripts && bash survey.sh      # 읽기 전용
```
`VERDICT:` 줄에 나온 값(복수 가능)에 따라 아래를 실행. **모든 경우 `00_local_repo.sh` 가 먼저.**

## 판정별 실행 목록
| VERDICT | 의미 | 실행 |
|---|---|---|
| **B-ok** | 22.04 + 6.8 generic + NVIDIA 470.256.02 정상 | `00` → `40 stage1`→재부팅→`stage2`→재부팅→`stage3` → `50` → `90` |
| **B-kernel** | 6.8 generic 없음(예: 5.15 GA) — WiFi 동글(`rtw88_8822bu`)은 6.2+ 필요 | `00` → **`10`**→재부팅(6.8 generic 확인) → 아래 이어서 |
| **B-nvidia** | NVIDIA 가 470.256.02-server 가 아님/없음 | `00` → (`10`) → **`20`**→재부팅(`nvidia-smi` 확인) → 이어서. ⚠️ 기존 다른 버전 NVIDIA 는 purge 됨 — 확인 프롬프트에서 y |
| **B-secureboot** | Secure Boot 켜짐 | BIOS 에서 끄기(우리 PC 와 동일 조건) 후 재조사. `40_rt.sh` 는 켜져 있으면 중단함 |
| **B-noros** | `/opt/ros/humble` 없음 | **`30`** 실행 |

이어서 실행 순서(공통): `30`(ROS2·wheel — 빠진 것만 설치) → `40 stage1` → 재부팅 → `40 stage2` → 재부팅 → `40 stage3` → `50`(sudo 없이) → `90`.
각 단계 통과 기준은 PLAN_A.md §A-2 표와 동일.

## B 고유 주의
- **이미 ROS2 Humble 이 있으면** `30` 은 없는 패키지만 추가(apt 가 번들 버전과 다르면 갱신될 수 있음 — `a1-apt` 가 출력하는 목록 확인).
- **`50_workspace.sh`** 는 이미 저장소가 있으면 덮어쓰지 않고 번들 HEAD 와 비교만 함.
- **NVIDIA 를 교체하지 않고 다른 버전(535 등)으로 RT 를 시도하는 것은 비권장** — 검증은 470.256.02 조합뿐.
- 이미 `nvidia-driver-470-server` 인데 `nvidia-smi` 가 실패하면 `dkms status` 로 generic 모듈 빌드 여부 확인 후 `20_nvidia.sh` 가 재설치를 시도함.
- 기존 부팅 설정(GRUB 사용자 정의)이 있으면 `40 stage1` 의 `pin` 이 `/etc/default/grub.d/99z-a1-bsw-safe-default.cfg` 를 만들고 원본을 `/opt/a1-bsw/tools/rt/backup_*` 에 백업함.

## 롤백
부팅 메뉴(10초)에서 generic 선택 → `sudo bash 40_rt.sh undo`. 6.8-rt 패키지까지 제거: `sudo bash /opt/a1-bsw/tools/rt/a1_rt.sh rollback`.
