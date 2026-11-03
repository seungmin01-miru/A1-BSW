import os
import glob,collections,cantools,sys
S=os.path.dirname(os.path.abspath(__file__))
fx=cantools.database.load_file('/home/ailab/git/A1-BSW/DBC/A1_dbc_fixed.dbc')
vd=cantools.database.load_file('/home/ailab/git/A1-BSW/DBC/A1_dbc.dbc',strict=False)
ea=cantools.database.load_file(os.path.join(S,'eait.dbc'),strict=False)
fr=[]
for f in sorted(glob.glob('/home/ailab/a1_race_capture/vehicle_20261007/kvaser_ch0*.log')):
    for l in open(f):
        p=l.split()
        if len(p)<3 or '#' not in p[2]: continue
        i,d=p[2].split('#'); fr.append((float(p[0][1:-1]),int(i,16),bytes.fromhex(d)))
fr.sort()
print('frames',len(fr))
def check(db,ids,name):
    print('==',name)
    for fid in ids:
        m=db.get_message_by_frame_id(fid)
        rows=[d for t,i,d in fr if i==fid]
        dl=collections.Counter(len(d) for d in rows)
        used=0
        for s in m.signals:
            for b in range(s.start,s.start+s.length): used|=1<<b   # all little endian
        seen=0
        for d in rows: seen|=int.from_bytes(d.ljust(8,b'\0'),'little')
        undef=seen&~used
        err=0; viol=collections.Counter(); ext={}
        for d in rows:
            try: v=m.decode(d,decode_choices=False,scaling=True)
            except Exception as e: err+=1; continue
            for s in m.signals:
                x=v[s.name]
                lo,hi=ext.get(s.name,(x,x)); ext[s.name]=(min(lo,x),max(hi,x))
                if s.minimum is not None and (x<s.minimum-1e-9 or x>s.maximum+1e-9): viol[s.name]+=1
        print(f'0x{fid:03X} {m.name}: n={len(rows)} DLC={dict(dl)} (DBC {m.length}) decode_err={err} undefined_bits_set=0x{undef:016X}')
        for s in m.signals:
            print(f'   {s.name:28s} obs[{ext[s.name][0]:g},{ext[s.name][1]:g}] dbc[{s.minimum},{s.maximum}] x{s.scale} viol={viol[s.name]}')
check(fx,[0x200,0x201,0x210],'A1 fixed')
check(vd,[0x200,0x210],'A1 vendor')
check(ea,[0x156,0x157],'EAIT (remote)')
print('ids',sorted(hex(x) for x in set(i for _,i,_ in fr)))
