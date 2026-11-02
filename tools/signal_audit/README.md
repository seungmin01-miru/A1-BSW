# signal_audit — 실차 DBC 신호 전수 검증

10-07 리프트 시험은 조향·브레이크·auto 비트만 확인했다. 이 도구는 `DBC/A1_dbc_fixed.dbc` 의 **모든 신호(10개 메시지, 46개)**와
대회측 원격 0x156/0x157(7개)을 같은 방식(명령 → 차량 응답 → 녹화로 판정)으로 하나씩 확인하고, **각 명령의 한계치**를 구해
제어기 출력 클램프를 정하기 위한 것이다.

**이 도구는 아무것도 송신하지 않는다.** 송신은 늘 `safety/can_guard/can_guard.py` + `lift_cmd.py` 한 경로로만 한다
(이중 한계·자동 원위치·안전 동작이 거기 있다). 표준 라이브러리만 쓴다(cantools 불필요).

**현장 절차는 [`AUDIT_MANUAL.md`](AUDIT_MANUAL.md)** — 0 사전 확인 → 1 원격 물리 한계 → 2 0x210 응답 → 3 정리·클램프 반영.
조향은 정지(앞바퀴 땅), 가감속은 뒷바퀴 공회전 — 공회전 값은 출력 상한·불감대·지연용이고 **m/s² 매칭은 11-04 주행**.

| 파일 | 하는 일 |
|---|---|
| `new_session.sh [날짜]` | `measurements/<날짜>_signal_audit/` 기록 폴더를 만들고 아래 템플릿을 채워 넣는다 |
| `gen_cards.py` | DBC → `cards.md` 신호별 검증 카드(단계·자극·기대·판정 규칙·안전·10-07 결과). DBC 에 카드 없는 신호가 생기면 표시 |
| `judge.py <세션>` | `raw/` 녹화 → `results.md` (ID 주기, 전 신호 min/max·변화 여부·범위 위반, live_counter, 명령→응답, lift_cmd 이력) + `limits.md` |
| `limits.py <세션>` | 명령 한계 초안 `limits.md` — 차량이 낸 범위, 명령→응답 표(정착 상태만), 포화 추정, 제어기 클램프 칸, 5절 공회전(최고 속도·최대 각가속도·바퀴 지름 역산·계단별 불감대·지연·모양). judge.py 가 같이 부른다 |
| `plot.py <세션> [--from HH:MM:SS --to … --tag 이름]` | `plots/timeline*.png`(명령 vs 반응 5단 시간축, 메모 세로선) + `plots/limits.png`(명령→응답 산점도·중앙값·명령 한계) + `plots/freespin.png`(뒷바퀴 공회전 계단 응답 겹쳐 보기) |
| `audit_data.py` | 시계열·명령-응답 쌍·포화 계산 공용 (judge·limits·plot 이 같은 숫자를 쓰게) |
| `vendor_questions.md` | 송신 전에 받아야 할 답 + DBC·안전 질문. 세션에 `vendor_answers.md` 로 복사됨 |
| `dbc_lite.py` | 표준 라이브러리 DBC 파서(Intel 바이트 순서만) |
| `session_template/` | 세션 `README.md`·`answers.md` 원본 |

## 순서

1. **출발 전** — `vendor_questions.md` 의 "송신 전에 반드시"를 업체에 먼저 보낸다. 특히 V11(구동 조건): 10-07 에 acc 2~60 % 에서
   바퀴가 한 번도 안 돌아서 0x201 신호 4개를 하나도 확인하지 못했다.
2. **현장 도착** — `tools/signal_audit/new_session.sh` → 출력된 `D=...`, `m()` 을 터미널에 붙여 넣는다.
3. **P0 듣기** — 녹화를 `$D` 에서 시작(VEHICLE_MANUAL 1~2절과 같은 명령). 2분 기준선. 카운터·주기 카드는 이것만으로 판정된다.
4. **P1 리프트 송신** — `answers.md` 전제 조건 전부 O 이면 can_guard·lift_cmd 를 `$D` 에서 실행하고 `cards.md` 의 자극 칸을
   위에서부터 입력한다. 단계마다 `m "P1 조향 시작"` / `m "... 끝"`.
5. **P2** — SYS 계열 단자, 0x301 은 업체 답(V9·V10)을 받은 뒤에만.
6. **P3 원격 한계** — `cards.md` 의 "P3 한계 시험" L0~L4. 원격으로 물리 한계, 그 범위 안에서 0x210 관계표.
7. **끝나고** — `judge.py measurements/<세션>` → `results.md`·`limits.md`, `plot.py measurements/<세션>` → `plots/`.
   그림과 표를 보며 `cards.md` 판정 칸(O / X / 보류)과 `limits.md` 4절 제어기 클램프를 채운다.
   단계별 확대: `plot.py measurements/<세션> --from 11:26:00 --to 11:34:00 --tag L2`.
   `raw/README.md` 에 원본 보관 위치·해시를 남기고 커밋(원본 자체는 `.gitignore`).

## 10-07 로 시험

    python3 tools/signal_audit/judge.py /tmp/s1007 --raw a1_race_capture/vehicle_20261007
    python3 tools/signal_audit/plot.py  /tmp/s1007 --raw a1_race_capture/vehicle_20261007 --from 11:26:00 --to 11:34:00 --tag lift

조향 +1/+3/+8/+10° → +0.7/+2.3/+8.8/+11.0°, 브레이크 20/50/30 % → 2.0/5.0/2.8, auto 수락 10~22 ms 로
`measurements/2026-10-07_vehicle_lift/README.md` 와 같은 숫자가 나온다.

## 알려진 한계

- 0x210 은 송신자를 구분할 수 없다. 외부 송신자(원격 등)가 섞이면 응답 표가 오염된다 — 0.3 s 미만 깜빡임은 계단에서 빼지만,
  판정은 사람이 lift_cmd 이력(5절)과 대조해서 한다.
- 계단 응답은 1 s 뒤 위치 하나만 본다(정착값). 응답 시간·오버슛은 `measurements/2026-10-07_vehicle_lift/analysis/response.py` 방식으로 따로.
