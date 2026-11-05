# bag → 토픽별 CSV. 열 t = 헤더 stamp(있으면, 2026-11-05 부터), t_bag = bag 기록 시각.
# 11-04 bag 은 /control/status/* 가 bag 시각으로 최대 52 s 늦게 찍혔다(can_raw_bridge 밀림) → 분석은 t 를 쓴다.
import sys, csv, os, json
import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

bag, out, topics = sys.argv[1], sys.argv[2], sys.argv[3].split(',')
os.makedirs(out, exist_ok=True)
r = rosbag2_py.SequentialReader()
r.open(rosbag2_py.StorageOptions(uri=bag, storage_id='mcap'), rosbag2_py.ConverterOptions('cdr','cdr'))
types = {t.name: t.type for t in r.get_all_topics_and_types()}
topics = [t for t in topics if t in types]
r.set_filter(rosbag2_py.StorageFilter(topics=topics))
cls = {t: get_message(types[t]) for t in topics}
files = {}; writers = {}
def w(t, row):
    if t not in writers:
        f = open(os.path.join(out, t.strip('/').replace('/','__')+'.csv'),'w',newline='')
        files[t]=f; writers[t]=csv.writer(f); writers[t].writerow(list(row.keys()))
    writers[t].writerow(list(row.values()))
while r.has_next():
    t, data, ts = r.read_next()
    m = deserialize_message(data, cls[t]); tb = ts*1e-9
    h = getattr(m, 'header', None)
    th = h.stamp.sec + h.stamp.nanosec*1e-9 if h is not None and (h.stamp.sec or h.stamp.nanosec) else tb
    ty = types[t]
    if ty == 'nav_msgs/msg/Odometry':
        p=m.pose.pose.position; q=m.pose.pose.orientation; v=m.twist.twist
        w(t, dict(t=th,t_bag=tb,x=p.x,y=p.y,qz=q.z,qw=q.w,vx=v.linear.x,vy=v.linear.y,wz=v.angular.z,cov_x=m.pose.covariance[0]))
    elif ty.endswith('ControllerOutput'):
        w(t, dict(t=th,t_bag=tb,accel=m.accel_mps2,steer_tire=m.steer_tire_deg))
    elif ty.endswith('ControlInfo'):
        w(t, dict(t=th,t_bag=tb,steer_pos=m.steer_position_deg,brake_pos=m.brake_position,sa=int(m.steer_auto),ba=int(m.brake_auto),aa=int(m.acc_auto),sok=int(m.steer_alive_ok)))
    elif ty.endswith('WheelInfo'):
        w(t, dict(t=th,t_bag=tb,rs=m.right_speed_kph,ls=m.left_speed_kph))
    elif ty.endswith('ControlCommand'):
        w(t, dict(t=th,t_bag=tb,steer_cmd=m.steer_cmd_deg,brake_cmd=m.brake_cmd_pct,acc_cmd=m.acc_cmd_pct,sa=int(m.steer_auto),ba=int(m.brake_auto),aa=int(m.acc_auto)))
    elif ty.endswith('AdapterStatus'):
        w(t, dict(t=th,t_bag=tb,sarm=int(m.steer_armed),larm=int(m.long_armed),writing=int(m.writing),age=m.input_age_s,in_acc=m.in_accel_mps2,in_st=m.in_steer_tire_deg,out_sw=m.out_steer_wheel_deg,out_b=m.out_brake_pct,out_a=m.out_acc_pct,rej=m.rejected_count,note=m.note))
    elif ty.endswith('NavigationSolution49'):
        w(t, dict(t=th,t_bag=tb,imu=m.status.imu_alignment,gnss=m.status.gnss,lat=m.lla.latitude,lon=m.lla.longitude,spd=m.total_speed,hdg=m.heading,yawrate=getattr(m,'ang_rate_down',float('nan')),ax=getattr(m,'acc_long',float('nan'))))
    elif ty.endswith('NavigationPerformance50'):
        w(t, dict(t=th,t_bag=tb,gnss=m.status.gnss,pn=m.pos_rms_error.north,pe=m.pos_rms_error.east,hdg_rms=m.attitude_rms_error_heading))
    elif ty == 'std_msgs/msg/String':
        w(t, dict(t=th,t_bag=tb,s=m.data))
    elif ty == 'std_msgs/msg/Float32':
        w(t, dict(t=th,t_bag=tb,v=m.data))
    elif ty.endswith('AccelWithCovarianceStamped'):
        a=m.accel.accel.linear; w(t, dict(t=th,t_bag=tb,ax=a.x,ay=a.y))
for f in files.values(): f.close()
print('done', bag, {t: types[t] for t in topics})
