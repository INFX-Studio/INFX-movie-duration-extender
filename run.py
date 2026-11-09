"""앱 실행 진입점 (개발 실행 및 PyInstaller 엔트리)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from mde.app import main

if __name__ == '__main__':
    sys.exit(main())
