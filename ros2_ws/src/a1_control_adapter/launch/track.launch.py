"""
트랙 시험 launch: 상태 브리지(a1_bridge.launch.py) + 제어기 출력 어댑터(cmd_adapter, disarm 으로 시작).

  ros2 launch a1_control_adapter track.launch.py channel:=kv0                 # Kvaser 미러
  ros2 launch a1_control_adapter track.launch.py channel:=can0 params:=/path/track.yaml
  ros2 launch a1_control_adapter track.launch.py channel:=vcan0 test_pub:=true  # 제어기 대역까지(SIL)
  ros2 launch a1_control_adapter track.launch.py channel:=kv0 adapter:=false    # 브리지만(T1~T4, lift_cmd 사용)

can_guard 는 이 launch 에 넣지 않는다 — 격리 코어·SCHED_FIFO 로 따로 띄우고(tools/track_day/bringup.sh),
어댑터는 can_guard 의 공유메모리가 생길 때까지 1초마다 다시 연결을 시도한다.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Bridge + adapter (+ optional test publisher)."""
    share = get_package_share_directory('a1_control_adapter')
    bridge = os.path.join(get_package_share_directory('a1_can_bridge'), 'launch', 'a1_bridge.launch.py')
    params = LaunchConfiguration('params')
    return LaunchDescription([
        DeclareLaunchArgument('channel', default_value='vcan0', description='브리지가 들을 SocketCAN (kv0/can0/vcan0)'),
        DeclareLaunchArgument('params', default_value=os.path.join(share, 'config', 'track.yaml'),
                              description='어댑터 파라미터(표) 파일 — 현장에서 고친 사본을 넘긴다'),
        DeclareLaunchArgument('test_pub', default_value='false', description='제어기 대역 test_target_pub 도 띄움'),
        DeclareLaunchArgument('adapter', default_value='true',
                              description='false = 브리지만(표 측정 T1~T4 에서 lift_cmd 로 직접 명령할 때 — 둘 다 쓰면 공유메모리 충돌)'),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(bridge),
                                 launch_arguments={'channel': LaunchConfiguration('channel')}.items()),
        Node(package='a1_control_adapter', executable='cmd_adapter', name='a1_cmd_adapter',
             parameters=[params], output='screen', condition=IfCondition(LaunchConfiguration('adapter'))),
        Node(package='a1_control_adapter', executable='test_target_pub', name='a1_test_target_pub',
             output='screen', condition=IfCondition(LaunchConfiguration('test_pub'))),
    ])
