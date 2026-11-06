"""raw_can/*.zst (candump 형식) → 세션별 npz + 공용 읽기 함수. 표준 라이브러리 + numpy, 저장소 dbc_lite 사용.

    python3 cannpz.py <raw_can 폴더> <npz 출력 폴더>      # 한 번만 (약 1분)
세션마다 더 긴 파일(kvaser_ch0 = bringup~stop 전체)을 쓰고, 없으면 candump_kv0 를 쓴다.
"""
import glob, os, re, subprocess, sys
import numpy as np

REPO = os.path.dirname(os.path.abspath(__file__))
while not os.path.isdir(os.path.join(REPO, 'DBC')):   # 저장소 루트(DBC/ 가 있는 곳)까지 올라간다
    REPO = os.path.dirname(REPO)
sys.path.insert(0, os.path.join(REPO, 'tools', 'signal_audit'))
import dbc_lite  # noqa: E402

SCALE = 0.958   # 0x201 / GNSS 속도비 (measurements/2026-11-04_drive_log/README.md 2.4절, wheel_speed_vs_gnss.csv)
LINE = re.compile(r'^\((\d+\.\d+)\)\s+\S+\s+([0-9A-Fa-f]+)#([0-9A-Fa-f]*)')


def _ids():
    db = dbc_lite.load(os.path.join(REPO, 'DBC', 'A1_dbc_2_fixed.dbc'))
    rm = dbc_lite.remote_messages()
    return {0x210: db[0x210], 0x200: db[0x200], 0x201: db[0x201], 0x156: rm[0x156], 0x157: rm[0x157]}


def decode_lines(lines):
    ids = _ids()
    rows = {k: [] for k in ids}
    for ln in lines:
        m = LINE.match(ln)
        if not m:
            continue
        cid = int(m.group(2), 16)
        if cid in ids:
            d = ids[cid].decode(bytes.fromhex(m.group(3)))
            rows[cid].append([float(m.group(1))] + [d[s.name] for s in ids[cid].signals])
    out = {}
    for cid, r in rows.items():
        names = ['t'] + [s.name for s in ids[cid].signals]
        a = np.array(r) if r else np.zeros((0, len(names)))
        for i, n in enumerate(names):
            out[f'{cid:03x}_{n}'] = a[:, i]
    return out


def build(raw_dir, npz_dir):
    os.makedirs(npz_dir, exist_ok=True)
    for sd in sorted(glob.glob(f'{raw_dir}/*/')):
        sess = os.path.basename(sd.rstrip('/'))
        fs = sorted(glob.glob(f'{sd}*.zst'), key=lambda p: ('kvaser' not in p, p))   # kvaser_ch0 우선
        txt = subprocess.run(['zstd', '-dc', fs[0]], capture_output=True, text=True, check=True).stdout
        np.savez_compressed(f'{npz_dir}/{sess}.npz', src=os.path.basename(fs[0]), **decode_lines(txt.splitlines()))
        print(sess, os.path.basename(fs[0]))


def sessions(npz_dir):
    return {os.path.basename(p)[:-4]: p for p in sorted(glob.glob(f'{npz_dir}/*.npz'))}


def load(p):
    z = np.load(p)
    return dict(tc=z['210_t'], acc=z['210_acc_command'], brk=z['210_break_command'], steer_cmd=z['210_steer_command'],
                ts=z['200_t'], steer=z['200_steer_postion'], brkpos=z['200_break_postion'],
                tw=z['201_t'], v=(z['201_left_speed'] + z['201_right_speed']) / 2 / SCALE,
                lspd=z['201_left_speed'], rspd=z['201_right_speed'], lrpm=z['201_left_rpm'], rrpm=z['201_right_rpm'],
                ta=z['156_t'], app_en=np.maximum(z['156_EPS_En'], z['156_ACC_En']))


def app_mask(d, t, pad=2.0):
    """앱 원격(0x156 EPS_En/ACC_En = 1) 켜진 시간 ± pad — 이때 차량은 0x210 이 아니라 원격을 따른다."""
    on = d['ta'][d['app_en'] > 0.5]
    m = np.zeros(len(t), bool)
    if len(on) == 0:
        return m
    br = np.where(np.diff(on) > 0.5)[0]
    for a, b in zip(np.r_[on[0], on[br + 1]] - pad, np.r_[on[br], on[-1]] + pad):
        m |= (t >= a) & (t <= b)
    return m


def runs(mask):
    m = np.diff(np.r_[False, mask, False].astype(int))
    return list(zip(np.where(m == 1)[0], np.where(m == -1)[0]))


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])
