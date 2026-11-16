"""리사이즈(사이즈 조절)·미리보기 프레임 관련 core 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mde import core
from mde.core import FIT_CROP, FIT_PAD, FIT_STRETCH, MediaDurationError, MediaInfo, ResizeSpec

PAD_720 = (
    'scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,'
    'pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1'
)


def _video_info(path: str = '/data/shot.mov', fps: float = 24.0, frames: int = 48) -> MediaInfo:
    return MediaInfo(
        path=path,
        media_type='video',
        extension=Path(path).suffix.lower(),
        fps=fps,
        frame_count=frames,
        duration_seconds=frames / fps,
        width=1920,
        height=1080,
    )


def _make_sequence(folder: Path, count: int = 3) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for frame in range(1001, 1001 + count):
        (folder / f'shot.{frame}.png').write_bytes(f'frame{frame}'.encode())


def test_resize_spec_defaults_and_size_text():
    spec = ResizeSpec(1280, 720)
    assert spec.fit == FIT_PAD
    assert spec.size_text == '1280x720'


def test_validate_resize():
    core.validate_resize(None, '.mp4')
    core.validate_resize(ResizeSpec(1281, 721), '.png')
    with pytest.raises(MediaDurationError):
        core.validate_resize(ResizeSpec(0, 720), '.mov')
    with pytest.raises(MediaDurationError):
        core.validate_resize(ResizeSpec(1280, 720, 'zoom'), '.mov')
    with pytest.raises(MediaDurationError):
        core.validate_resize(ResizeSpec(1281, 720), '.mp4')
    with pytest.raises(MediaDurationError):
        core.validate_resize(ResizeSpec(1280, 721), '.mov')


def test_build_resize_filter():
    assert core.build_resize_filter(ResizeSpec(1280, 720)) == PAD_720
    assert core.build_resize_filter(ResizeSpec(1280, 720, FIT_CROP)) == (
        'scale=1280:720:force_original_aspect_ratio=increase:flags=lanczos,crop=1280:720,setsar=1'
    )
    assert core.build_resize_filter(ResizeSpec(1280, 720, FIT_STRETCH)) == 'scale=1280:720:flags=lanczos,setsar=1'


def test_default_output_path_with_resize():
    data = Path('/data')
    info = _video_info(str(data / 'shot.mov'))
    resize = ResizeSpec(1280, 720)
    assert core.default_output_path(info, 10.0, resize=resize) == str(data / 'shot_hold_10s_1280x720.mov')
    # 길이를 늘리지 않고 사이즈만 바꾸면 hold 표기를 뺍니다.
    assert core.default_output_path(info, 2.0, '.mp4', resize) == str(data / 'shot_1280x720.mp4')
    assert core.default_output_path(info, 2.0, '.png', resize) == str(data / 'shot_1280x720' / 'shot.%04d.png')
    assert core.default_output_path(info, 2.0) == str(data / 'shot_hold_2s.mov')


def test_video_command_with_resize_puts_scale_before_tpad():
    cmd = core.build_video_extend_command(
        _video_info(), 10.0, '/data/out.mp4', ffmpeg_bin='ffmpeg', resize=ResizeSpec(1280, 720)
    )
    assert cmd.count('-vf') == 1
    assert cmd[cmd.index('-vf') + 1] == f'{PAD_720},tpad=stop_mode=clone:stop_duration=8.000000'


def test_video_command_rejects_odd_size_for_mp4():
    with pytest.raises(MediaDurationError):
        core.build_video_extend_command(
            _video_info(), 10.0, '/data/out.mp4', ffmpeg_bin='ffmpeg', resize=ResizeSpec(1281, 720)
        )


def test_sequence_convert_command_with_resize(tmp_path):
    _make_sequence(tmp_path)
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1001.png'), fps=24.0)
    cmd = core.build_sequence_convert_command(
        info, 10.0, str(tmp_path / 'out.mp4'), input_pattern='/tmp/hold.%04d.png', ffmpeg_bin='ffmpeg',
        resize=ResizeSpec(1280, 720),
    )
    assert cmd.count('-vf') == 1
    assert cmd[cmd.index('-vf') + 1] == PAD_720


def test_extend_media_sequence_same_format_with_resize_uses_ffmpeg(monkeypatch, tmp_path):
    _make_sequence(tmp_path)
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1001.png'), fps=24.0)
    calls = []
    monkeypatch.setattr(core, 'extend_sequence', lambda *a, **k: calls.append('copy'))
    monkeypatch.setattr(core, 'convert_extended_sequence', lambda *a, **k: calls.append(('convert', k['resize'])))
    out = str(tmp_path / 'out' / 'shot.%04d.png')
    core.extend_media(info, 1.0, out)
    core.extend_media(info, 1.0, out, resize=ResizeSpec(640, 360))
    assert calls == ['copy', ('convert', ResizeSpec(640, 360))]


def _fake_run(stdout: str, created: list[str] | None = None):
    def run(cmd, **kwargs):
        if created is not None:
            Path(cmd[-1]).write_bytes(b'png')
            created.append(cmd)
        return core.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr='')

    return run


def test_probe_video_reads_size(monkeypatch):
    stream = {'r_frame_rate': '24/1', 'duration': '2.0', 'width': 1920, 'height': 1080}
    monkeypatch.setattr(core.subprocess, 'run', _fake_run(json.dumps({'streams': [stream]})))
    info = core.probe_video('/data/a.mov', ffprobe_bin='ffprobe')
    assert (info.width, info.height) == (1920, 1080)


def test_probe_image_size(monkeypatch):
    monkeypatch.setattr(core.subprocess, 'run', _fake_run(json.dumps({'streams': [{'width': 64, 'height': 32}]})))
    assert core.probe_image_size('/data/a.png', ffprobe_bin='ffprobe') == (64, 32)


def test_probe_image_size_failure_returns_zero(tmp_path):
    assert core.probe_image_size(str(tmp_path / 'a.png'), ffprobe_bin=str(tmp_path / 'no_ffprobe')) == (0, 0)


def test_extract_preview_frame_sequence(tmp_path):
    _make_sequence(tmp_path)
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1002.png'))
    assert core.extract_preview_frame(info, 'first', str(tmp_path)) == str(tmp_path / 'shot.1001.png')
    assert core.extract_preview_frame(info, 'last', str(tmp_path)) == str(tmp_path / 'shot.1003.png')


def test_extract_preview_frame_video(monkeypatch, tmp_path):
    created = []
    monkeypatch.setattr(core.subprocess, 'run', _fake_run('', created))
    info = _video_info(str(tmp_path / 'a.mov'))
    first = core.extract_preview_frame(info, 'first', str(tmp_path), ffmpeg_bin='ffmpeg')
    last = core.extract_preview_frame(info, 'last', str(tmp_path), ffmpeg_bin='ffmpeg')
    assert Path(first).is_file() and Path(last).is_file() and first != last
    assert created[0][-3:-1] == ['-frames:v', '1']
    assert created[1][created[1].index('-ss') + 1] == '1.000000'
