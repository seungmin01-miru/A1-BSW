import os,pickle,datetime,collections,bisect
S=os.path.dirname(os.path.abspath(__file__))
fr=pickle.load(open(S+'/frames.pkl','rb'))
T=lambda x: datetime.datetime.fromtimestamp(x).strftime('%H:%M:%S.%f')[:-3]
s16=lambda b: int.from_bytes(b,'little',signed=True)
u16=lambda b: int.from_bytes(b,'little')
FOREIGN=('11:03:30','11:09:06')
def mm(name,vals):
    vals=list(vals)
    if not vals: print(f'{name:48s} 없음'); return
    v=[x for x,_ in vals]; lo=min(vals); hi=max(vals)
    print(f'{name:48s} n={len(v):6d} min {lo[0]:+8.2f} ({T(lo[1])})  max {hi[0]:+8.2f} ({T(hi[1])})')
r210=[(t,d) for t,i,d in fr if i==0x210]
ours=[(t,d) for t,d in r210 if not FOREIGN[0]<=T(t)[:8]<=FOREIGN[1]]
fo=[(t,d) for t,d in r210 if FOREIGN[0]<=T(t)[:8]<=FOREIGN[1]]
for lab,L in (('0x210 can_guard',ours),('0x210 11:03~11:09 구간(우리+미확인)',fo)):
    mm(lab+' steer_cmd deg (auto S=1)',((s16(d[0:2])/10,t) for t,d in L if d[5]&1))
    mm(lab+' brake_cmd % (auto B=1)',((u16(d[2:4]),t) for t,d in L if d[5]&2))
    mm(lab+' acc_cmd % (auto A=1)',((d[4],t) for t,d in L if d[5]&4))
r156=[(t,d) for t,i,d in fr if i==0x156]; t156=[t for t,_ in r156]
def en(t):
    k=bisect.bisect_right(t156,t)-1; return r156[k][1][0]&1 if k>=0 else 0
r157=[(t,d) for t,i,d in fr if i==0x157]
mm('0x157 원격 EPS_Cmd deg (전체)',((s16(d[0:2])/10,t) for t,d in r157))
mm('0x157 원격 EPS_Cmd deg (En=1)',((s16(d[0:2])/10,t) for t,d in r157 if en(t)))
mm('0x157 원격 ACC_Cmd m/s2 (전체)',((u16(d[3:5])*0.01-10.23,t) for t,d in r157))
mm('0x157 원격 ACC_Cmd m/s2 (En=1)',((u16(d[3:5])*0.01-10.23,t) for t,d in r157 if en(t)))
r200=[(t,d) for t,i,d in fr if i==0x200]
mm('0x200 steer_pos deg (전체)',((s16(d[0:2])/10,t) for t,d in r200))
mm('0x200 steer_pos deg (원격 En=1)',((s16(d[0:2])/10,t) for t,d in r200 if en(t)))
mm('0x200 brake_pos (전체)',((s16(d[2:4])/10,t) for t,d in r200))
mm('0x200 brake_pos (원격 En=1)',((s16(d[2:4])/10,t) for t,d in r200 if en(t)))
mm('0x200 brake_pos (원격 En=0)',((s16(d[2:4])/10,t) for t,d in r200 if not en(t)))
r201=[(t,d) for t,i,d in fr if i==0x201]
mm('0x201 right km/h',((((u16(d[0:2]))&0xFFF)*0.1,t) for t,d in r201))
mm('0x201 left km/h',(((int.from_bytes(d[3:5],'little')&0xFFF)*0.1,t) for t,d in r201))
