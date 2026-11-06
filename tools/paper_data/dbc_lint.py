#!/usr/bin/env python3
"""DBC 줄 단위 문법·의미 대조 → 논문용 표 (markdown) + 비트 배치 그림.

표준 라이브러리 + matplotlib(그림만). cantools 없이 DBC 문법(Vector DBC File Format 1.0.x 기준)을
정규식으로 줄마다 대조하고, 의미 점검(비트 범위·겹침·배율·범위 표현 가능성·VAL_ 참조 등)을 붙인다.

    python3 tools/paper_data/dbc_lint.py DBC/A1_dbc_2.dbc DBC/A1_dbc_2_fixed.dbc --out reports/2026-11-03_paper_data
"""
import argparse
import os
import re
from collections import defaultdict

IDENT = r'[A-Za-z_][A-Za-z0-9_]*'
NUM = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'

# 문법 규칙: (키워드, 엄격 정규식, 설명). 엄격식은 공백 한 칸 이상을 허용하되 토큰 순서·구분자를 강제한다.
RULES = {
    'VERSION': (re.compile(r'^VERSION\s+"[^"]*"$'), 'VERSION "<문자열>"'),
    'NS_': (re.compile(r'^NS_\s*:$'), 'NS_ : (뒤 줄에 키워드 목록)'),
    'NS_ITEM': (re.compile(r'^(CM_|BA_DEF_|BA_|VAL_|BA_DEF_DEF_|SIG_VALTYPE_|[A-Z_]+)$'), 'NS_ 목록 키워드'),
    'BS_': (re.compile(r'^BS_\s*:(\s*\d+\s*:\s*\d+\s*,\s*\d+)?$'), 'BS_: [baud:BTR1,BTR2]'),
    'BU_': (re.compile(rf'^BU_\s*:(\s+{IDENT})*\s*$'), 'BU_: {노드}'),
    'BO_': (re.compile(rf'^BO_\s+(\d+)\s+({IDENT})\s*:\s*(\d+)\s+({IDENT})$'),
            'BO_ <ID> <이름>: <DLC> <송신 노드>'),
    'SG_': (re.compile(rf'^SG_\s+({IDENT})\s*(M|m\d+)?\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*'
                       rf'\(\s*({NUM})\s*,\s*({NUM})\s*\)\s*\[\s*({NUM})\s*\|\s*({NUM})\s*\]\s*"([^"]*)"\s+'
                       rf'({IDENT}(?:\s*,\s*{IDENT})*)$'),
            'SG_ <이름> : <시작>|<길이>@<순서><부호> (<배율>,<오프셋>) [<최소>|<최대>] "<단위>" <수신 노드>'),
    'CM_': (re.compile(rf'^CM_\s+(?:SG_\s+(\d+)\s+({IDENT})\s+|BO_\s+(\d+)\s+|BU_\s+{IDENT}\s+)?"[^"]*"\s*;$'),
            'CM_ [SG_ <ID> <신호>] "<문자열>";'),
    'BA_DEF_': (re.compile(r'^BA_DEF_\s+(?:BU_|BO_|SG_|EV_)?\s*"\w+"\s+(STRING|INT\s+\S+\s+\S+|HEX\s+\S+\s+\S+|'
                           r'FLOAT\s+\S+\s+\S+|ENUM\s+.+)\s*;$'), 'BA_DEF_ [대상] "<이름>" <형> ;'),
    'BA_DEF_DEF_': (re.compile(r'^BA_DEF_DEF_\s+"\w+"\s+("[^"]*"|\S+)\s*;$'), 'BA_DEF_DEF_ "<이름>" <기본값>;'),
    'BA_': (re.compile(r'^BA_\s+"\w+"\s+(?:BU_\s+\w+\s+|BO_\s+\d+\s+|SG_\s+\d+\s+\w+\s+)?("[^"]*"|\S+)\s*;$'),
            'BA_ "<이름>" [대상] <값>;'),
    'VAL_': (re.compile(rf'^VAL_\s+(\d+)\s+({IDENT})((?:\s+-?\d+\s+"[^"]*")+)\s*;$'),
             'VAL_ <ID> <신호> {<정수> "<설명>"} ;'),
}


