# 대회 PC 배포 번들 — 개요

대회 PC 에 우리가 검증한 RT 환경(`6.8.1-1059-realtime` + NVIDIA 470 + `a1-bsw-rt-poll` 부팅 + 운용 유닛)을 **인터넷 없이** 옮기는 도구.

## 가져가는 것 / 남는 것 / 가져오는 것
| | 내용 |
|---|---|
| **가져감** | 번들 저장장치 `a1_bundle/` 1개 (≥ 32 GB, ext4 또는 exFAT). 상황 A 대비로 **Ubuntu 22.04.5 부팅 USB 1개**(우리 PC 에서 미리 제작) — Ventoy USB 하나에 ISO + 번들을 같이 넣어도 됨 |
| **대회 PC 에 남음** | RT 커널, NVIDIA 470, ROS2, 복원된 저장소(can_guard 등) — 대회 때 실제로 쓸 환경 |
| **가져옴** | `a1_bundle/results/<호스트>_<시각>/` — survey(설치 전 원래 상태), verify/bench/soak, can_guard 테스트, 단계별 로그, 사후 스냅샷, 현장 코드 변경(`changes.bundle`, `git_diff.patch`) |

돌아온 뒤: `results/…` 를 저장소 `measurements/<날짜>_competition_pc/` 로 옮기고 커밋.

## 상황 판정 흐름
```
대회 PC 에서:  bash survey.sh      (읽기 전용, 아무것도 안 바꿈)
   VERDICT: A                → PLAN_A.md  (20.04+ROS1 → 포맷 → 22.04.5)
   VERDICT: B-ok             → PLAN_B.md §B-ok
   VERDICT: B-kernel/nvidia/secureboot/noros (복수 가능) → PLAN_B.md 해당 절
```

## 번들 구조
```
a1_bundle/
  README.md PLAN_A.md PLAN_B.md
  iso/ubuntu-22.04.5-desktop-amd64.iso (+ .sha256)     # 상황 A 용
  repo/pool/*.deb, repo/Packages.gz                    # 로컬 apt 저장소 (amd64 + i386, 의존성 전체)
  wheels/*.whl                                         # cantools 44.0.0, python-can 4.6.1, numpy 1.26.4 …
  src/a1-bsw.bundle, src/HEAD.txt                      # 저장소 스냅샷(git bundle)
  scripts/*.sh, scripts/rt/                            # 이 디렉터리 + tools/rt 사본
  reference/                                           # 우리 PC 기준값
  results/                                             # 대회 PC 가 채움 (빈 폴더로 출발)
  SHA256SUMS
```

## 스크립트 (번호순)
| 파일 | 실행 | 역할 |
|---|---|---|
| `build_bundle.sh` | **우리 PC**, sudo, 온라인 | 번들 생성 (esm 인증이 root 전용이라 sudo 필요) |
| `verify_bundle.sh` | 우리 PC | 번들 완결성 시뮬레이션(누락 의존성 0 확인) |
| `survey.sh` | 대회 PC | 현재 상태 조사 + A/B 판정 (읽기 전용) |
| `00_local_repo.sh` | sudo | 번들 → `/var/local/a1-repo`, `a1-apt`(오프라인 apt) 설치 |
| `10_base.sh` | sudo | 6.8 generic + dkms/gcc-12/측정 도구 |
| `20_nvidia.sh` | sudo | NVIDIA 470.256.02-server 로 맞춤 |
| `30_ros2.sh` | sudo | ROS2 Humble + pip wheel |
| `40_rt.sh stage1/2/3` | sudo | pin·install·arm → verify·tune·finalize → 최종 verify (`a1_rt.sh` 오프라인 래퍼) |
| `50_workspace.sh` | **일반 사용자** | 저장소 복원 + colcon build/test + can_guard 테스트 |
| `90_validate.sh` | sudo | verify + bench + soak 30 + can_guard SIL, **결과 회수** |

## 우리 PC(출발 전) 체크
```bash
sudo bash tools/deploy/build_bundle.sh          # → ~/a1_bundle (약 10 GB+, ISO 포함)
bash tools/deploy/verify_bundle.sh ~/a1_bundle  # 의존성 누락 0 이어야 함
cd ~/a1_bundle && sha256sum -c SHA256SUMS
# USB 로 복사: rsync -a ~/a1_bundle/ /media/$USER/<USB>/a1_bundle/   (부팅 USB 는 별도: Ventoy 또는 balenaEtcher 로 ISO)
```

## 핵심 주의
- **커널은 1059 로 고정** (esm 저장소의 메타패키지는 이제 1060 만 남음 — 미검증이라 번들에 넣지 않음). 번들엔 `linux-image-6.8.1-1059-realtime` 등 버전 명시 패키지가 들어간다.
- NVIDIA 는 470.256.02-server 하나만 검증됨. 다른 버전이면 교체(20_nvidia.sh). RT 빌드는 `IGNORE_PREEMPT_RT_PRESENCE=1` 우회(공식 미지원) — GPU 프로세스 비정상 종료 시 `scheduling while atomic` 관측 이력, 운용 중 정상 종료만.
- BIOS 설정(C-state·터보·전력 제한)은 OS 에서 못 읽는다. 결과(soak)가 우리 PC(30분 최악 27 µs)보다 크게 나쁘면 BIOS 를 우리 PC 와 대조.
- **롤백**: 부팅 메뉴(10초)에서 generic 선택. 완전 원복은 `sudo bash 40_rt.sh undo` / `sudo bash /opt/a1-bsw/tools/rt/a1_rt.sh rollback`.
