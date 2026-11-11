"""GitHub Release 기반 자동 업데이트.

최신 릴리즈의 배포 파일과 `.sha256` 파일을 내려받아 해시를 확인한 뒤,
별도 헬퍼 스크립트가 앱 종료를 기다렸다가 설치된 앱을 교체하고 다시 실행합니다.

- macOS: DMG 를 받아 `/Applications` 의 번들을 교체합니다. (bash)
- Windows: zip 을 받아 실행 파일이 있는 폴더를 교체합니다. (PowerShell)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

log = logging.getLogger(__name__)

REPO = 'INFX-Studio/INFX-movie-duration-extender'
LATEST_RELEASE_URL = f'https://api.github.com/repos/{REPO}/releases/latest'
USER_AGENT = 'INFX-Movie-Duration-Extender-Updater'
CHECK_TIMEOUT = 5
DOWNLOAD_TIMEOUT = 60

IS_WINDOWS = sys.platform == 'win32'
MACOS_ASSET_SUFFIX = '.dmg'
WINDOWS_ASSET_SUFFIX = '-win64.zip'
# Windows zip 의 최상위 폴더 이름. (scripts/build_app.ps1 이 만드는 dist 폴더 이름과 같아야 합니다.)
WINDOWS_ZIP_FOLDER = 'INFX Movie Duration Extender'

_VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')
_SHA256_RE = re.compile(r'\b([0-9a-fA-F]{64})\b')


class UpdateError(Exception):
    """업데이트 확인·적용 중 발생한 오류."""


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(text.strip())
    if not match:
        raise ValueError(f'버전 형식이 아닙니다: {text!r}')
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer(tag: str, current: str) -> bool:
    return parse_version(tag) > parse_version(current)


@dataclass
class ReleaseInfo:
    tag: str
    asset_name: str
    asset_url: str
    sha256_url: str

    @classmethod
    def from_json(cls, data: dict) -> ReleaseInfo:
        """릴리즈 자산 중 현재 OS 용 배포 파일과 그 `.sha256` 을 고릅니다."""
        tag = data.get('tag_name', '')
        suffix = WINDOWS_ASSET_SUFFIX if IS_WINDOWS else MACOS_ASSET_SUFFIX
        package = sha = None
        for asset in data.get('assets', []):
            name = asset.get('name', '')
            if name.endswith(f'{suffix}.sha256'):
                sha = asset
            elif name.endswith(suffix):
                package = asset
        if package is None or sha is None:
            raise UpdateError(f'릴리즈 {tag} 에 {suffix} 또는 .sha256 자산이 없습니다.')
        return cls(
            tag=tag,
            asset_name=package['name'],
            asset_url=package['browser_download_url'],
            sha256_url=sha['browser_download_url'],
        )


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'})


def fetch_json(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_text(url: str, timeout: int) -> str:
    with urllib.request.urlopen(_request(url), timeout=timeout) as resp:
        return resp.read().decode('utf-8')


def download(url: str, dest: str, timeout: int, progress: Callable[[int, int], None] | None = None) -> None:
    with urllib.request.urlopen(_request(url), timeout=timeout) as resp, open(dest, 'wb') as out:
        total = int(resp.headers.get('Content-Length') or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def check_for_update(current_version: str) -> ReleaseInfo | None:
    """최신 릴리즈가 현재 버전보다 새로우면 ReleaseInfo, 아니면 None."""
    data = fetch_json(LATEST_RELEASE_URL, CHECK_TIMEOUT)
    info = ReleaseInfo.from_json(data)
    if not is_newer(info.tag, current_version):
        return None
    return info


def parse_sha256_text(text: str) -> str:
    match = _SHA256_RE.search(text)
    if not match:
        raise UpdateError('.sha256 파일에서 해시를 찾지 못했습니다.')
    return match.group(1).lower()


def verify_sha256(path: str, expected: str) -> None:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected.lower():
        raise UpdateError(f'다운로드한 파일의 SHA-256 이 다릅니다. 예상 {expected[:12]}…, 실제 {actual[:12]}…')


def installed_app_path() -> str | None:
    """업데이트로 교체할 설치 경로를 돌려줍니다. 패키징된 앱이 아니면 None 입니다.

    - macOS: `/Applications` 아래에 설치된 번들로 실행 중일 때만 번들 경로.
    - Windows: 실행 파일이 들어 있는 폴더.
    """
    if not getattr(sys, 'frozen', False):
        return None
    if IS_WINDOWS:
        return str(PureWindowsPath(sys.executable).parent)
    exe = PurePosixPath(sys.executable)
    # <App>.app/Contents/MacOS/<exe>
    app = exe.parent.parent.parent
    if app.suffix != '.app' or app.parent != PurePosixPath('/Applications'):
        return None
    return str(app)


_HELPER_TEMPLATE = '''#!/bin/bash
# INFX Movie Duration Extender 업데이트 헬퍼. 앱이 종료되면 번들을 교체하고 다시 실행합니다.
PID={pid}
DMG={dmg}
APP={app}
MNT="$(mktemp -d)"
while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MNT" "$DMG" || exit 1
rm -rf "${{APP}}.new"
ditto "$MNT/$(basename "$APP")" "$APP.new" || {{ hdiutil detach -quiet "$MNT"; exit 1; }}
hdiutil detach -quiet "$MNT"
rm -rf "$APP"
mv "$APP.new" "$APP"
rm -f "$DMG"
open "$APP"
'''

_WINDOWS_HELPER_TEMPLATE = '''# INFX Movie Duration Extender 업데이트 헬퍼. 앱이 종료되면 폴더를 교체하고 다시 실행합니다.
$ErrorActionPreference = 'Stop'
$Zip = {zip}
$App = {app}
$New = "$App.new"
Wait-Process -Id {pid} -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $New) {{ Remove-Item -LiteralPath $New -Recurse -Force }}
Expand-Archive -LiteralPath $Zip -DestinationPath $New
Remove-Item -LiteralPath $App -Recurse -Force
Move-Item -LiteralPath (Join-Path $New {folder}) -Destination $App
Remove-Item -LiteralPath $New -Recurse -Force
Remove-Item -LiteralPath $Zip -Force
Start-Process -FilePath (Join-Path $App {exe})
'''


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_helper_script(pid: int, asset_path: str, app_path: str, work_dir: str) -> str:
    if IS_WINDOWS:
        script = Path(work_dir) / 'update.ps1'
        text = _WINDOWS_HELPER_TEMPLATE.format(
            pid=pid,
            zip=_ps_quote(asset_path),
            app=_ps_quote(app_path),
            folder=_ps_quote(WINDOWS_ZIP_FOLDER),
            exe=_ps_quote(PureWindowsPath(sys.executable).name),
        )
        # Windows PowerShell 5.1 은 BOM 이 없으면 스크립트를 시스템 코드 페이지로 읽습니다.
        script.write_text(text, encoding='utf-8-sig')
        return str(script)
    script = Path(work_dir) / 'update.sh'
    script.write_text(
        _HELPER_TEMPLATE.format(pid=pid, dmg=_sh_quote(asset_path), app=_sh_quote(app_path)),
        encoding='utf-8',
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def download_and_prepare(info: ReleaseInfo, app_path: str, progress: Callable[[int, int], None] | None = None) -> str:
    """배포 파일을 받아 해시를 확인하고 헬퍼 스크립트 경로를 돌려줍니다. 호출 측이 스크립트를 띄운 뒤 종료해야 합니다."""
    work_dir = tempfile.mkdtemp(prefix='infx_mde_update_')
    asset_path = os.path.join(work_dir, info.asset_name)
    expected = parse_sha256_text(fetch_text(info.sha256_url, CHECK_TIMEOUT))
    download(info.asset_url, asset_path, DOWNLOAD_TIMEOUT, progress)
    verify_sha256(asset_path, expected)
    return write_helper_script(os.getpid(), asset_path, app_path, work_dir)


def launch_helper(script_path: str) -> None:
    if IS_WINDOWS:
        subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script_path],
            cwd=os.path.dirname(script_path),
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    subprocess.Popen(
        ['/bin/bash', script_path],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