class Sig:
    def __init__(self, line_no, m, raw_line):
        g = m.groups()
        self.line = line_no
        self.name, self.mux = g[0], g[1]
        self.start, self.length = int(g[2]), int(g[3])
        self.intel = g[4] == '1'
        self.signed = g[5] == '-'
        self.factor, self.offset = float(g[6]), float(g[7])
        self.min, self.max = float(g[8]), float(g[9])
        self.unit, self.recv = g[10], g[11]
        self.leading_space = raw_line.startswith(' ')

    def raw_range(self):
        if self.signed:
            lo, hi = -(1 << (self.length - 1)), (1 << (self.length - 1)) - 1
        else:
            lo, hi = 0, (1 << self.length) - 1
        a, b = lo * self.factor + self.offset, hi * self.factor + self.offset
        return min(a, b), max(a, b)


def fmt(x):
    return f'{x:g}'


def lint(path):
    lines = open(path, 'rb').read().decode('ascii', errors='replace').split('\n')
    crlf = sum(l.endswith('\r') for l in lines)
    rows, issues = [], []           # rows: 줄별 대조, issues: (등급, 위치, 내용)
    msgs, cur, in_ns = {}, None, False
    vals, cms = [], []
    seen_kw = []
    typos = []

    def issue(sev, where, text):
        issues.append((sev, where, text))

    for i, raw in enumerate(lines, 1):
        line = raw.rstrip('\r')
        s = line.strip()
        if not s:
            cur = None if cur is not None and not in_ns else cur
            in_ns = False if in_ns and not s else in_ns
            continue
        kw = s.split()[0].rstrip(':')
        if in_ns and kw in ('CM_', 'BA_DEF_', 'BA_', 'VAL_', 'BA_DEF_DEF_', 'SIG_VALTYPE_') and len(s.split()) == 1:
            key = 'NS_ITEM'
        else:
            key = kw if kw in RULES else None
            in_ns = False
        if key == 'NS_':
            in_ns = True
        if key is None:
            rows.append((i, kw, 'X', '알 수 없는 키워드', s))
            issue('오류', f'L{i}', f'알 수 없는 줄: `{s[:60]}`')
            continue
        rx, desc = RULES[key]
        m = rx.match(s)
        note = []
        if key not in ('NS_ITEM', 'SG_'):
            seen_kw.append(key)
        if not m:
            rows.append((i, key, 'X', desc, s))
            issue('오류', f'L{i}', f'{key} 문법 불일치: `{s[:70]}`')
            continue
        if key == 'BO_':
            fid, name, dlc, tx = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
            cur = {'id': fid, 'name': name, 'dlc': dlc, 'tx': tx, 'sigs': [], 'line': i}
            if fid in msgs:
                issue('오류', f'L{i}', f'ID {fid} 중복')
            msgs[fid] = cur
            if fid > 0x7FF:
                note.append('29비트 ID')
            if not 0 <= dlc <= 8:
                issue('오류', f'L{i}', f'{name}: DLC {dlc} (classic CAN 0~8)')
            if tx == 'Vector__XXX':
                note.append('송신 노드 미지정')
            note.append(f'0x{fid:03X}, DLC {dlc}')
        elif key == 'SG_':
            if cur is None:
                rows.append((i, key, 'X', 'BO_ 블록 밖 SG_', s))
                issue('오류', f'L{i}', 'BO_ 블록 밖 SG_')
                continue
            sg = Sig(i, m, line)
            cur['sigs'].append(sg)
            if not sg.leading_space:
                note.append('들여쓰기 없음(관례 위반, 파서 허용)')
            if not sg.intel:
                note.append('Motorola')
            where = f'L{i} 0x{cur["id"]:03X}.{sg.name}'
            if sg.start + sg.length > cur['dlc'] * 8:
                issue('오류', where, f'비트 {sg.start}+{sg.length} > DLC×8={cur["dlc"] * 8}')
            lo, hi = sg.raw_range()
            if sg.min < lo - 1e-9 or sg.max > hi + 1e-9:
                issue('오류', where, f'[{fmt(sg.min)}|{fmt(sg.max)}] 가 표현 가능 범위 [{fmt(lo)}|{fmt(hi)}] 밖')
            if sg.min > sg.max:
                issue('오류', where, '최소 > 최대')
            if sg.length == 1:
                if sg.factor != 1 or sg.offset != 0:
                    issue('오류', where, f'1비트 플래그인데 배율 {fmt(sg.factor)} → 물리값 0/{fmt(sg.factor)} (VAL_ 0/1 과 불일치)')
                if sg.signed:
                    issue('경고', where, '1비트 부호형')
            elif sg.signed and sg.min >= 0:
                issue('참고', where, f'부호형(-)인데 범위 [{fmt(sg.min)}|{fmt(sg.max)}] 가 음수 없음')
            if sg.length > 1 and not sg.unit and 'counter' not in sg.name and 'side' not in sg.name:
                issue('경고', where, '단위 없음 ("")')
            if re.search(r'postion|break', sg.name):
                typos.append(f'0x{cur["id"]:03X}.{sg.name}')
            if sg.recv == 'Vector__XXX':
                pass
            note.insert(0, f'**{sg.name}**')
            note.append(f'{sg.start}|{sg.length} {"부호" if sg.signed else "무부호"} ×{fmt(sg.factor)} '
                        f'[{fmt(sg.min)}|{fmt(sg.max)}] 표현 [{fmt(lo)}|{fmt(hi)}]')
        elif key == 'VAL_':
            vals.append((i, int(m.group(1)), m.group(2), re.findall(r'(-?\d+)\s+"([^"]*)"', m.group(3))))
            note.append(f'0x{int(m.group(1)):03X}.{m.group(2)} = ' + ', '.join(f'{v}:{d}' for v, d in vals[-1][3]))
        elif key == 'CM_':
            if m.group(1):
                cms.append((i, int(m.group(1)), m.group(2)))
                note.append(f'주석 → 0x{int(m.group(1)):03X}.{m.group(2)}')
        rows.append((i, key, 'O', desc, '; '.join(note)))

    # 블록 단위 의미 점검
    for fid, msg in msgs.items():
        names = [s.name for s in msg['sigs']]
        for n in set(names):
            if names.count(n) > 1:
                issue('오류', f'0x{fid:03X}', f'신호 이름 중복 {n}')
        used = defaultdict(list)
        for s in msg['sigs']:
            for b in range(s.start, s.start + s.length):
                used[b].append(s.name)
        for b, who in sorted(used.items()):
            if len(who) > 1:
                issue('오류', f'0x{fid:03X}', f'비트 {b} 겹침: {who}')
        msg['used'] = used
        msg['free'] = [b for b in range(msg['dlc'] * 8) if b not in used]
        if 'left' in msg['name']:
            for s in msg['sigs']:
                if 'right' in s.name:
                    issue('경고', f'L{s.line} 0x{fid:03X}.{s.name}', '왼쪽 바퀴 메시지에 right 이름(복사 오류 추정)')
    for i, fid, sname, pairs in vals:
        msg = msgs.get(fid)
        s = next((x for x in msg['sigs'] if x.name == sname), None) if msg else None
        if s is None:
            issue('오류', f'L{i}', f'VAL_ 대상 없음 {fid}.{sname}')
            continue
        defined = {int(v) for v, _ in pairs}
        lo, hi = (0, (1 << s.length) - 1) if not s.signed else (-(1 << (s.length - 1)), (1 << (s.length - 1)) - 1)
        if s.length <= 2:
            missing = [v for v in range(lo, hi + 1) if v not in defined]
            if missing:
                issue('참고', f'L{i} 0x{fid:03X}.{sname}', f'VAL_ 미정의 raw 값 {missing}')
        if s.factor != 1:
            issue('경고', f'L{i} 0x{fid:03X}.{sname}', f'VAL_ 은 raw 기준인데 배율 {fmt(s.factor)} — 물리값과 어긋남')
    for i, fid, sname in cms:
        msg = msgs.get(fid)
        if not msg or not any(x.name == sname for x in msg['sigs']):
            issue('오류', f'L{i}', f'CM_ 대상 없음 {fid}.{sname}')
    # 같은 이름 신호가 SYS/USER 에서 배율이 다른지
    by_name = defaultdict(list)
    for fid, msg in msgs.items():
        for s in msg['sigs']:
            by_name[s.name].append((fid, s))
    for n, lst in by_name.items():
        fs = {s.factor for _, s in lst}
        if len(fs) > 1:
            issue('경고', n, '같은 이름 다른 배율: ' + ', '.join(f'0x{f:03X} ×{fmt(s.factor)}' for f, s in lst))
    if typos:
        issue('참고', f'신호 {len(typos)}개', '철자 postion→position, break→brake (호환 위해 이름 유지 권장): ' + ', '.join(typos))
    for k in ('VERSION', 'NS_', 'BS_', 'BU_'):
        if k not in seen_kw:
            issue('오류', '파일', f'{k} 없음')
    if 'GenMsgCycleTime' not in open(path, errors='replace').read():
        issue('참고', '파일', 'GenMsgCycleTime(주기) 속성 없음 — 주기는 03_bus_timing 실측값 사용')
    if all(m['tx'] == 'Vector__XXX' for m in msgs.values()):
        issue('참고', '파일', '모든 메시지 송신/수신 노드 Vector__XXX — 송수신 주체는 실측(03)으로 구분')
    return dict(path=path, lines=len(lines), crlf=crlf, rows=rows, issues=issues, msgs=msgs, vals=vals)


