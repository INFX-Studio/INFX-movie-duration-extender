"""GitHub Release 기반 자동 업데이트.

최신 릴리즈의 DMG 와 `.sha256` 파일을 내려받아 해시를 확인한 뒤,
별도 셸 스크립트가 앱 종료를 기다렸다가 `/Applications` 의 번들을 교체하고 다시 실행합니다.
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
from pathlib import Path

log = logging.getLogger(__name__)

REPO = 'INFX-Studio/INFX-mov-duration-extender'
LATEST_RELEASE_URL = f'https://api.github.com/repos/{REPO}/releases/latest'
USER_AGENT = 'INFX-Movie-Duration-Extender-Updater'
CHECK_TIMEOUT = 5
DOWNLOAD_TIMEOUT = 60

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
    dmg_name: str
    dmg_url: str
    sha256_url: str

    @classmethod
    def from_json(cls, data: dict) -> ReleaseInfo:
        tag = data.get('tag_name', '')
        dmg = sha = None
        for asset in data.get('assets', []):
            name = asset.get('name', '')
            if name.endswith('.dmg.sha256'):
                sha = asset
            elif name.endswith('.dmg'):
                dmg = asset
        if dmg is None or sha is None:
            raise UpdateError(f'릴리즈 {tag} 에 DMG 또는 .sha256 자산이 없습니다.')
        return cls(
            tag=tag,
            dmg_name=dmg['name'],
            dmg_url=dmg['browser_download_url'],
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
    """`/Applications` 아래에 설치된 번들로 실행 중일 때만 앱 경로를 돌려줍니다."""
    if not getattr(sys, 'frozen', False):
        return None
    exe = Path(sys.executable)
    # <App>.app/Contents/MacOS/<exe>
    app = exe.parent.parent.parent
    if app.suffix != '.app' or app.parent != Path('/Applications'):
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


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def write_helper_script(pid: int, dmg_path: str, app_path: str, work_dir: str) -> str:
    script = Path(work_dir) / 'update.sh'
    script.write_text(
        _HELPER_TEMPLATE.format(pid=pid, dmg=_sh_quote(dmg_path), app=_sh_quote(app_path)),
        encoding='utf-8',
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def download_and_prepare(info: ReleaseInfo, app_path: str, progress: Callable[[int, int], None] | None = None) -> str:
    """DMG 를 받아 해시를 확인하고 헬퍼 스크립트 경로를 돌려줍니다. 호출 측이 스크립트를 띄운 뒤 종료해야 합니다."""
    work_dir = tempfile.mkdtemp(prefix='infx_mde_update_')
    dmg_path = os.path.join(work_dir, info.dmg_name)
    expected = parse_sha256_text(fetch_text(info.sha256_url, CHECK_TIMEOUT))
    download(info.dmg_url, dmg_path, DOWNLOAD_TIMEOUT, progress)
    verify_sha256(dmg_path, expected)
    return write_helper_script(os.getpid(), dmg_path, app_path, work_dir)


def launch_helper(script_path: str) -> None:
    subprocess.Popen(
        ['/bin/bash', script_path],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
