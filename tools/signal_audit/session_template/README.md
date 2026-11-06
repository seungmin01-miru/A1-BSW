# __DATE__ 실차 신호 검증

> 만든 명령: `tools/signal_audit/new_session.sh __DATE__` · 직전 기록: `measurements/2026-10-07_vehicle_lift/`
> 기준 DBC: `DBC/A1_dbc_2_fixed.dbc`

## 파일

| 파일 | 누가 | 내용 |
|---|---|---|
| `answers.md` | 사람(현장) | 조건·전제 조건·단계별 시각 기록 |
| `vendor_answers.md` | 사람(현장) | 업체·대회측 질문 답 (`tools/signal_audit/vendor_questions.md` 사본) |
| `cards.md` | gen_cards.py → 사람 | 신호별 검증 카드 — 결과·판정 칸을 채운다 |
| `results.md` | judge.py | 녹화에서 계산한 숫자(신호 범위·카운터·응답) |
| `limits.md` | judge.py → 사람 | 명령 한계 초안 — 4절 제어기 클램프는 사람이 채움 |
| `plots/` | plot.py | timeline*.png(명령 vs 반응 시간축), limits.png(명령→응답 산점도) |
| `analysis/` | 사람 | 그날만 쓰는 분석 스크립트·그래프 |
| `raw/` | 녹화 | candump 원본·lift_cmd·can_guard 로그 — **git 에 안 올림**(raw/README.md 에 목록·해시만) |

## 요약 (끝나고 채움)

- 조건: 장소 / 차량 상태(리프트·주행) / 장치 / 비트레이트 / 한계값
- 검증 완료 신호: __ / 46 (+ 원격 7), 보류: __, 실패: __
- 새로 확정된 것:
- 못 한 것 · 다음에 할 것:
