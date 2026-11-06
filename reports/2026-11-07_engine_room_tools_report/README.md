# 2026-11-07 BSW 엔진룸 도구 보고서

[11-06 보고서](../2026-11-06_engine_room_report/)의 후속이다. WP2(속도별 표)·WP3(측정·갱신 도구)·WP7(경기 날 운영)과 모델 입력 20 Hz 반영을 다룬다. **실차 검증 전이다** — 그림 대부분은 가짜 차량 값이고 판정은 대회 PC·실차로 한다. 표 형식은 이전 보고서와 같다.

| 장 | 내용 |
|---|---|
| 1–2 | WP 진행, 전체 회귀, 20 Hz, 구성 |
| 3 | `long_map` 규칙, 지시서와 다르게 한 것 |
| 4 | `fit_long_map` 차단 검사·시운전, 11-04 미리보기, 원 돌기 계획·저속 유지 주의 |
| 5–7 | 시작 점검·경기 날 절차, 팀 요청, 대회 PC 할 일 |
| 부록 A | **PlotJuggler 데이터시트** Fig. A1–A9, Sheet A |

| 파일 | 내용 |
|---|---|
| `report.pdf` / `report.html` | 인쇄본 / 원본 — `google-chrome --headless --no-pdf-header-footer --print-to-pdf=report.pdf report.html` |
| `make_data.py` | SIL 결과·11-04 로그 → `data/pj/*.csv` |
| `pj_capture.py` | PlotJuggler + Xvfb 캡처(`XTOOLS_ROOT` 는 11-06 보고서 README) → `figs/pj_*.png`, `pj_layouts/*.xml` |
| `data/sil/` | 판정 요약, `data/bsw5_preview/` 11-04 미리보기 |

공유 페이지는 만들지 않는다(CLAUDE.md 7절).
