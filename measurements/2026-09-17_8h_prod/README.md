# 2026-09-16/17 운용 설정 8시간 무인 재검증 — rt-poll + eco 800 + hk ondemand

## 조건
- 부팅 `a1-bsw-rt-poll`(RT 6.8.1 + 격리 8-15 + `idle=poll intel_pstate=disable max_cstate=0`), 런타임 `iso_pm.sh eco 800`(격리 800 MHz 고정) + `iso_pm.sh hk ondemand`(하우스키핑).
- 22:22:01 ~ 06:22:01, **무인**(세션 확인 0회). 부하 = 모든 soak 와 동일(glmark2 4K refract GPU 평균 81 % + stress-ng). `soak_guard on`(화면 잠금·apt 타이머 정지). 직전에 `a1_rt.sh prune` 완료(실험 GRUB 항목·5.15-rt 제거).
- 확인: cpuidle 없음, acpi-cpufreq, 격리 MSR 790~991 MHz, SMI 0(295→295), 스로틀 0, 패키지 43~63 °C, GPU 최대 83 °C, 커널 경고 0, glmark2 중단 0(2,868회 확인).

## 결과 — `soak_20260916_222155.*` (2.3억 표본)

| | 9/14 8h (기본 RT 튜닝) | **9/17 8h (운용 설정)** | D4 예산 |
|---|---|---|---|
| 최악 | 828 µs | **920 µs** | ≤ 1,000 ✓ (여유 8 %) |
| 평균 | 4.x | 7.0 | — |
| 50 µs 초과 | 2,893 | **81** | — |
| 100 µs 초과 | 1,465 | **51 (0.000022 %)** | ≤ 0.01 % ✓ (450배 여유) |
| 200 µs 초과 | 1,424 | **31** | — |
| 400 µs 초과 | 231 (전부 CPU 12/13) | **26** | — |
| 400 µs 초과 에피소드 | 65 | **3** | — |

스레드별 200 µs 초과 3~5회, 최악 465~920 — 8코어 균등(코어 6 증폭 현상 없음).

## 400 µs 초과 26건 = 에피소드 3개 (cyclictest overflow 사이클 번호로 복원)
| 에피소드 | 시각 | 코어 | 저널에서 같은 순간 |
|---|---|---|---|
| 1 | 00:07:14 | 8코어 전부 동시 | 00:07:03 `rtw_8822bu: firmware failed to leave lps state` |
| 2 | 06:12:18 | 8코어 전부(10·12 는 2회) | 06:12:13~14 NetworkManager 연결 상태 변화(CONNECTED_SITE→GLOBAL), **PackageKit 저장소 갱신 시작**(43 s), apt-news / esm-cache 서비스 실행; 06:12:45 rtw LPS 실패 |
| 3 | 06:12:18 + 106 ms | 8코어 전부 | 위와 동일 창 |

- rtw(USB WiFi) LPS 실패 메시지는 이 부팅에서 **238회(약 3분마다)** 찍혔고 그 대부분은 스파이크와 무관 → LPS 메시지 자체가 트리거는 아님.
- 에피소드 2·3은 **네트워크 재연결 + PackageKit 갱신**과 5초 안에 겹침. `soak_guard` 는 apt 타이머만 막았고 NetworkManager dispatcher 가 띄우는 PackageKit/apt-news/esm-cache 는 막지 못했다.
- 9/14 8h 부팅 저널에는 rtw 메시지 0건 — 그때의 65 에피소드는 전원관리 원인(이후 제거됨)이었고, 이번 3건은 **다른, 드문 시스템 전역 사건**이다. 8코어 동시·IRQ 없이 0.5~0.9 ms 멈춤이라는 형태는 같다.

## 판정
- **D4 충족**: 최악 920 µs ≤ 1 ms, 100 µs 초과 0.000022 % ≪ 0.01 %. 30분 대비 8시간에서 드문 전역 사건 3개가 드러났고, 최악값이 1 ms 에 8 % 여유로 붙어 있는 점은 기록한다.
- 9/14 대비 200 µs 초과 **46배 감소**(1,424 → 31), 400 µs 초과 에피소드 65 → 3.
- 남은 3건의 후보: (a) USB WiFi 어댑터(rtw88 8822bu)의 전원 상태 전이·재연결, (b) 네트워크 연결 시 자동 실행되는 PackageKit/apt 계열 서비스, (c) 미상 전역 사건. **대회 운용 환경은 인터넷·WiFi 없음**이 전제이므로 (a)(b)는 운용 규칙(`rfkill block wifi` 또는 어댑터 제거, PackageKit·snapd·apt 타이머 비활성)으로 제거되고, 그 상태로 8시간 한 번 더 돌리면 (c) 만 남는지 판정된다.

## 다음
1. `soak_guard.sh on` 에 `packagekit` `snapd` `fwupd-refresh.timer` `motd-news.timer` `ua-timer.timer` 정지 + `rfkill block wifi` 옵션 추가 → **운용 조건 8시간 재실행**(WiFi 차단·백그라운드 서비스 정지). 이것이 대회 당일 상태와 같다.
2. A-3 systemd 유닛: 부팅 시 `eco 800` + `hk ondemand` + 위 서비스 정지 + rfkill 을 자동 적용(승인 후).
3. 2-1 인지 처리량(유휴 14 W 기준 여유 21 W).
