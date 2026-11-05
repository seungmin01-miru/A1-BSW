# 2026-11-05 하위 제어(BSW) 인터페이스 보고서

모델 팀에 하위 제어를 설명하기 위한 기술 보고서(15쪽). 본문 한국어, 표·그림은 예시 `reports/2026-11-03_paper_data/A1_BSW_Figures_Tables.pdf` 형식.

| 파일 | 내용 |
|---|---|
| `report.pdf` | 인쇄본 |
| `report.html` | 원본(A4 인쇄 CSS, 그림 `figs/`) — 고친 뒤 `google-chrome --headless --disable-gpu --no-pdf-header-footer --print-to-pdf=report.pdf report.html` |
| `make_figs.py` | Fig. 3~7 생성: `python3 make_figs.py <저장소 루트> .` (입력 `measurements/2026-11-04_drive_log/tables/*.csv`, `track.yaml`) |
| `figs/` | Fig. 1~7 + 데이터시트 sheet0~5 사본 |

기준 커밋 1f4d36b + 11-05 미커밋 분석. 11-05 분석(출발 민감도·완성도)은 개발 PC 결과라 "참고값"으로 표시했다.

공유 페이지(Artifact, 비공개 — Share 메뉴에서 팀원 공유): https://claude.ai/artifact/MdTdZ6oM4dggTLAvZDKowo
