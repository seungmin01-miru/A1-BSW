import os
import pickle,statistics as st,datetime,collections,bisect
S=os.path.dirname(os.path.abspath(__file__))
fr=pickle.load(open(S+'/frames.pkl','rb'))
T=lambda x: datetime.datetime.fromtimestamp(x).strftime('%H:%M:%S.%f')[:-3]
by=collections.defaultdict(list)
for t,i,d in fr: by[i].append((t,d))
s16=lambda b: int.from_bytes(b,'little',signed=True)
v200=[(t,s16(d[0:2])/10,s16(d[2:4])/10,d[4]&7) for t,d in by[0x200]]
t200=[x[0] for x in v200]
dbl=[('11:06:28','11:06:31'),('11:06:36','11:06:38'),('11:08:05','11:08:12'),('11:08:21','11:08:23'),('11:08:32','11:08:34'),('11:08:53','11:09:06'),('11:03:30','11:09:06')]
def foreign(t): h=T(t)[:8]; return any(a<=h<=b for a,b in dbl)
x=[(t,d) for t,d in by[0x210] if s16(d[0:2])<-150]
print('steer<-15deg:',len(x),[ (T(t),s16(d[0:2]),d.hex()) for t,d in x[:5]])
# our 0x210 change events
cmd=[(t,s16(d[0:2])/10,int.from_bytes(d[2:4],'little'),d[4],d[5]&7) for t,d in by[0x210] if not foreign(t)]
ev=[]
for a,b in zip(cmd,cmd[1:]):
    if b[1:]!=a[1:] and b[0]-a[0]<0.1: ev.append((b,a))
print('our change events',len(ev))
# auto acceptance latency
lat=[]
for b,a in ev:
    if b[4]!=a[4]:
        k=bisect.bisect_left(t200,b[0])
        for j in range(k,min(k+50,len(v200))):
            if v200[j][3]==b[4]: lat.append(((v200[j][0]-b[0])*1000,T(b[0]),f'{a[4]:03b}->{b[4]:03b}')); break
L=[l[0] for l in lat]
print('auto latency ms n=%d min %.1f med %.1f max %.1f'%(len(L),min(L),st.median(L),max(L)))
print(sorted(lat)[-3:])
# steering steps
def step_resp(idx_cmd, idx_pos, label, minstep, thr):
    out=[]
    for n,(b,a) in enumerate(ev):
        if abs(b[idx_cmd]-a[idx_cmd])<minstep: continue
        # next change of same channel
        nxt=next((bb[0] for bb,aa in ev[n+1:] if bb[idx_cmd]!=aa[idx_cmd] or bb[4]!=aa[4]), b[0]+5)
        k=bisect.bisect_left(t200,b[0]); 
        if k>=len(v200): continue
        p0=v200[k][idx_pos]; seg=[v for v in v200[k:] if v[0]<nxt]
        if len(seg)<20: continue
        pss=st.mean(v[idx_pos] for v in seg[-10:])
        tgt=pss-p0
        if abs(tgt)<thr: out.append((T(b[0]),a[idx_cmd],b[idx_cmd],p0,pss,None,None,None,None,f'{v200[k][3]:03b}')); continue
        dead=next(((v[0]-b[0])*1000 for v in seg if abs(v[idx_pos]-p0)>=thr),None)
        t63=next(((v[0]-b[0])*1000 for v in seg if (v[idx_pos]-p0)/tgt>=0.63),None)
        t90=next(((v[0]-b[0])*1000 for v in seg if (v[idx_pos]-p0)/tgt>=0.90),None)
        rate=max(abs(seg[i+2][idx_pos]-seg[i][idx_pos])/(seg[i+2][0]-seg[i][0]) for i in range(len(seg)-2))
        out.append((T(b[0]),a[idx_cmd],b[idx_cmd],p0,pss,dead,t63,t90,rate,f'{v200[k][3]:03b}'))
    print('==',label)
    for o in out: print('  ',o[0],f'cmd {o[1]:+.1f}->{o[2]:+.1f} pos {o[3]:+.1f}->{o[4]:+.2f}',
        'dead %s t63 %s t90 %s rate %s'%tuple('-' if v is None else f'{v:.0f}' for v in o[5:9]),'veh_auto',o[9])
step_resp(1,1,'steer (deg)',0.5,0.2)
step_resp(2,2,'brake (cmd% -> pos)',5,0.2)
# remote En -> auto
print('== remote En latency')
e156=[(t,d[0]&1) for t,d in by[0x156]]
for a,b in zip(e156,e156[1:]):
    if b[1]!=a[1]:
        k=bisect.bisect_left(t200,b[0])
        want=7 if b[1] else None
        j=next((j for j in range(k,k+30) if (v200[j][3]==7)==(b[1]==1) and (b[1]==1 or v200[j][3]!=v200[k-1][3])),None)
        print('  ',T(b[0]),'En',a[1],'->',b[1],'veh auto before',f'{v200[k-1][3]:03b}', 'after %s at +%.1f ms'%(f'{v200[j][3]:03b}',(v200[j][0]-b[0])*1000) if j else '')
# remote ACC -> brake
print('== remote ACC_Cmd -> brake pos (En=1)')
t157=[(t,int.from_bytes(d[3:5],'little')*0.01-10.23) for t,d in by[0x157]]
en_t=[t for t,d in by[0x156] if d[0]&1]
import itertools
pairs=collections.defaultdict(list)
for t,b in [(v[0],v[2]) for v in v200]:
    if en_t and any(abs(t-e)<0.02 for e in en_t[bisect.bisect_left(en_t,t-0.02):bisect.bisect_left(en_t,t-0.02)+3]):
        k=bisect.bisect_right([x[0] for x in t157],t)-1
        pairs[round(t157[k][1],1)].append(b)
for a in sorted(pairs): print('   acc %+.1f: brake pos med %.1f max %.1f n=%d'%(a,st.median(pairs[a]),max(pairs[a]),len(pairs[a])))
# steer rate max overall and brake rate
r=max((abs(v200[i+2][1]-v200[i][1])/(v200[i+2][0]-v200[i][0]),T(v200[i][0])) for i in range(len(v200)-2) if v200[i+2][0]-v200[i][0]<0.1)
rb=max((abs(v200[i+2][2]-v200[i][2])/(v200[i+2][0]-v200[i][0]),T(v200[i][0])) for i in range(len(v200)-2) if v200[i+2][0]-v200[i][0]<0.1)
print('max steer rate',r,'max brake rate',rb)