SEV_ORDER = {'오류': 0, '경고': 1, '참고': 2}


def report(results, out_md):
    L = []
    w = L.append
    w('# 표 1. DBC 문법·의미 대조 (줄 단위)\n')
    w('> 생성: `tools/paper_data/dbc_lint.py` — 기준: Vector DBC File Format(BO_/SG_/VAL_/CM_/BA_* 문법). '
      '판정 O = 엄격 정규식 일치, X = 불일치. 의미 점검은 비트 범위·겹침·배율·표현 가능 범위·VAL_/CM_ 참조·이름 일관성.\n')
    w('## 1. 요약\n')
    w('| 파일 | 줄 | 개행 | 메시지 | 신호 | 문법 O / X | 오류 | 경고 | 참고 |')
    w('|---|---|---|---|---|---|---|---|---|')
    for r in results:
        o = sum(x[2] == 'O' for x in r['rows'])
        x = sum(x[2] == 'X' for x in r['rows'])
        c = {k: sum(1 for i in r['issues'] if i[0] == k) for k in SEV_ORDER}
        ns = sum(len(m['sigs']) for m in r['msgs'].values())
        w(f'| `{r["path"]}` | {r["lines"]} | {"CRLF" if r["crlf"] else "LF"} | {len(r["msgs"])} | {ns} | '
          f'{o} / {x} | {c["오류"]} | {c["경고"]} | {c["참고"]} |')
    w('')
    for r in results:
        w(f'## 2. 의미 점검 결과 — `{r["path"]}`\n')
        w('| 등급 | 위치 | 내용 |')
        w('|---|---|---|')
        for sev, where, text in sorted(r['issues'], key=lambda z: (SEV_ORDER[z[0]], z[1])):
            w(f'| {sev} | {where} | {esc(text)} |')
        w('')
    r = results[-1]
    w(f'## 3. 메시지별 비트 사용 — `{r["path"]}`\n')
    w('| ID | 이름 | DLC | 신호 수 | 사용 비트 | 미사용 비트 |')
    w('|---|---|---|---|---|---|')
    for fid, m in sorted(r['msgs'].items()):
        free = m['free']
        w(f'| 0x{fid:03X} | {m["name"]} | {m["dlc"]} | {len(m["sigs"])} | {len(m["used"])}/{m["dlc"] * 8} | '
          f'{compress(free) or "—"} |')
    w('')
    w(f'## 4. 줄별 대조 — `{r["path"]}`\n')
    w('| 줄 | 키워드 | 판정 | 해석 / 메모 |')
    w('|---|---|---|---|')
    for i, key, ok, desc, note in r['rows']:
        if key == 'NS_ITEM':
            continue
        w(f'| {i} | `{key}` | {ok} | {esc(note if ok == "O" else desc)} |')
    w('')
    if len(results) == 2:
        a, b = results
        w('## 5. 업체 원본 대비 수정본 차이\n')
        w('| 신호 | 원본 | 수정본 | 근거 |')
        w('|---|---|---|---|')
        for fid, m in sorted(b['msgs'].items()):
            ma = a['msgs'].get(fid)
            for s in m['sigs']:
                sa = next((x for x in ma['sigs'] if x.name == s.name), None) if ma else None
                if sa and (sa.factor, sa.min, sa.max, sa.signed) != (s.factor, s.min, s.max, s.signed):
                    w(f'| 0x{fid:03X}.{s.name} | ×{fmt(sa.factor)} [{fmt(sa.min)}\\|{fmt(sa.max)}] | '
                      f'×{fmt(s.factor)} [{fmt(s.min)}\\|{fmt(s.max)}] | 1비트 플래그 배율 오류 (CM_ 주석) |')
        w('')
    open(out_md, 'w').write('\n'.join(L))


