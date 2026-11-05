# tools/drive_log — 주행 로그 분석 (11-04 부터)

결과는 `measurements/<날짜>_drive_log/` 에 쓴다. 저장소 루트에서 실행.

| 스크립트 | 입력 → 출력 |
|---|---|
| `cannpz.py` | `raw_can/*.zst` → 세션별 npz (0x210·0x200·0x201·0x156/0x157 디코딩, 공용 함수) |
| `launch_sens.py` | npz → `tables/launch_*.csv`, `sheets/sheet5_launch.png` (출발 민감도) |
| `coverage.py` | npz → `tables/interface_status.csv`, `sheets/sheet0_interface_status.png` (인터페이스 완성도) |
| `extract.py`, `hdr.py` | bag → 토픽 CSV, 헤더 지연 (대회 PC, A1_Total 메시지 정의 필요) |
| `iface.py` → `sheets.py` | 추출 CSV → `tables/*.csv` → `sheets/sheet1~4` (대회 PC 원본 경로 기준) |
| `remote.py`, `auto.py`, `autocan.py`, `minidf.py` | 요약 문서 1~2장 수치, pandas 대용 |

```bash
D=measurements/2026-11-04_drive_log
python3 tools/drive_log/cannpz.py $D/raw_can /tmp/npz
python3 tools/drive_log/launch_sens.py /tmp/npz $D/tables $D/sheets
python3 tools/drive_log/coverage.py   /tmp/npz $D/tables $D/sheets
```
