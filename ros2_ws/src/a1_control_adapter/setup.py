import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'a1_control_adapter'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ailab',
    maintainer_email='ailab@example.com',
    description='A1-BSW — 제어기 출력(가감속 m/s², 타이어각 °) → can_guard 공유메모리 어댑터',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'cmd_adapter = a1_control_adapter.cmd_adapter:main',
            'test_target_pub = a1_control_adapter.test_target_pub:main',
        ],
    },
)
