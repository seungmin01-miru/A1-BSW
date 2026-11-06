import sys, numpy as np
sys.path.insert(0, '/home/ailab/git/A1-BSW/tools/signal_audit')
import dbc_lite
db = dbc_lite.load('/home/ailab/git/A1-BSW/DBC/A1_dbc_2_fixed.dbc')
log = sys.argv[1]
rows = {0x200: [], 0x201: [], 0x210: []}
for line in open(log):
    try:
        ts, _, fr = line.split()
        cid, data = fr.split('#')
        cid = int(cid, 16)
    except ValueError:
        continue
    if cid in rows:
        d = bytes.fromhex(data); m = db[cid].decode(d)
        rows[cid].append((float(ts[1:-1]), m))
def arr(cid, keys):
    t = np.array([r[0] for r in rows[cid]]); return t, {k: np.array([r[1][k] for r in rows[cid]]) for k in keys}
t0, s200 = arr(0x200, ['steer_postion', 'break_postion'])
t1, s201 = arr(0x201, ['left_speed', 'right_speed'])
t2, s210 = arr(0x210, ['steer_command', 'break_command', 'acc_command'])
v = (s201['left_speed'] + s201['right_speed']) / 2 / 3.6  # m/s
T0 = t1[0]
print('span', t1[0], t1[-1], (t1[-1]-t1[0]))
dt = np.diff(t1, prepend=t1[0]); dist = np.sum(v*dt)
moving = v > 0.5/3.6
print('dist_m', dist, 'moving_s', np.sum(dt[moving]), 'vmax_kph', v.max()*3.6, 'vmean_moving_kph', v[moving].mean()*3.6)
for th in [10, 20, 25, 30]:
    print(f' time above {th} kph: {np.sum(dt[v*3.6>th]):.1f}s')
# 주행 구간(연속 이동) 나누기
seg=[]; inm=False
for i in range(len(t1)):
    if moving[i] and not inm: st=i; inm=True
    if not moving[i] and inm:
        if t1[i]-t1[st] > 3: seg.append((st,i))
        inm=False
print('segments', len(seg))
for a,b in seg:
    print(f'  {t1[a]-T0:7.1f}~{t1[b]-T0:7.1f}s  {t1[b]-t1[a]:6.1f}s  dist {np.sum(v[a:b]*dt[a:b]):7.1f}m  vmax {v[a:b].max()*3.6:5.1f}')
# 가속도: 0x201 속도를 0.5s 창 이동평균 후 미분
from numpy.lib.stride_tricks import sliding_window_view
def smooth(x, n): k=np.ones(n)/n; return np.convolve(x, k, mode='same')
vs = smooth(v, 12)  # ~0.25s
a = np.gradient(vs, t1)
a = smooth(a, 12)
# 명령 보간
acc = np.interp(t1, t2, s210['acc_command']); brk = np.interp(t1, t2, s210['break_command'])
# 지연 0.4s 반영: 0.4s 전 명령 기준
acc_d = np.interp(t1-0.5, t2, s210['acc_command']); brk_d = np.interp(t1-0.3, t2, s210['break_command'])
print('\n가속 % 구간(브레이크 0, 지연 0.5s 반영, 이동 중) → a [m/s^2]  (p50 / p90 / n, 속도대)')
for lo,hi in [(0,1),(1,60),(60,75),(75,85),(85,95),(95,101)]:
    msk = (acc_d>=lo)&(acc_d<hi)&(brk_d<1)&(brk<1)&moving
    for vlo,vhi in [(0,10),(10,20),(20,40)]:
        mm = msk&(v*3.6>=vlo)&(v*3.6<vhi)
        if mm.sum()>20: print(f'  acc {lo:3d}~{hi:3d}% v{vlo}-{vhi}: a p50 {np.median(a[mm]):+.2f} p90 {np.percentile(a[mm],90):+.2f} p10 {np.percentile(a[mm],10):+.2f} n={mm.sum()}')
print('\n브레이크 % 구간(가속 0, 지연 0.3s 반영, 2 km/h 이상) → a')
for lo,hi in [(1,20),(20,40),(40,60),(60,82),(82,101)]:
    msk=(brk_d>=lo)&(brk_d<hi)&(acc<1)&(v*3.6>2)
    if msk.sum()>10: print(f'  brk {lo:3d}~{hi:3d}%: a p50 {np.median(a[msk]):+.2f} p10 {np.percentile(a[msk],10):+.2f} n={msk.sum()}')
print('max accel', a[moving].max(), 'max decel', a[moving].min())
# 조향 명령 vs 위치: 지연 추정(상호상관)
ts = np.arange(t0[0], t0[-1], 0.01)
pos = np.interp(ts, t0, s200['steer_postion']); cmd = np.interp(ts, t2, s210['steer_command'])
best=None
for lag in range(0, 60):
    e = np.sqrt(np.mean((pos[lag:]-cmd[:len(cmd)-lag])**2))
    if best is None or e<best[1]: best=(lag,e)
print('\nsteer lag(best rms) ms', best[0]*10, 'rms', best[1])
err = pos - cmd
print('steer cmd range', s210['steer_command'].min(), s210['steer_command'].max(), 'pos range', s200['steer_postion'].min(), s200['steer_postion'].max())
print('steer |pos-cmd| p50/p95 (no lag)', np.percentile(abs(err),50), np.percentile(abs(err),95))
lag=best[0]; e2=pos[lag:]-cmd[:len(cmd)-lag]
print('steer |pos-cmd| p50/p95 (lag)', np.percentile(abs(e2),50), np.percentile(abs(e2),95))
rate = np.abs(np.gradient(smooth(pos,5), ts)); print('steer rate p99 deg/s', np.percentile(rate,99), 'max', rate.max())
# 명령 사용 분포
print('acc cmd use: >0 frac', np.mean(s210['acc_command']>0), 'p50 when>0', np.median(s210['acc_command'][s210['acc_command']>0]), 'max', s210['acc_command'].max())
print('brk cmd use: >0 frac', np.mean(s210['break_command']>0), 'max', s210['break_command'].max())
print('brake pos max %', s200['break_postion'].max())
# 좌우 바퀴 속도 차
dv = s201['left_speed']-s201['right_speed']
print('L-R speed diff p50/p99 (moving>5kph)', np.median(dv[v*3.6>5]), np.percentile(abs(dv[v*3.6>5]),99))
