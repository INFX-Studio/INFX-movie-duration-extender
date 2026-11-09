"""ffmpeg 명령 조립 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from mde import core, ffmpeg_cmd
from mde.core import MediaInfo

PRORES_HQ = [
    '-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le', '-vendor', 'apl0', '-bits_per_mb', '8000',
]
H264_DELIVERY = ['-c:v', 'libx264', '-profile:v', 'high', '-preset', 'veryslow', '-crf', '17', '-pix_fmt', 'yuv420p']


def _video_info() -> MediaInfo:
    return MediaInfo(
        path='/data/shot.mov', media_type='video', extension='.mov', fps=24.0, frame_count=48, duration_seconds=2.0
    )


def test_output_encode_args():
    assert ffmpeg_cmd.output_encode_args('.mov') == PRORES_HQ
    assert ffmpeg_cmd.output_encode_args('.mp4') == H264_DELIVERY + ['-movflags', '+faststart']
    assert ffmpeg_cmd.output_encode_args('.png') == [
        '-c:v', 'png', '-pix_fmt', 'rgb24', '-compression_level', '2', '-f', 'image2',
    ]
    assert ffmpeg_cmd.output_encode_args('.jpg') == ['-c:v', 'mjpeg', '-pix_fmt', 'yuvj420p', '-q:v', '1', '-f', 'image2']
    with pytest.raises(ValueError):
        ffmpeg_cmd.output_encode_args('.exr')


def test_video_to_mov_command():
    cmd = core.build_video_extend_command(_video_info(), 10.0, '/data/out.mov', ffmpeg_bin='ffmpeg')
    assert cmd == (
        ['ffmpeg', '-y', '-hide_banner', '-i', '/data/shot.mov', '-an']
        + ['-vf', 'tpad=stop_mode=clone:stop_duration=8.000000', '-t', '10.000000']
        + PRORES_HQ
        + ['/data/out.mov']
    )


def test_video_to_mp4_command_keeps_even_scale_with_tpad():
    cmd = core.build_video_extend_command(_video_info(), 10.0, '/data/out.mp4', ffmpeg_bin='ffmpeg')
    assert cmd.count('-vf') == 1
    vf = cmd[cmd.index('-vf') + 1]
    assert vf == 'tpad=stop_mode=clone:stop_duration=8.000000,scale=trunc(iw/2)*2:trunc(ih/2)*2'
    assert cmd[-3:] == ['-movflags', '+faststart', '/data/out.mp4']


def test_video_to_png_sequence_starts_at_one():
    cmd = core.build_video_extend_command(_video_info(), 10.0, '/data/out/shot.%04d.png', ffmpeg_bin='ffmpeg')
    assert cmd[-3:] == ['-start_number', '1', '/data/out/shot.%04d.png']
    assert 'scale=' not in cmd[cmd.index('-vf') + 1]


def test_sequence_convert_command(tmp_path):
    for frame in range(1001, 1004):
        (tmp_path / f'shot.{frame}.png').write_bytes(b'x')
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1001.png'), fps=24.0)
    out = str(tmp_path / 'out.mp4')
    cmd = core.build_sequence_convert_command(info, 10.0, out, input_pattern='/tmp/hold.%04d.png', ffmpeg_bin='ffmpeg')
    assert cmd == (
        ['ffmpeg', '-y', '-hide_banner', '-start_number', '1001', '-framerate', '24.000000']
        + ['-i', '/tmp/hold.%04d.png', '-an', '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']
        + H264_DELIVERY
        + ['-movflags', '+faststart', '-r', '24.000000', out]
    )


def test_resolve_binaries_frozen(monkeypatch, tmp_path):
    from mde import ffmpeg_bin

    monkeypatch.setattr(ffmpeg_bin.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(ffmpeg_bin.sys, '_MEIPASS', str(tmp_path), raising=False)
    assert ffmpeg_bin.ffmpeg_path() == str(Path(tmp_path) / 'ffmpeg' / 'ffmpeg')
    assert ffmpeg_bin.ffprobe_path() == str(Path(tmp_path) / 'ffmpeg' / 'ffprobe')
