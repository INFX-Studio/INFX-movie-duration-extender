"""GitHub Release 기반 자동 업데이트 로직 테스트 (네트워크 호출 없음)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mde import updater

RELEASE = {
    'tag_name': 'v1.2.0',
    'assets': [
        {'name': 'INFX-Movie-Duration-Extender-1.2.0-arm64.dmg', 'browser_download_url': 'https://x/app.dmg'},
        {'name': 'INFX-Movie-Duration-Extender-1.2.0-arm64.dmg.sha256', 'browser_download_url': 'https://x/app.sha256'},
    ],
}


def test_parse_version():
    assert updater.parse_version('v1.2.3') == (1, 2, 3)
    assert updater.parse_version('1.10.0') == (1, 10, 0)
    with pytest.raises(ValueError):
        updater.parse_version('latest')


def test_is_newer():
    assert updater.is_newer('v1.2.0', '1.1.9')
    assert not updater.is_newer('v1.2.0', '1.2.0')
    assert not updater.is_newer('v1.2.0', '1.10.0')


def test_release_info_from_json():
    info = updater.ReleaseInfo.from_json(RELEASE)
    assert info.tag == 'v1.2.0'
    assert info.dmg_url == 'https://x/app.dmg'
    assert info.sha256_url == 'https://x/app.sha256'
    assert info.dmg_name == 'INFX-Movie-Duration-Extender-1.2.0-arm64.dmg'


def test_release_info_requires_both_assets():
    with pytest.raises(updater.UpdateError):
        updater.ReleaseInfo.from_json({'tag_name': 'v1.0.0', 'assets': RELEASE['assets'][:1]})


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


def test_installed_app_path_only_when_frozen_in_applications(monkeypatch):
    monkeypatch.setattr(updater.sys, 'frozen', False, raising=False)
    assert updater.installed_app_path() is None

    monkeypatch.setattr(updater.sys, 'frozen', True, raising=False)
    exe = '/Applications/INFX Movie Duration Extender.app/Contents/MacOS/INFX Movie Duration Extender'
    monkeypatch.setattr(updater.sys, 'executable', exe)
    assert updater.installed_app_path() == '/Applications/INFX Movie Duration Extender.app'

    monkeypatch.setattr(updater.sys, 'executable', '/Users/me/Downloads/X.app/Contents/MacOS/X')
    assert updater.installed_app_path() is None


def test_helper_script_swaps_bundle_then_relaunches(tmp_path):
    script = updater.write_helper_script(
        pid=1234,
        dmg_path=str(tmp_path / 'new.dmg'),
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


def test_check_for_update_uses_fetch_json(monkeypatch):
    monkeypatch.setattr(updater, 'fetch_json', lambda url, timeout: json.loads(json.dumps(RELEASE)))
    assert updater.check_for_update('1.0.0').tag == 'v1.2.0'
    assert updater.check_for_update('1.2.0') is None
