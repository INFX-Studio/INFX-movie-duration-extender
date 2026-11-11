"""GitHub Release 기반 자동 업데이트 로직 테스트 (네트워크 호출 없음)."""

from __future__ import annotations

import codecs
import hashlib
import json
from pathlib import Path

import pytest

from mde import updater

DMG = 'INFX-Movie-Duration-Extender-1.2.0-arm64.dmg'
ZIP = 'INFX-Movie-Duration-Extender-1.2.0-win64.zip'
RELEASE = {
    'tag_name': 'v1.2.0',
    'assets': [
        {'name': DMG, 'browser_download_url': 'https://x/app.dmg'},
        {'name': f'{DMG}.sha256', 'browser_download_url': 'https://x/app.dmg.sha256'},
        {'name': ZIP, 'browser_download_url': 'https://x/app.zip'},
        {'name': f'{ZIP}.sha256', 'browser_download_url': 'https://x/app.zip.sha256'},
    ],
}


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(updater, 'IS_WINDOWS', False)


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(updater, 'IS_WINDOWS', True)


def test_parse_version():
    assert updater.parse_version('v1.2.3') == (1, 2, 3)
    assert updater.parse_version('1.10.0') == (1, 10, 0)
    with pytest.raises(ValueError):
        updater.parse_version('latest')


def test_is_newer():
    assert updater.is_newer('v1.2.0', '1.1.9')
    assert not updater.is_newer('v1.2.0', '1.2.0')
    assert not updater.is_newer('v1.2.0', '1.10.0')


def test_release_info_from_json_macos(mac):
    info = updater.ReleaseInfo.from_json(RELEASE)
    assert info.tag == 'v1.2.0'
    assert info.asset_name == DMG
    assert info.asset_url == 'https://x/app.dmg'
    assert info.sha256_url == 'https://x/app.dmg.sha256'


def test_release_info_from_json_windows(win):
    info = updater.ReleaseInfo.from_json(RELEASE)
    assert info.asset_name == ZIP
    assert info.asset_url == 'https://x/app.zip'
    assert info.sha256_url == 'https://x/app.zip.sha256'


def test_release_info_requires_both_assets(mac):
    with pytest.raises(updater.UpdateError):
        updater.ReleaseInfo.from_json({'tag_name': 'v1.0.0', 'assets': RELEASE['assets'][:1]})


def test_release_info_ignores_other_platform_assets(win):
    with pytest.raises(updater.UpdateError):
        updater.ReleaseInfo.from_json({'tag_name': 'v1.0.0', 'assets': RELEASE['assets'][:2]})


def test_parse_sha256_file_accepts_shasum_format(tmp_path):
    digest = 'a' * 64
    assert updater.parse_sha256_text(f'{digest}  INFX.dmg\n') == digest
    assert updater.parse_sha256_text(f'{digest.upper()}\n') == digest
    with pytest.raises(updater.UpdateError):
        updater.parse_sha256_text('not a hash')


def test_verify_sha256(tmp_path):
    f = tmp_path / 'a.dmg'
    f.write_bytes(b'hello')
    good = hashlib.sha256(b'hello').hexdigest()
    updater.verify_sha256(str(f), good)
    with pytest.raises(updater.UpdateError):
        updater.verify_sha256(str(f), '0' * 64)


def test_installed_app_path_only_when_frozen_in_applications(mac, monkeypatch):
    monkeypatch.setattr(updater.sys, 'frozen', False, raising=False)
    assert updater.installed_app_path() is None

    monkeypatch.setattr(updater.sys, 'frozen', True, raising=False)
    exe = '/Applications/INFX Movie Duration Extender.app/Contents/MacOS/INFX Movie Duration Extender'
    monkeypatch.setattr(updater.sys, 'executable', exe)
    assert updater.installed_app_path() == '/Applications/INFX Movie Duration Extender.app'

    monkeypatch.setattr(updater.sys, 'executable', '/Users/me/Downloads/X.app/Contents/MacOS/X')
    assert updater.installed_app_path() is None


def test_installed_app_path_windows_is_exe_folder_when_frozen(win, monkeypatch):
    monkeypatch.setattr(updater.sys, 'frozen', False, raising=False)
    assert updater.installed_app_path() is None

    monkeypatch.setattr(updater.sys, 'frozen', True, raising=False)
    exe = r'D:\Tools\INFX Movie Duration Extender\INFX Movie Duration Extender.exe'
    monkeypatch.setattr(updater.sys, 'executable', exe)
    assert updater.installed_app_path() == r'D:\Tools\INFX Movie Duration Extender'


def test_helper_script_swaps_bundle_then_relaunches(mac, tmp_path):
    script = updater.write_helper_script(
        pid=1234,
        asset_path=str(tmp_path / 'new.dmg'),
        app_path='/Applications/INFX Movie Duration Extender.app',
        work_dir=str(tmp_path),
    )
    text = Path(script).read_text(encoding='utf-8')
    assert text.startswith('#!/bin/bash')
    assert 'PID=1234' in text and 'kill -0 "$PID"' in text
    assert 'hdiutil attach' in text and 'hdiutil detach' in text
    # 새 번들을 임시 이름으로 복사한 뒤 교체해야 실패 시 기존 앱이 남습니다.
    assert text.index('ditto') < text.index('rm -rf "$APP"') < text.index('mv "$APP.new" "$APP"')
    assert text.rstrip().endswith('open "$APP"')


def test_windows_helper_script_swaps_folder_then_relaunches(win, monkeypatch, tmp_path):
    monkeypatch.setattr(updater.sys, 'executable', r"D:\It's\App\INFX Movie Duration Extender.exe")
    script = updater.write_helper_script(
        pid=1234,
        asset_path=r'C:\Temp\update.zip',
        app_path=r"D:\It's\App",
        work_dir=str(tmp_path),
    )
    assert script.endswith('.ps1')
    raw = Path(script).read_bytes()
    # Windows PowerShell 5.1 은 BOM 이 있어야 한글 경로를 UTF-8 로 읽습니다.
    assert raw.startswith(codecs.BOM_UTF8)
    text = raw.decode('utf-8-sig')
    assert 'Wait-Process -Id 1234' in text
    assert r"$App = 'D:\It''s\App'" in text
    assert r"$Zip = 'C:\Temp\update.zip'" in text
    # 새 폴더를 먼저 풀어 둔 뒤 교체해야 압축 해제 실패 시 기존 앱이 남습니다.
    assert text.index('Expand-Archive') < text.index('Remove-Item -LiteralPath $App') < text.index('Move-Item')
    assert text.rstrip().endswith("Start-Process -FilePath (Join-Path $App 'INFX Movie Duration Extender.exe')")


def test_check_for_update_uses_fetch_json(monkeypatch):
    monkeypatch.setattr(updater, 'fetch_json', lambda url, timeout: json.loads(json.dumps(RELEASE)))
    assert updater.check_for_update('1.0.0').tag == 'v1.2.0'
    assert updater.check_for_update('1.2.0') is None
