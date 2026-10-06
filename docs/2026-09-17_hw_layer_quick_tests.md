# 2026-09-17 하드웨어 층 3분 대조 실험 계획 (저녁 8h 전)

네트워크·패키지 활동이 격리 코어 전역 스톨을 만든다는 것은 확정(양성 대조 130 에피소드). IPI·IRQ·SMI·커널 이벤트는 모두 아님 → 남은 층은 하드웨어·펌웨어·언코어.
양성 대조에서 에피소드가 **분당 ~4개**였으므로 **3분 soak 로도 10개 안팎**이 나와야 정상 — 각 대조군은 3분이면 판정 가능하다(0개 vs ≥5개).

공통: 운용 설정(rt-poll + eco 800 + hk ondemand) 유지. `soak` 는 열 로그에 **하우스키핑 실클럭·패키지 W·클럭 제한 사유(MSR 0x64F/0x1B1)** 를 10초마다 남기고, `episodes.py` 가 에피소드 시각의 그 행을 붙여 준다.

## 실행 형식 (터미널 2개)
```bash
# T1: soak (3분)                         # T2: 유발 (soak 시작 직후)
sudo bash tools/rt/a1_rt.sh soak 3        sudo bash tools/rt/provoke_net.sh 3 45
# 끝난 뒤
python3 tools/rt/episodes.py tools/rt/logs/soak_<ts>.log --provoke tools/rt/logs/provoke_<ts>.log
```
판정 기준: 에피소드 **0~1개 = 조용**, **≥5개 = 재현**. 2~4개면 한 번 더.

## 순서 (정보량 ÷ 비용 순)

| # | 대조 | 준비 | 유발 | 조용하면 → | 재현되면 → |
|---|---|---|---|---|---|
| **T0** | 3분 재현 확인 + 새 열 로그 검증 | 없음 | `provoke_net 3 45` | 3분 프로토콜 무효 → 10분으로 | 프로토콜 유효. 에피소드 행의 `limit_log` 에 PL2/전류/VR 사유가 찍히는지 확인 |
| **T1** | WiFi 절전(LPS) 끔 | `sudo nmcli con mod HY-WiFi 802-11-wireless.powersave 2 && sudo nmcli con up HY-WiFi` | `provoke_net 3 45` | **원인 = 어댑터 절전 전이** → 운용: 라디오 끔 대신 powersave 2 로도 충분(기전 논문 포인트) | 절전 전이 아님 |
| **T2** | 부하 필요조건 | 없음 | `A1_NOLOAD=1 sudo … soak 3` + `provoke_net 3 45` | **부하가 필요조건** → 전력/전류 과도(PL2·ICCmax) 쪽 | 부하 무관 → 장치·링크 쪽 |
| **T3** | 네트워크 없이 무거운 사용자 작업 (WiFi 라디오 off) | `sudo nmcli radio wifi off` | `provoke_local 3 45` | 사용자 작업 자체는 무해 → 어댑터가 필요 | **원인 = 무거운 작업의 전력·디스크 과도** (어댑터 무관) |
| **T4** | 같은 작업, WiFi 켜짐·트래픽 없음 | `sudo nmcli radio wifi on` | `provoke_local 3 45` | | T3 와 비교해 어댑터 존재 효과 |
| **T5** | USB autosuspend·PCIe ASPM 끔 | `for d in /sys/bus/usb/devices/*/power/control; do echo on \| sudo tee $d; done; echo performance \| sudo tee /sys/module/pcie_aspm/parameters/policy` | `provoke_net 3 45` | **원인 = 링크 전원 상태 전이** | 링크 PM 아님 |
| **T6** | 전력 과도만 (네트워크·디스크 없음) | `sudo nmcli radio wifi off` | 별도 터미널: `for i in $(seq 40); do stress-ng --matrix 12 --timeout 2 -q; sleep 2; done` | 전력 계단은 무해 | **원인 = PL2/전류 제한 반응** (0x64F 로그로 확인) |
| T7 | 어댑터 물리 제거 | USB 뽑기 | `provoke_local 3 45` | | T3 와 비교 |

T1 은 재부팅·모듈 재적재 없이 되는 가장 싼 결정적 실험이라 **T0 다음에 바로**. 드라이버 수준의 깊은 절전(`rtw88_core disable_lps_deep=Y`)은 모듈 재적재가 필요해 T1 이 애매할 때만.

원복: T1 `nmcli con mod HY-WiFi 802-11-wireless.powersave 0`, T3/T6 `nmcli radio wifi on`, T5 `echo auto > …/power/control`, `echo default > …/pcie_aspm/parameters/policy` (전부 재부팅 시 자동 원복).

## 저녁 8h (대회 조건)
```bash
sudo bash tools/rt/soak_guard.sh on race     # check 표: 라디오 disabled, 유닛 전부 inactive
sudo bash tools/rt/a1_rt.sh soak 480
# 아침: sudo bash tools/rt/soak_guard.sh off
```
T1 이 조용했다면 8h 도 `powersave 2` 상태를 함께 기록(운용 대안 검증).

## 결과 기록
`measurements/2026-09-17_hw_quick/README.md` 에 T별 한 줄(에피소드 수, limit_log 사유, 판정) — 논문 체크리스트 §3 갱신.
