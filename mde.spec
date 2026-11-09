# PyInstaller spec: INFX Movie Duration Extender.app (macOS arm64)
import re
from pathlib import Path

ROOT = Path(SPECPATH)
VERSION = re.search(r"__version__ = '([^']+)'", (ROOT / 'src/mde/__init__.py').read_text(encoding='utf-8')).group(1)
APP_NAME = 'INFX Movie Duration Extender'

a = Analysis(
    [str(ROOT / 'run.py')],
    pathex=[str(ROOT / 'src')],
    binaries=[
        (str(ROOT / 'vendor/ffmpeg/ffmpeg'), 'ffmpeg'),
        (str(ROOT / 'vendor/ffmpeg/ffprobe'), 'ffmpeg'),
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
    target_arch='arm64',
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
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
