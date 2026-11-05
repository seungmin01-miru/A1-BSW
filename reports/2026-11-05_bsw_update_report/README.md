# 2026-11-05 BSW 개선 보고 — 앱 비상정지 · 자동 arm/disarm · 주행 날 통합 실행 · Kvaser 채널 공유

[11-05 하위 제어 보고서](../2026-11-05_lowlevel_control_report/)의 후속 보고서다(원 보고서는 고치지 않음, 12쪽). 표·그림 형식은 원 보고서와 같다.

| 장 | 내용 |
|---|---|
| 2 | 앱 비상정지 = 원격 인수. 앱 프레임 해석, 11-04 사건 시간표, 규칙 R1–R4, 해제 순간 조향 튐 수정, 00 기준 경로 A/B/C, 앱 운용 절차·업체 질문 |
| 3 | **자동 arm 조건(6개)·한 번만 규칙, 고장 래치 → HOLDING → STOPPED, disarm 의 실제 동작과 수정(ESTOP·STOPPED 가 disarm 에 풀리던 결함)**, 상황별 표 |
| 3.5 | **예시로 보는 정지** — 정상·보호·비상 정지와 "정지가 아닌 것"(주행 중 disarm 등) 12가지, 정지 시간·거리 추정, 시간 순 예시 |
| 4 | `tools/bsw` — `bsw_up`/`bsw_down`/`bsw_status`/`bsw_monitor`(상태판) |
| 5 | **Kvaser 채널 공유 — 송신을 열면 수신이 막히나**: 핸들 구성, 11-03 실패 원인, 11-04 송신 중 수신율 100.0 %, RT 와의 관계, 규칙 |
| 6–7 | 도구 정비, 확인 상태·남은 일 |

| 파일 | 내용 |
|---|---|
| `report.pdf` / `report.html` | 인쇄본 / 원본(A4 CSS) — `google-chrome --headless --no-pdf-header-footer --print-to-pdf=report.pdf report.html` |
| `scenarios.py` | 3장 시나리오: 실제 can_guard 코드를 python-can `virtual` 버스로 — `python3 scenarios.py ../.. .`(python-can 필요) |
| `make_figs.py` | Fig. 1–5 — `python3 make_figs.py ../.. .` |
| `data/` | `scenarios.csv`·`events_*.csv`(3장), `kvaser_rx.tsv`(5장, `tools/drive_log/kvaser_rx_check.py`) |

개발 PC 결과는 참고값이다(CLAUDE.md). 판정은 대회 PC SIL·실차로 한다 — 순서는 `bsw_test/docs/NEXT_competition_pc.md` 1절.
