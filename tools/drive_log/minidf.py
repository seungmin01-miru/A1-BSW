import csv, numpy as np
class S(np.ndarray):
    def median(self): return float(np.median(self))
    def mean(self, *a, **k): return float(np.mean(np.asarray(self)))
    def value_counts(self):
        u,c=np.unique(np.asarray(self),return_counts=True); return dict(zip(u.tolist(),c.tolist()))
    def to_dict(self): return self
    def dropna(self): return [x for x in np.asarray(self) if x==x and x!='']
    @property
    def values(self): return np.asarray(self)
class DF:
    def __init__(self, d): self.d=d
    def __getattr__(self,k):
        if k=='d': raise AttributeError
        return self.d[k].view(S)
    def __getitem__(self,k):
        if isinstance(k,list): return np.column_stack([self.d[c] for c in k]).astype(float).view(S)
        if isinstance(k,str): return self.d[k].view(S)
        return DF({c:v[np.asarray(k)] for c,v in self.d.items()})
    def __len__(self): return len(next(iter(self.d.values())))
    @property
    def iloc(self): 
        me=self
        class I:
            def __getitem__(s,k): return DF({c:v[k] for c,v in me.d.items()})
        return I()
def read_csv(p):
    with open(p) as f:
        r=csv.reader(f); h=next(r); cols=list(zip(*r))
    d={}
    for k,c in zip(h,cols):
        try: d[k]=np.array(c,dtype=float)
        except ValueError: d[k]=np.array(c,dtype=object)
    return DF(d)
