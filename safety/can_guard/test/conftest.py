"""테스트가 같은 디렉터리의 protocol.py 등을 `import protocol` 로 바로 쓸 수 있게 경로를 추가.
can_guard 는 ROS2 패키지가 아니라 독립 실행 스크립트 모음이라 setup.py/ament 없이 이렇게 한다."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
