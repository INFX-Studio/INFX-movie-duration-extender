"""ffmpeg/ffprobe 실행 파일 경로 해석.

- 패키징된 앱(PyInstaller): 번들 내부의 ``ffmpeg/`` 폴더를 사용합니다.
- 개발 환경: 저장소의 ``vendor/ffmpeg/`` 폴더를 사용합니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

EXE_SUFFIX = '.exe' if sys.platform == 'win32' else ''


def _binary_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'ffmpeg'
    return Path(__file__).resolve().parents[2] / 'vendor' / 'ffmpeg'


def ffmpeg_path() -> str:
    return str(_binary_dir() / f'ffmpeg{EXE_SUFFIX}')


def ffprobe_path() -> str:
    return str(_binary_dir() / f'ffprobe{EXE_SUFFIX}')
