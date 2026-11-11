"""내장 ffmpeg / ffprobe 경로 해석 테스트."""

from __future__ import annotations

from pathlib import Path

from mde import ffmpeg_bin


def test_binary_names_on_windows(monkeypatch):
    monkeypatch.setattr(ffmpeg_bin, 'EXE_SUFFIX', '.exe')
    assert Path(ffmpeg_bin.ffmpeg_path()).name == 'ffmpeg.exe'
    assert Path(ffmpeg_bin.ffprobe_path()).name == 'ffprobe.exe'


def test_binary_names_on_macos(monkeypatch):
    monkeypatch.setattr(ffmpeg_bin, 'EXE_SUFFIX', '')
    assert Path(ffmpeg_bin.ffmpeg_path()).name == 'ffmpeg'
    assert Path(ffmpeg_bin.ffprobe_path()).name == 'ffprobe'
