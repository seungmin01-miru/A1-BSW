import sys, json, numpy as np, datetime as dt
import minidf as pd
R='/home/ailab/a1_race_capture/a1drive_20261104_164006_run'
paths={k:np.loadtxt(f'{R}/meta/{f}',delimiter=',',skiprows=1) for k,f in [('short',"short_racingline_60m.csv"),('300m','racingline_300m.csv'),('lap','yongin_outer_lap.csv')]}
def hm(t): return dt.datetime.fromtimestamp(t).strftime('%H:%M:%S')
def lat_err(xy, P, closed):
    # 각 점에서 폴리라인까지 부호 있는 거리(좌 +)
    A=P[:-1]; B=P[1:]
    if closed: A=P; B=np.roll(P,-1,0)
    AB=B-A; L2=(AB**2).sum(1)
    out=np.empty(len(xy)); sidx=np.empty(len(xy))
    cum=np.concatenate([[0],np.cumsum(np.sqrt(L2))])
    for i,p in enumerate(xy):
        u=np.clip(((p-A)*AB).sum(1)/L2,0,1); C=A+AB*u[:,None]; d=np.hypot(*(p-C).T); j=d.argmin()
        cr=AB[j,0]*(p[1]-A[j,1])-AB[j,1]*(p[0]-A[j,0]); out[i]=np.sign(cr)*d[j]; sidx[i]=cum[j]+u[j]*np.sqrt(L2[j])
    return out, sidx
def load(s,n):
    try: return pd.read_csv(f'out/{s}/{n}.csv')
    except FileNotFoundError: return None
