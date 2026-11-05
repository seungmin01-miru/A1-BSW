import sys, numpy as np; sys.path.insert(0,'/home/ailab/git/A1-BSW/tools/signal_audit'); sys.path.insert(0,'.'); import dbc_lite, minidf as pd
db=dbc_lite.load('/home/ailab/git/A1-BSW/DBC/A1_dbc_fixed.dbc')
R='/home/ailab/a1_race_capture/a1drive_20261104_164006_run'
def readcan(f):
    rows={0x200:[],0x201:[],0x210:[]}
    for line in open(f):
        p=line.split()
        try: cid,d=p[2].split('#'); cid=int(cid,16)
        except Exception: continue
        if cid in rows: rows[cid].append((float(p[0][1:-1]),db[cid].decode(bytes.fromhex(d))))
    out={}
    for c,r in rows.items():
        t=np.array([x[0] for x in r]); out[c]=(t,{k:np.array([x[1][k] for x in r]) for k in r[0][1]})
    return out
def sm(x,n): return np.convolve(x,np.ones(n)/n,'same')
runs=[('A','bsw3',1793778888.31,1793778907.0),('B','bsw3',1793779027.92,1793779092.0),('C1','bsw4',1793779220.12,1793779247.8),('D','bsw5',1793779367.56,1793779789.3)]
cache={}
for n,s,ta,tb in runs:
    if s not in cache: cache[s]=readcan(f'{R}/{s}/candump_kv0.log')
    c=cache[s]; t0,S0=c[0x200]; t1,S1=c[0x201]; t2,S2=c[0x210]
    m1=(t1>=ta)&(t1<=tb); m2=(t2>=ta)&(t2<=tb); m0=(t0>=ta)&(t0<=tb)
    v=(S1['left_speed']+S1['right_speed'])/2
    # 0x201 vs odom 속도
    od=pd.read_csv(f'out/{s}/localization__odom.csv'); vo=np.hypot(od.vx,od.vy)*3.6
    vw=v[m1]; vod=np.interp(t1[m1],od.t,np.asarray(vo)); mm=vod>10
    scale=np.median(vw[mm]/vod[mm]) if mm.sum() else np.nan
    # 조향 지연
    tt=np.arange(ta+1,tb-1,0.01); cmd=np.interp(tt,t2,S2['steer_command']); best=None
    for L in range(0,50):
        pos=np.interp(tt+L/100,t0,S0['steer_postion']); r=np.sqrt(np.mean((pos-cmd)**2))
        if best is None or r<best[1]: best=(L*10,r,np.percentile(abs(pos-cmd),95))
    # 가감속 명령
    acc=S2['acc_command'][m2]; br=S2['break_command'][m2]
    mode=np.where(br>0,-1,np.where(acc>0,1,0)); sw=np.sum(np.diff(mode)!=0)
    a=np.gradient(sm(v[m1]/3.6,12),t1[m1]); a=sm(a,12)
    print(f'{n} {s}: 0x201/odom 속도비 {scale:.3f} | 조향 0x210→0x200 지연 {best[0]} ms rms {best[1]:.1f} p95 {best[2]:.1f} 핸들° | acc% >0 {np.mean(acc>0):.2f} (p50 {np.median(acc[acc>0]) if (acc>0).any() else 0:.0f}) 100% {np.mean(acc>=100):.2f} brake>0 {np.mean(br>0):.2f} max {br.max():.0f} | 가속↔제동↔무 전환 {sw} 회 ({sw/(tb-ta)*60:.0f}/분) | a p1 {np.percentile(a,1):+.2f} p99 {np.percentile(a,99):+.2f} | vmax {vw.max():.1f}  brake_pos max {S0["break_postion"][m0].max():.1f}')
print('--- 거리/속도 (candump)')
for n,s,ta,tb in runs:
    c=cache[s]; t1,S1=c[0x201]; m=(t1>=ta)&(t1<=tb); v=(S1['left_speed'][m]+S1['right_speed'][m])/2
    d=np.sum(v[:-1]/3.6*np.diff(t1[m])); mv=v>15
    print(f'{n}: dist {d:.0f} m (/0.958 = {d/0.958:.0f} m) vmax {v.max():.1f} mean(>1) {v[v>1].mean():.1f} at>15 p5 {np.percentile(v[mv],5):.1f} p95 {np.percentile(v[mv],95):.1f}')
