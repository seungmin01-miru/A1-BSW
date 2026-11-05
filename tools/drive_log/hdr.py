import sys, rosbag2_py, numpy as np
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message
bag=sys.argv[1]; topics=sys.argv[2].split(',')
r=rosbag2_py.SequentialReader(); r.open(rosbag2_py.StorageOptions(uri=bag,storage_id='mcap'),rosbag2_py.ConverterOptions('cdr','cdr'))
ty={t.name:t.type for t in r.get_all_topics_and_types()}; r.set_filter(rosbag2_py.StorageFilter(topics=topics))
d={t:[] for t in topics}
while r.has_next():
    t,data,ts=r.read_next(); m=deserialize_message(data,get_message(ty[t])); h=m.header.stamp
    d[t].append((ts*1e-9, h.sec+h.nanosec*1e-9))
for t,v in d.items():
    v=np.array(v); lag=v[:,0]-v[:,1]; n=len(v)
    q=[lag[int(n*f)] if n else 0 for f in (0.05,0.25,0.5,0.75,0.95)]
    print(bag.split('/')[-2], t, 'n',n, 'rate %.1f'%(n/(v[-1,0]-v[0,0])), 'recv-stamp lag p50 %.3f p95 %.3f max %.3f'%(np.median(lag),np.percentile(lag,95),lag.max()), 'lag over time(5/25/50/75/95%%): '+' '.join('%.2f'%x for x in q))