runs=[ # (이름, 세션, 경로, 시작, 끝)  follower 로그 기준
 ('A short 60m', 'bsw3','short',1793778888.31,1793778907.0),
 ('B 300m',      'bsw3','300m', 1793779027.92,1793779092.0),
 ('C lap1-a',    'bsw4','lap',  1793779220.12,1793779247.8),
 ('C lap1-b',    'bsw4','lap',  1793779293.70,1793779339.0),
 ('D lap2',      'bsw5','lap',  1793779367.56,1793779789.3),
]
for name,s,pk,ta,tb in runs:
    od=load(s,'localization__odom'); w=load(s,'control__status__wheel_info'); ci=load(s,'control__status__control_info')
    tg=load(s,'control__target'); ad=load(s,'control__adapter__status'); cb=load(s,'control__status__command_on_bus')
    g=load(s,'gsof_client__gsof__ins_solution_49'); rms=load(s,'gsof_client__gsof__ins_solution_rms_50'); lq=load(s,'lidar_odometry__pose_quality')
    gn=load(s,'a1_gnss__odom')
    m=(od.t>=ta)&(od.t<=tb); o=od[m]
    o=o.iloc[::max(1,len(o)//3000)]
    e,sp=lat_err(o[['x','y']].values, paths[pk], pk=='lap')
    wm=(w.t>=ta)&(w.t<=tb); v=(w.rs[wm]+w.ls[wm])/2
    dist=np.sum(v.values[:-1]/3.6*np.diff(w.t[wm].values))
    print(f'\n## {name} ({s}, {pk}) {hm(ta)}~{hm(tb)} {tb-ta:.1f}s  dist(0x201) {dist:.0f} m  s {sp.min():.0f}->{sp.max():.0f} m')
    print(f'  lat err: mean {e.mean():+.3f} |p50| {np.median(abs(e)):.3f} |p95| {np.percentile(abs(e),95):.3f} max {abs(e).max():.3f} rms {np.sqrt((e**2).mean()):.3f}')
    mv=v>15
    print(f'  v kph: max {v.max():.1f} mean(moving) {v[v>1].mean():.1f}  at>15kph mean {v[mv].mean():.1f} p5 {np.percentile(v[mv],5) if mv.sum() else 0:.1f} p95 {np.percentile(v[mv],95) if mv.sum() else 0:.1f}')
    # 조향: 목표 타이어각 vs 실제(핸들/7.5)
    tm=(tg.t>=ta)&(tg.t<=tb); cm=(ci.t>=ta)&(ci.t<=tb)
    st_t=tg.steer_tire[tm]; act=np.interp(tg.t[tm], ci.t[cm], ci.steer_pos[cm])/7.5
    best=min(range(0,60,2), key=lambda L: np.mean((np.interp(tg.t[tm]+L/100, ci.t[cm], ci.steer_pos[cm])/7.5-st_t)**2))
    act2=np.interp(tg.t[tm]+best/100, ci.t[cm], ci.steer_pos[cm])/7.5
    print(f'  steer tire cmd range {st_t.min():+.1f}~{st_t.max():+.1f}  |act-cmd| p50 {np.median(abs(act-st_t)):.2f} p95 {np.percentile(abs(act-st_t),95):.2f} | lag {best*10} ms → p95 {np.percentile(abs(act2-st_t),95):.2f}  mean offset(act-cmd) {np.mean(act2-st_t):+.2f}')
    print(f'  accel tgt: min {tg.accel[tm].min():+.2f} max {tg.accel[tm].max():+.2f} frac at a_max(0.6) {np.mean(tg.accel[tm]>=0.59):.2f}')
    if cb is not None:
        bm=(cb.t>=ta)&(cb.t<=tb)
        print(f'  0x210: acc% p50 {cb.acc_cmd[bm].median():.0f} max {cb.acc_cmd[bm].max()} frac100 {np.mean(cb.acc_cmd[bm]>=100):.2f}  brake% max {cb.brake_cmd[bm].max()} frac>0 {np.mean(cb.brake_cmd[bm]>0):.2f}  steer cmd {cb.steer_cmd[bm].min():+.1f}~{cb.steer_cmd[bm].max():+.1f}')
    am=(ad.t>=ta)&(ad.t<=tb); A_=ad[am]
    print(f'  adapter: armed {A_.sarm.mean():.2f}/{A_.larm.mean():.2f} writing {A_.writing.mean():.3f} input_age max {A_.age.max()*1000:.0f} ms p99 {np.percentile(A_.age,99)*1000:.0f} ms rej {A_.rej.max()} notes {set(A_.note.dropna())}')
    gm=(g.t>=ta)&(g.t<=tb); G=g[gm]
    print(f'  GNSS: gnss mode counts {G.gnss.value_counts()} imu {G.imu.value_counts()}')
    if rms is not None:
        rm=(rms.t>=ta)&(rms.t<=tb); Rr=rms[rm]; ph=np.hypot(Rr.pn,Rr.pe)
        print(f'  GNSS pos rms horiz p50 {ph.median():.3f} max {ph.max():.3f} m, hdg rms p50 {Rr.hdg_rms.median():.2f}')
    if lq is not None:
        qm=(lq.t>=ta)&(lq.t<=tb); print(f'  LO quality p5 {np.percentile(lq.v[qm],5):.2f} p50 {lq.v[qm].median():.2f} min {lq.v[qm].min():.2f}')
    # localization vs raw GNSS
    gnm=(gn.t>=ta)&(gn.t<=tb); G2=gn[gnm]
    dx=np.interp(G2.t,od.t,od.x)-G2.x; dy=np.interp(G2.t,od.t,od.y)-G2.y; dd=np.hypot(dx,dy)
    print(f'  loc-GNSS diff p50 {dd.median():.2f} p95 {np.percentile(dd,95):.2f} max {dd.max():.2f} m  (odom rate {len(od[m])/(tb-ta):.0f} Hz, max gap {np.diff(od.t[m]).max()*1000:.0f} ms)')
    # 가속도 GNSS(종) 
    ax=g.ax[gm]; print(f'  INS acc_long p1 {np.percentile(ax,1):+.2f} p99 {np.percentile(ax,99):+.2f}')
    # 횡편차 큰 지점
    big=np.argsort(-abs(e))[:1]
    for i in big: print(f'  worst lat at s={sp[i]:.0f} m t={hm(o.t.values[i])} e={e[i]:+.2f}')
    # 구간별 (lap) 최대 횡편차 100m 단위
    if pk=='lap' and sp.max()-sp.min()>300:
        bins=np.arange(0,2600,200); idx=np.digitize(sp,bins)
        print('  per 200m |lat| max:', ' '.join(f'{bins[k-1]:.0f}:{abs(e[idx==k]).max():.2f}' for k in np.unique(idx)))
