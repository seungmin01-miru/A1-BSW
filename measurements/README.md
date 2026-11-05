# measurements — 날짜별 실측 데이터·분석 결과

폴더 이름 = `YYYY-MM-DD_주제`. 한 폴더 = 그날의 원본(또는 원본 위치)·분석 결과·데이터시트. **분석 코드는 `tools/` 에** 두고 여기서는 결과만 둔다.
`raw/` 안 원본은 읽기 전용(CLAUDE.md 1.1). 큰 원본(bag·라이다)은 저장소 밖 — 위치·sha256 은 각 폴더 `raw/README.md` 또는 `RAW_SOURCES.md`.

| 날짜 | 폴더 | 내용 | 대표 결과 |
|---|---|---|---|
| 09-12 ~ 09-18 | `2026-09-1x_*` (13개) | RT 커널·전원관리·IRQ·8 h soak 실측 (PC 단독) | `docs/can_stack_development.md` §5.A |
| 10-07 | [`2026-10-07_vehicle_lift/`](2026-10-07_vehicle_lift/) | 실차 인수일 리프트 송신 시험. `capture/` = Kvaser 원본·현장 기록(읽기 전용) | `analysis_report.md`, `signal_ranges.md` |
| 11-03 | [`2026-11-03_signal_audit/`](2026-11-03_signal_audit/) | 실차 신호 전수 검증, 원격 주행, 자율 명령 정지 시험 | `results.md`, `limits.md` |
| 11-04 | [`2026-11-04_signal_audit/`](2026-11-04_signal_audit/) | 현장 빠른 시험(가속 불감대·어댑터 종방향) 기록 | `quick_lon.md`, `results.md` |
| 11-04 | [`2026-11-04_drive_log/`](2026-11-04_drive_log/) | 원격·자율 주행 로그 요약 + 인터페이스 데이터시트. `raw_can/` = CAN 원본(zstd) | `README.md`, `sheets/sheet0~5` |

테스트 단위 결과(00~07 판정)는 `bsw_test/<ID>/docs/results.md`, 보고서·논문 자료는 `reports/`.
