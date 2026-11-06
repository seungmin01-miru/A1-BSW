# 2026-11-06 BSW 엔진룸·경기 신호 전달 보고서

회의 결정(BSW = 변환·끊김 정지·전달·오돔·한계 측정, 반응은 모델) 뒤의 전체 정리(13쪽). 형식은 [11-05 보고서](../2026-11-05_bsw_update_report/)와 같다. **실차 검증 전이다** — 표 숫자는 잠정, 첫 확인은 11-14.

| 장 | 내용 |
|---|---|
| 1–2 | 역할 분담, 검증 수준, 구성 |
| 3 | 폐기 설계 정리(WP-1), 복원 기준 커밋 정정(913e2bf → d702f63) |
| 4 | 조향 7점 표(측정 대비 오차), 가속 히스테리시스(시험의 한계) |
| 5 | `/race/state` 규칙, 자동 판정, 지연, 부하 |
| 6–7 | 팀 통지, 미결(E-6 U턴 등), 남은 일(sudo 필요한 시험) |
| 부록 A | **PlotJuggler 데이터시트** Fig. A1–A12, Sheet A·B |
| 부록 B·C | 대회 CAN 신호, 다시 만들기 |

| 파일 | 내용 |
|---|---|
| `report.pdf` / `report.html` | 인쇄본 / 원본 — `google-chrome --headless --no-pdf-header-footer --print-to-pdf=report.pdf report.html` |
| `make_data.py` | CAN 원본 → `data/pj/*.csv`: `python3 make_data.py ../.. .` |
| `pj_capture.py` | PlotJuggler + Xvfb 캡처 → `figs/pj_*.png`, `pj_layouts/*.xml`: `python3 pj_capture.py .` |
| `pj_layouts/` | PlotJuggler 에서 직접 여는 레이아웃. CSV 를 Data 로 연 뒤 Layout 으로 불러온다(CSV 창에서 X 축 = `t`) |
| `data/sil/` | `r_race.py`·`adapter_cruise_check.py` 결과(개발 PC 참고값) |

Xvfb·xdotool 은 sudo 없이 풀어 쓸 수 있다: `apt-get download xvfb xdotool libxdo3 libxfont2 libfontenc1 x11-xkb-utils xkb-data` → 각 파일 `dpkg -x <deb> <폴더>` → `XTOOLS_ROOT=<폴더> python3 pj_capture.py .`
공유 페이지는 만들지 않는다(CLAUDE.md 7절).
