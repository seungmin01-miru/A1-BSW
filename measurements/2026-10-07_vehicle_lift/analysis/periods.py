import os
import pickle,statistics as st,datetime,collections
S=os.path.dirname(os.path.abspath(__file__))
fr=pickle.load(open(S+'/frames.pkl','rb'))
T=lambda x: datetime.datetime.fromtimestamp(x).strftime('%H:%M:%S.%f')[:-3]
by=collections.defaultdict(list)
for t,i,d in fr: by[i].append((t,d))
def pct(a,q): a=sorted(a); return a[min(len(a)-1,int(len(a)*q))]
print('## periods (gaps>0.5s excluded)')
for i,L in sorted(by.items()):
    ts=[t for t,_ in L]; dd=[(b-a)*1000 for a,b in zip(ts,ts[1:]) if b-a<0.5]
    nom=st.median(dd)
    print(f'0x{i:03X} n={len(L)} med={nom:.2f} p99={pct(dd,.99):.2f} p99.9={pct(dd,.999):.2f} p99.99={pct(dd,.9999):.2f} max={max(dd):.2f} min={min(dd):.3f} >1.5x={sum(1 for x in dd if x>1.5*nom)} >2x={sum(1 for x in dd if x>2*nom)}')
    big=sorted(((b-a)*1000,a) for a,b in zip(ts,ts[1:]) if 0.5> b-a)[-3:]
    print('   worst:',[(f'{x:.1f}ms',T(a)) for x,a in big])
# bus load
dur=fr[-1][0]-fr[0][0]
sec=collections.Counter(int(t) for t,_,_ in fr)
print('bus load: avg fps', len(fr)/dur, 'max fps', max(sec.values()), 'max load % (125 bit/frame @500k)', max(sec.values())*125/5000)
# 0x210 double sender detection
ts=[t for t,_ in by[0x210]]
seg=[];s=None;p=None
for a,b in zip(ts,ts[1:]):
    if b-a<0.010:
        if s is None or a-p>1: 
            if s: seg.append((s,p))
            s=a
        p=b
if s: seg.append((s,p))
print('0x210 double-sender segs:',[(T(a),T(b)) for a,b in seg])
# 0x210 values exceeding our limits
ex=[(t,d) for t,d in by[0x210] if abs(int.from_bytes(d[0:2],'little',signed=True))>150 or d[4]>60 or int.from_bytes(d[2:4],'little')>60]
print('0x210 beyond our limits:',len(ex), T(ex[0][0]) if ex else '', T(ex[-1][0]) if ex else '')
c=collections.Counter((int.from_bytes(d[0:2],'little',signed=True),int.from_bytes(d[2:4],'little'),d[4],d[5]) for t,d in ex)
print(c.most_common(10))
# 0x157 steering cmd segments
st157=[(t,int.from_bytes(d[0:2],'little',signed=True)/10) for t,d in by[0x157] if d[0:2]!=b'\0\0']
seg=[];s=None;p=None
for t,v in st157:
    if s is None or t-p>0.5:
        if s: seg.append((s,p))
        s=t
    p=t
if s: seg.append((s,p))
print('0x157 steer nonzero segs',[(T(a),T(b),min(v for t,v in st157 if a<=t<=b),max(v for t,v in st157 if a<=t<=b)) for a,b in seg])
acc_hi=[(t,int.from_bytes(d[3:5],'little')*0.01-10.23) for t,d in by[0x157] if int.from_bytes(d[3:5],'little')*0.01-10.23>1.0001]
print('0x157 ACC>1:',len(acc_hi),T(acc_hi[0][0]),T(acc_hi[-1][0]),max(v for _,v in acc_hi))
en=[t for t,d in by[0x156] if d[0]&1]
print('0x156 EPS_En first/last', T(en[0]), T(en[-1]))