def esc(t):
    return t.replace('|', '\\|')


def compress(bits):
    out, i = [], 0
    while i < len(bits):
        j = i
        while j + 1 < len(bits) and bits[j + 1] == bits[j] + 1:
            j += 1
        out.append(f'{bits[i]}' if i == j else f'{bits[i]}–{bits[j]}')
        i = j + 1
    return ', '.join(out)


def bitmap_fig(res, ids, out_png):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _style import plt
    msgs = [res['msgs'][i] for i in ids if i in res['msgs']]
    fig, axes = plt.subplots(len(msgs), 1, figsize=(11, 1.55 * len(msgs) + 0.4))
    palette = ['#4C78A8', '#F58518', '#54A24B', '#B279A2', '#E45756', '#72B7B2', '#EECA3B', '#9D755D']
    for ax, m in zip(axes, msgs):
        for b in range(64):
            ax.add_patch(plt.Rectangle((b, 0), 1, 1, fill=False, ec='#bbb', lw=0.5))
        for b in range(0, 65, 8):
            ax.axvline(b, color='#555', lw=1.2)
        for k, s in enumerate(m['sigs']):
            col = palette[k % len(palette)]
            bits = range(s.start, s.start + s.length)
            xs = list(bits)
            for x in xs:
                ax.add_patch(plt.Rectangle((x, 0), 1, 1, color=col, alpha=0.75))
            ax.text(sum(xs) / len(xs) + 0.5, 0.5, s.name if s.length >= 6 else s.name.split('_')[0][:5],
                    ha='center', va='center', fontsize=7 if s.length >= 8 else 5.5, color='white', weight='bold',
                    rotation=0 if s.length >= 6 else 90)
        ax.set_xlim(0, 64)
        ax.set_ylim(0, 1)
        ax.grid(False)
        ax.set_yticks([])
        ax.set_xticks([b * 8 + 4 for b in range(8)])
        ax.set_xticklabels([f'Byte {b} (bit {b * 8}–{b * 8 + 7})' for b in range(8)], fontsize=7)
        ax.set_title(f'0x{m["id"]:03X} {m["name"]} (DLC {m["dlc"]}) — Intel(little-endian) 비트 0→63, 흰 칸 = 미사용', fontsize=9, loc='left')
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dbc', nargs='+')
    ap.add_argument('--out', default='reports/2026-11-03_paper_data')
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, 'fig'), exist_ok=True)
    res = [lint(p) for p in a.dbc]
    report(res, os.path.join(a.out, '01_dbc_syntax_audit.md'))
    bitmap_fig(res[-1], [0x210, 0x200, 0x201], os.path.join(a.out, 'fig', 'fig1_bit_layout_user.png'))
    for r in res:
        print(r['path'], {k: sum(1 for i in r['issues'] if i[0] == k) for k in SEV_ORDER},
              'X', sum(x[2] == 'X' for x in r['rows']))


if __name__ == '__main__':
    main()
