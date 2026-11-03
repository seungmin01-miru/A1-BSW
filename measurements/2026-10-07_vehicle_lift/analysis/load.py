import os
import glob,datetime,pickle
fr=[]
for f in sorted(glob.glob('/home/ailab/a1_race_capture/vehicle_20261007/kvaser_ch0*.log')):
    for l in open(f):
        p=l.split()
        if len(p)<3 or '#' not in p[2]: continue
        i,d=p[2].split('#'); fr.append((float(p[0][1:-1]),int(i,16),bytes.fromhex(d)))
fr.sort()
pickle.dump(fr,open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'frames.pkl'),'wb'))
