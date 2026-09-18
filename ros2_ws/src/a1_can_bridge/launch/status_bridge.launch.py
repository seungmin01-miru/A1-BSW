"""
Launch the full status-bridge set: can_raw_bridge + all decoders (SPD/EPS/ACC/IMU).

  ros2 launch a1_can_bridge status_bridge.launch.py channel:=vcan0
  ros2 launch a1_can_bridge status_bridge.launch.py channel:=can0 cpu_affinity:=8 rt_priority:=80

spd_slice.launch.py 는 최소 재현용(걷기골격 첫 슬라이스)으로 그대로 둔다 — 이 파일이 실제 운용 조합.
EPS/ACC 는 Alive_Cnt 진단을 /diagnostics 로도 낸다(§2 카테고리 C E2E 체크).
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    """Build the LaunchDescription for the full status-bridge set."""
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
            description='can_raw_bridge 격리 코어 배치, 예 "8" (A-3)',
        ),
        DeclareLaunchArgument(
            'rt_priority', default_value='0',
            description='can_raw_bridge SCHED_FIFO 우선순위, 0=미적용',
        ),
        Node(
            package='a1_can_bridge', executable='can_raw_bridge', name='can_raw_bridge',
            # ParameterValue 로 타입을 강제한다 — 안 하면 "cpu_affinity:=8" 처럼 숫자로 보이는
            # 문자열을 launch_ros 가 params YAML 에 정수로 써버려 string 파라미터 선언과 충돌한다
            # (InvalidParameterTypeException, 2026-09-18 A-3 재측정에서 실제로 발견).
            parameters=[{
                'channel': ParameterValue(channel, value_type=str),
                'interface': ParameterValue(interface, value_type=str),
                'cpu_affinity': ParameterValue(cpu_affinity, value_type=str),
                'rt_priority': ParameterValue(rt_priority, value_type=int),
            }],
            output='screen',
        ),
        Node(package='a1_can_bridge', executable='spd_decoder',
             name='spd_decoder', output='screen'),
        Node(package='a1_can_bridge', executable='eps_decoder',
             name='eps_decoder', output='screen'),
        Node(package='a1_can_bridge', executable='acc_decoder',
             name='acc_decoder', output='screen'),
        Node(package='a1_can_bridge', executable='imu_decoder',
             name='imu_decoder', output='screen'),
    ])
