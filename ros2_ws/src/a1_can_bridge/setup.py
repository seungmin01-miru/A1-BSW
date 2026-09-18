import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'a1_can_bridge'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ailab',
    maintainer_email='ailab@example.com',
    description='A1-BSW Phase C — vcan0/can0 ↔ ROS2 브리지·디코더 (걷기골격: EAIT_INFO_SPD 슬라이스)',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'can_raw_bridge = a1_can_bridge.can_raw_bridge:main',
            'spd_decoder = a1_can_bridge.spd_decoder:main',
        ],
    },
)
