"""
Launch the real-vehicle (USER_ 0x200/0x201/0x210) bridge: can_raw_bridge + a1_status_decoder.

  ros2 launch a1_can_bridge a1_bridge.launch.py channel:=vcan0
  ros2 launch a1_can_bridge a1_bridge.launch.py channel:=can0 cpu_affinity:=8 rt_priority:=80

2026-10-06 실차 DBC(DBC/A1_dbc_2_fixed.dbc) 기준. 이 패키지의 유일한 launch.
감시 등급 토픽 — 차량으로 명령을 보내는 건 can_guard 하나뿐(이 launch 는 아무것도 송신하지 않는다).
race_state_publisher: can_guard StatusChannel → /race/state. can_guard 가 경기 모드가 아니면 아무것도 발행하지 않는다
(race_state:=false 로 끌 수 있음).
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    """Build the LaunchDescription for the real-vehicle bridge."""
    channel = LaunchConfiguration('channel')
    interface = LaunchConfiguration('interface')
    cpu_affinity = LaunchConfiguration('cpu_affinity')
    rt_priority = LaunchConfiguration('rt_priority')
    ids = LaunchConfiguration('ids')
    default_cg = os.path.join(os.environ.get('A1_BSW_ROOT', '~/git/A1-BSW'), 'safety', 'can_guard')

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
        DeclareLaunchArgument(
            'ids', default_value='0x200,0x201,0x210',
            description='raw 토픽으로 내보낼 CAN ID(커널 필터). 빈 값 = 전부. 11-04 전부(약 580 /s) 내보내다 52 s 밀림 → '
                        '디코더가 쓰는 0x200/0x201/0x210 만(약 145 /s). 앱 0x156/0x157(각 100 Hz)·원본 전체는 candump/Kvaser 로그',
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
                'ids': ParameterValue(ids, value_type=str),
            }],
            output='screen',
        ),
        Node(package='a1_can_bridge', executable='a1_status_decoder',
             name='a1_status_decoder', output='screen'),
        DeclareLaunchArgument('race_state', default_value='true',
                              description='can_guard StatusChannel → /race/state 발행 노드'),
        DeclareLaunchArgument('can_guard_dir', default_value=default_cg,
                              description='protocol.py 위치(구조체 정의 공유)'),
        DeclareLaunchArgument('status_shm', default_value='a1_can_guard_status'),
        Node(package='a1_can_bridge', executable='race_state_publisher', name='a1_race_state_publisher',
             parameters=[{
                 'can_guard_dir': ParameterValue(LaunchConfiguration('can_guard_dir'), value_type=str),
                 'status_shm': ParameterValue(LaunchConfiguration('status_shm'), value_type=str),
             }],
             output='screen', condition=IfCondition(LaunchConfiguration('race_state'))),
    ])
