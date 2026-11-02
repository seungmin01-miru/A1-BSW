#!/usr/bin/env python3
"""DBC → 신호별 검증 카드(cards.md). 실차 신호 전수 검증의 체크리스트.

  python3 tools/signal_audit/gen_cards.py                         # 표준출력
  python3 tools/signal_audit/gen_cards.py -o <세션폴더>/cards.md   # new_session.sh 가 부르는 형태

DBC 의 **모든** 메시지·신호를 한 줄씩 꺼내므로 빠뜨리는 신호가 없다. 각 줄에 붙는 자극(lift_cmd 명령)·기대값·판정 규칙·
안전 조건·10-07 결과는 아래 CARDS 표에서 온다. DBC 에 새 신호가 생겼는데 CARDS 에 없으면 "카드 없음"으로 표시되니
그때 CARDS 를 채운다(조용히 빠지지 않게).

자극 명령은 10-07 리프트 시험에서 실제로 쓴 lift_cmd 명령 형식 그대로다(auto <축> on/off, steer/brake/acc <값>, zero,
perception off). 송신 경로는 반드시 can_guard + lift_cmd 하나 — 이 도구는 아무것도 보내지 않는다.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import dbc_lite  # noqa: E402

DEFAULT_DBC = os.path.join(REPO, 'DBC', 'A1_dbc_fixed.dbc')

# 단계: P0 듣기만(송신 없음) / P1 리프트 송신(can_guard+lift_cmd) / P2 업체 입회·허락 필요(설정 송신, 안전 시험, 다른 단자)
# 상태: ✅ 확인 / ⚠️ 일부 / ❌ 미확인 / — 해당 없음
# key: (frame_id, signal) — 같은 이름이 여러 메시지에 있으므로 ID 로 구분
CARDS = {
    # ── 0x210 USER_control_command (PC → 차량) ──
    (0x210, 'steer_command'): dict(
        phase='P1', stim='`auto steer on` → `steer 1/-1/5/-5/10/-10/15/-15` (각 뒤 `steer 0`)',
        expect='0x200 steer_postion ≈ 명령(°), 부호 같음', rule='정착값/명령 0.9~1.2, 부호 일치, 음수도 대칭',
        safety='한계 ±15° (lift_cmd·can_guard 이중)',
        prior='⚠️ 양수 +1/+3/+8/+10° → +0.7/+2.3/+8.8/+11.0° (×0.1 확정). **음수 명령은 우리가 안 보냄**(원격 −3.5°만)'),
    (0x210, 'steer_is_auto_command'): dict(
        phase='P1', stim='`auto steer on` / `auto steer off`', expect='0x200 steer_is_auto 같은 값으로',
        rule='수락 지연 ≤ 50 ms, 다른 축 비트 불변', safety='조향 0° 에서만 켬',
        prior='✅ 축별 독립, 약 17 ms'),
    (0x210, 'break_command'): dict(
        phase='P1', stim='`auto brake on` → `brake 5/10/20/40/60` → `brake 0`',
        expect='0x200 break_postion ≈ 명령 ÷ 10', rule='위치×10 / 명령 0.9~1.1, 저값(5%) 불감대 확인',
        safety='한계 60 %', prior='⚠️ 20/30/50 % → 1.9/3.0/5.0. 저값·60 % 미시험'),
    (0x210, 'break_is_auto_command'): dict(
        phase='P1', stim='`auto brake on` / `auto brake off`', expect='0x200 break_is_auto 같은 값으로',
        rule='수락 지연 ≤ 50 ms', safety='브레이크 0 % 에서 켬', prior='✅ 축별 독립, 약 17 ms'),
    (0x210, 'acc_command'): dict(
        phase='P1', stim='**L4-4 공회전**: `auto acc on` → `acc 3/5/7/10` (각 3 s, lift_cmd `--acc-hold-s 3`)',
        expect='0x201 바퀴 rpm 상승, 0 되면 하강', rule='limits.md 5절: 불감대(처음 도는 %)·지연·모양(정착/계속 증가). '
        'm/s² 는 11-04 주행',
        safety='뒷바퀴 띄움·받침·출입 통제, 바퀴 속도 상한(V18), break_postion 0 확인 후',
        prior='❌ 2~60 % 전부 바퀴 0 — 11:00~11:07 은 break_postion 10.0, 11:19~11:33 은 브레이크 0 인데도 0. '
              '구동 enable 조건부터 업체에 확인'),
    (0x210, 'acc_is_auto_command'): dict(
        phase='P1', stim='`auto acc on` / `auto acc off`', expect='0x200 acc_is_auto 같은 값으로',
        rule='수락 지연 ≤ 50 ms', safety='가속 0 % 에서 켬', prior='✅ 축별 독립, 약 17 ms'),

    # ── 0x200 USER_control_info (차량 → PC) ──
    (0x200, 'steer_postion'): dict(
        phase='P1', stim='조향 명령 시험과 같이 + (수동 모드) 사람이 핸들을 좌·우로 끝까지',
        expect='명령과 같은 단위(°), +가 어느 쪽인지 확정', rule='+명령 → 핸들 방향 기록, 수동 최대각 기록',
        safety='수동 시험은 auto 000 상태에서', prior='⚠️ 단위 확인. **+가 좌/우인지 미기록**, 0 근처 약 +1° 오프셋'),
    (0x200, 'break_postion'): dict(
        phase='P1', stim='브레이크 명령 시험 + (수동) 페달 밟기',
        expect='명령 ÷ 10, 최대 10.0', rule='DBC 범위 0~30 중 실제 최대가 10.0 인지, 페달 밟으면 변하는지',
        safety='—', prior='⚠️ 명령 관계 확인. 원인 모를 10.0 고정 구간 있었음(10:58)'),
    (0x200, 'steer_is_auto'): dict(phase='P1', stim='`auto steer on/off`', expect='명령 비트 따라감',
                                   rule='0x210 비트와 일치', safety='—', prior='✅'),
    (0x200, 'break_is_auto'): dict(phase='P1', stim='`auto brake on/off`', expect='명령 비트 따라감',
                                   rule='0x210 비트와 일치', safety='—', prior='✅'),
    (0x200, 'acc_is_auto'): dict(phase='P1', stim='`auto acc on/off`', expect='명령 비트 따라감',
                                 rule='0x210 비트와 일치', safety='—', prior='✅'),
    (0x200, 'steer_live_counter'): dict(phase='P0', stim='없음(듣기만)', expect='+1 씩 증가, 255 → 0',
                                        rule='반복·건너뜀 비율, 정지 0', safety='—', prior='✅ 반복 약 3 %, 건너뜀 약 0.4 %'),
    (0x200, 'break_live_counter'): dict(phase='P0', stim='없음(듣기만)', expect='+1 씩 증가',
                                        rule='반복·건너뜀 비율, 정지 0', safety='—',
                                        prior='⚠️ 모듈 고장으로 11:17 까지 0 고정, 수리 후 증가 확인'),
    (0x200, 'acc_live_counter'): dict(phase='P0', stim='없음(듣기만)', expect='+1 씩 증가',
                                      rule='반복·건너뜀 비율, 정지 0', safety='—', prior='✅'),

    # ── 0x201 USER_right_wheel_info (차량 → PC, 이름과 달리 좌우 둘 다) ──
    (0x201, 'right_speed'): dict(
        phase='P1', stim='공회전(L3·L4-4). 엔코더 = 뒷바퀴(11-02 확인)', expect='> 0 km/h, rpm 과 비례',
        rule='limits.md 5절 바퀴 지름 역산 ≈ 줄자 실측, 좌우 차 작음', safety='acc_command 카드와 같음',
        prior='❌ 73분 내내 0 (바퀴가 실제로 돌았는지 미기록)'),
    (0x201, 'right_rpm'): dict(phase='P1', stim='공회전(L3·L4-4)', expect='> 0', rule='speed 와 비례', safety='—',
                               prior='❌ 내내 0'),
    (0x201, 'left_speed'): dict(phase='P1', stim='공회전(L3·L4-4)', expect='> 0 km/h', rule='right_speed 와 비슷(열린 차동)',
                                safety='—', prior='❌ 내내 0'),
    (0x201, 'left_rpm'): dict(phase='P1', stim='공회전(L3·L4-4)', expect='> 0', rule='left_speed 와 비례', safety='—',
                              prior='❌ 내내 0'),
    (0x201, 'right_live_counter'): dict(phase='P0', stim='없음', expect='+1 씩 증가', rule='반복·건너뜀 비율',
                                        safety='—', prior='✅'),
    (0x201, 'left_live_counter'): dict(phase='P0', stim='없음', expect='+1 씩 증가', rule='반복·건너뜀 비율',
                                       safety='—', prior='✅'),

    # ── SYS 계열: 10-07 단자에 안 보임. 어느 버스·커넥터인지부터 업체에 확인 ──
    (0x100, '*'): dict(
        phase='P2', stim='**송신 금지.** 다른 단자에서 듣기만 / 업체 질문 V9',
        expect='차량 내부(MCU→액추에이터) 명령으로 추정', rule='보이는 단자·주기 기록, 0x210 과 값이 같이 움직이는지',
        safety='업체 허락 없이 송신 금지', prior='❌ 안 보임. DBC 상 steer 배율 1 (0x210 은 0.1) — 의미 확인'),
    (0x110, '*'): dict(phase='P2', stim='다른 단자에서 듣기만', expect='0x200 조향 값과 같은 원천',
                       rule='0x200 steer_postion 과 일치 여부', safety='—', prior='❌ 안 보임'),
    (0x111, '*'): dict(phase='P2', stim='다른 단자에서 듣기만', expect='0x200 브레이크 값과 같은 원천',
                       rule='0x200 break_postion 과 일치 여부', safety='—', prior='❌ 안 보임'),
    (0x112, '*'): dict(phase='P2', stim='다른 단자에서 듣기만 + 가속 시험', expect='0x201 오른쪽과 같은 원천',
                       rule='0x201 과 일치, enc_*·wheel_*_size 값 기록', safety='—', prior='❌ 안 보임'),
    (0x113, '*'): dict(phase='P2', stim='다른 단자에서 듣기만 + 가속 시험', expect='0x201 왼쪽과 같은 원천',
                       rule='0x201 과 일치', safety='—',
                       prior='❌ 안 보임. **DBC 지적: 왼쪽 메시지에 wheel_right_size 이름**'),
    (0x114, '*'): dict(phase='P2', stim='다른 단자에서 듣기만', expect='가속 모듈 auto·카운터',
                       rule='0x200 acc_is_auto 와 일치', safety='—', prior='❌ 안 보임. DLC 2'),
    (0x301, '*'): dict(
        phase='P2', stim='**송신 금지.** 업체 질문 V10 (바퀴 크기·좌우 설정 방법, 현재 값)',
        expect='엔코더 설정(바퀴 지름 cm, 좌우)', rule='현재 설정값을 먼저 받아 기록 — 바꾸면 0x201 속도 계산이 달라짐',
        safety='업체 입회·원래 값 확보 전 송신 금지', prior='❌ 미확인'),

    # ── 원격 0x156/0x157 (DBC 밖, 대회측 원격 유닛 — 듣기·분석만) ──
    (0x156, 'EPS_En'): dict(phase='P3', stim='원격으로 조향 활성 켬·끔', expect='1 이면 차량 auto 조향 1 (약 20 ms)',
                            rule='0x200 steer_is_auto 와 일치', safety='—', prior='✅ En=1 → auto 111 약 20 ms'),
    (0x156, 'EPS_Speed'): dict(phase='P3', stim='원격 조향 속도 설정을 바꿔 봄', expect='조향 속도 제한값?',
                               rule='값 ↔ 0x200 최대 조향 속도(°/s) 관계', safety='—', prior='❌ 의미 미확인 (V16)'),
    (0x156, 'ACC_En'): dict(phase='P3', stim='원격으로 가감속 활성 켬·끔', expect='1 이면 차량 auto 브레이크·가속 1',
                            rule='0x200 break/acc_is_auto 와 일치', safety='—', prior='✅'),
    (0x156, 'AEB_En'): dict(phase='P3', stim='(대회측 설명 받은 뒤) AEB 켬', expect='?', rule='켤 때 바뀌는 신호 기록',
                            safety='대회측 입회', prior='❌ 미확인'),
    (0x156, 'Aliv_Cnt'): dict(phase='P0', stim='없음', expect='+1 씩 증가', rule='반복·건너뜀 비율', safety='—',
                              prior='✅ 10 ms 주기'),
    (0x157, 'EPS_Cmd'): dict(
        phase='P3', stim='**L1** 정지(앞바퀴 땅): 좌·우 끝까지 천천히 → 빠르게 좌우 왕복',
        expect='0x200 위치 ≈ 명령, 끝에서 포화', rule='limits.md: 포화 시작 명령·최대 위치·최대 조향 속도(°/s)',
        safety='정지 상태 끝까지 조향은 업체 허락(V17)', prior='❌ En=1 동안 조향 명령 0 뿐 — 미검증'),
    (0x157, 'ACC_Cmd'): dict(
        phase='P3', stim='**L3** 공회전 가속 +0.25/+0.5/+1.0 · **L2** 공회전(10 km/h) 감속 −0.5/−1/−2/−3',
        expect='가속 → 바퀴 각가속, 감속 → 브레이크 위치', rule='limits.md 5절: 공회전 최고 속도·최대 각가속도(출력 상한), '
        '브레이크 위치 포화. **m/s² 는 11-04 주행** (원격이 폐루프면 공회전에서 출력을 줄일 수 있음, V16)',
        safety='뒷바퀴 띄움·출입 통제·속도 상한(V18), 고속 공회전 중 강한 감속 금지', prior='⚠️ −3.0 → 브레이크 위치 6.4~10.0 (리프트, 바퀴 0)'),
}

PHASE_TEXT = {
    'P0': '듣기만 — 송신 없이 녹화만으로 판정(카운터·주기·범위). 어느 때든 녹화하고 있으면 자동으로 채워진다',
    'P1': '리프트 송신 — can_guard + lift_cmd. 전제 조건(answers.md) 전부 O 일 때만',
    'P2': '업체 입회·허락 — 설정 메시지, 다른 단자, 안전 시험. 질문 답을 먼저 받는다',
    'P3': '원격 주행 한계 — 대회측 원격(0x156/0x157)으로 차량 물리 한계를 찾고, 그 범위 안에서 0x210 으로 관계표를 만든다'
          '(아래 "P3 한계 시험")',
}

LIMIT_TESTS = '''## P3 한계 시험 — 상세 절차는 `tools/signal_audit/AUDIT_MANUAL.md`

조향은 **정지·앞바퀴 땅**, 가감속은 **뒷바퀴 공회전**. 원격(0x157)으로 물리 상한을 찾고, 같은 범위에서 0x210 응답을 본다.
공회전 바퀴 가속도는 차량 가감속도가 아니다 — **m/s² 매칭은 11-04 주행**(TRACK_MANUAL T3·T4).

| 단계 | 누가 | 조건 | 하는 것 | 얻는 것 |
|---|---|---|---|---|
| L0 | 원격 | 정지, 듣기만 | 조금씩 조작 → 0x157 만인지 0x210 도인지, 뒷바퀴 돌 때 0x201 움직이는지 | 원격 송신 ID, 0x201 동작 |
| L1 | 원격 | 정지, 앞바퀴 땅 | 좌·우 끝까지 천천히(3~5 s 유지) → 빠르게 왕복 2회 | 최대 핸들각·포화·최대 조향 속도·불감대 (limits.md 1·3절) |
| L1b | 사람 | 정지 | 핸들각 0/±30/±60/±90/±120/끝 에서 타이어 각(각도계) | 조향비 표 (fit_maps.py steer) |
| L3 | 원격 | 뒷바퀴 공회전 | 가속 +0.25 → +0.5 → +1.0 m/s², 각 3 s | 공회전 최고 속도·최대 각가속도 (limits.md 5절, freespin.png) |
| L2 | 원격 | 공회전 10 km/h | 감속 −0.5 → −1 → −2 → −3 m/s², 각 2 s | 브레이크 위치 포화·지연 |
| L4 | 우리 (can_guard, 원격 대기) | 정지 / 공회전 | 배율·부호, 조향 30~상한, 브레이크 10~100 %(바퀴 정지), 가속 3~10 %(공회전) | 0x210 응답·불감대·지연·모양 |

- 한 단계마다 `m "L3 시작"` / `m "L3 끝"` → `plot.py --from --to --tag L3` 로 그 구간만 확대해 본다.
- 원격 최대값이 **원격 유닛이 자른 값**인지 **차량 한계**인지 구분할 것(V16).
'''


def card_for(fid, sig):
    return CARDS.get((fid, sig)) or CARDS.get((fid, '*'))


def render(msgs, dbc_path):
    out = []
    w = out.append
    w('# 신호별 검증 카드\n')
    w(f'> 생성: `tools/signal_audit/gen_cards.py` ← `{os.path.relpath(dbc_path, REPO)}`. '
      f'DBC 메시지 {sum(m.sender != "REMOTE" for m in msgs.values())}개·신호 '
      f'{sum(len(m.signals) for m in msgs.values() if m.sender != "REMOTE")}개 + 원격(DBC 밖) '
      f'{sum(len(m.signals) for m in msgs.values() if m.sender == "REMOTE")}개.')
    w('> **결과** 칸은 현장에서 사람이 채우고(관측값·시각), **판정** 칸은 `judge.py` 의 results.md 를 보고 최종 기입한다'
      '(O / X / 보류 + 이유). 10-07 칸은 `measurements/2026-10-07_vehicle_lift/` 기준.\n')
    w('## 단계\n')
    for k, v in PHASE_TEXT.items():
        w(f'- **{k}** {v}')
    w('')
    w(LIMIT_TESTS)
    missing = []
    for fid in sorted(msgs):
        m = msgs[fid]
        w(f'## 0x{fid:03X} {m.name} (DLC {m.dlc}){" — DBC 밖, 원격 유닛" if m.sender == "REMOTE" else ""}\n')
        w('| ☐ | 신호 | 비트 | 배율·범위 | 단계 | 자극 (lift_cmd) | 기대 | 판정 규칙 | 안전 | 10-07 | 결과 | 판정 |')
        w('|---|---|---|---|---|---|---|---|---|---|---|---|')
        for s in m.signals:
            c = card_for(fid, s.name)
            if c is None:
                missing.append(f'0x{fid:03X}.{s.name}')
                c = dict(phase='?', stim='**카드 없음 — gen_cards.py CARDS 에 추가**', expect='', rule='',
                         safety='', prior='')
            sign = 'int' if s.signed else 'uint'
            scale = f'×{s.factor:g}' + (f' {s.offset:+g}' if s.offset else '')
            unit = f' {s.unit}' if s.unit else ''
            w(f'| ☐ | `{s.name}` | {s.start}\\|{s.length} {sign} | {scale} [{s.minimum:g}, {s.maximum:g}]{unit} '
              f'| {c["phase"]} | {c["stim"]} | {c["expect"]} | {c["rule"]} | {c["safety"]} | {c["prior"]} |  |  |')
        w('')
    if missing:
        w('## ⚠️ 카드 없는 신호\n')
        w('DBC 에 있으나 CARDS 표에 없음 — 검증 방법을 정해 `gen_cards.py` 에 추가할 것:\n')
        for x in missing:
            w(f'- {x}')
        w('')
    return '\n'.join(out), missing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dbc', default=DEFAULT_DBC)
    ap.add_argument('-o', '--output', help='쓸 파일(없으면 표준출력)')
    a = ap.parse_args()
    msgs = dbc_lite.load(a.dbc)
    msgs.update(dbc_lite.remote_messages())
    text, missing = render(msgs, a.dbc)
    if a.output:
        with open(a.output, 'w') as f:
            f.write(text + '\n')
        print(f'{a.output} 작성 — 카드 없는 신호 {len(missing)}개', file=sys.stderr)
    else:
        print(text)
    return 1 if missing else 0


if __name__ == '__main__':
    sys.exit(main())
