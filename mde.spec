# PyInstaller spec: INFX Movie Duration Extender
# - macOS (arm64): INFX Movie Duration Extender.app
# - Windows (x64): INFX Movie Duration Extender\INFX Movie Duration Extender.exe
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
VERSION = re.search(r"__version__ = '([^']+)'", (ROOT / 'src/mde/__init__.py').read_text(encoding='utf-8')).group(1)
APP_NAME = 'INFX Movie Duration Extender'
IS_WINDOWS = sys.platform == 'win32'
EXE_SUFFIX = '.exe' if IS_WINDOWS else ''

a = Analysis(
    [str(ROOT / 'run.py')],
    pathex=[str(ROOT / 'src')],
    binaries=[
        (str(ROOT / f'vendor/ffmpeg/ffmpeg{EXE_SUFFIX}'), 'ffmpeg'),
        (str(ROOT / f'vendor/ffmpeg/ffprobe{EXE_SUFFIX}'), 'ffmpeg'),
    ],
    datas=[],
    hiddenimports=[],
    excludes=['tkinter'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    target_arch=None if IS_WINDOWS else 'arm64',
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
if not IS_WINDOWS:
    app = BUNDLE(
        coll,
        name=f'{APP_NAME}.app',
        bundle_identifier='kr.infx.movie-duration-extender',
        version=VERSION,
        info_plist={
            'CFBundleDisplayName': APP_NAME,
            'CFBundleShortVersionString': VERSION,
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '26.0',
        },
    )
