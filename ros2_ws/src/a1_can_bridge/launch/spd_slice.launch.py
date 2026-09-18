"""
Launch the walking-skeleton slice: can_raw_bridge + spd_decoder together.

  ros2 launch a1_can_bridge spd_slice.launch.py channel:=vcan0
  ros2 launch a1_can_bridge spd_slice.launch.py channel:=can0 cpu_affinity:=8 rt_priority:=80

rt_priority>0 이면 SCHED_FIFO 를 시도한다 — sudo 없이 launch 하면 권한 실패 경고만 찍고
계속 동작(A-3/A-4). 실제 RT 측정 시에는 (rtprio 영구 부여 안 함):
  sudo chrt -f 80 sudo -u ailab ros2 launch a1_can_bridge spd_slice.launch.py ...
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Build the LaunchDescription for the SPD walking-skeleton slice."""
    channel = LaunchConfiguration('channel')
    interface = LaunchConfiguration('interface')
    cpu_affinity = LaunchConfiguration('cpu_affinity')
    rt_priority = LaunchConfiguration('rt_priority')

    return LaunchDescription([
        DeclareLaunchArgument(
            'channel', default_value='vcan0',
            description='SocketCAN 인터페이스 (vcan0 또는 can0)',
        ),
        DeclareLaunchArgument('interface', default_value='socketcan'),
        DeclareLaunchArgument(
            'cpu_affinity', default_value='',
            description='격리 코어 배치, 예 "8" (A-3)',
        ),
        DeclareLaunchArgument(
            'rt_priority', default_value='0',
            description='SCHED_FIFO 우선순위, 0=미적용',
        ),
        Node(
            package='a1_can_bridge', executable='can_raw_bridge', name='can_raw_bridge',
            parameters=[{'channel': channel, 'interface': interface,
                         'cpu_affinity': cpu_affinity, 'rt_priority': rt_priority}],
            output='screen',
        ),
        Node(
            package='a1_can_bridge', executable='spd_decoder', name='spd_decoder',
            output='screen',
        ),
    ])
