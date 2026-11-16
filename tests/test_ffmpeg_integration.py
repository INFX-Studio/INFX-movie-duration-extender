"""실제 ffmpeg/ffprobe 를 실행하는 통합 테스트. vendor/ffmpeg 바이너리가 없으면 건너뜁니다."""

from __future__ import annotations

import os
import subprocess

import pytest

from mde import core
from mde.ffmpeg_bin import ffmpeg_path, ffprobe_path

pytestmark = pytest.mark.skipif(
    not (os.path.isfile(ffmpeg_path()) and os.path.isfile(ffprobe_path())),
    reason='vendor/ffmpeg 에 ffmpeg, ffprobe 바이너리가 없습니다.',
)


@pytest.fixture
def source_video(tmp_path):
    path = str(tmp_path / 'src.mov')
    subprocess.run(
        [ffmpeg_path(), '-y', '-f', 'lavfi', '-i', 'testsrc=size=321x241:rate=24:duration=1']
        + ['-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le', path],
        check=True,
        capture_output=True,
    )
    return path


@pytest.mark.parametrize('ext', ['.mov', '.mp4'])
def test_extend_video(source_video, tmp_path, ext):
    info = core.probe_video(source_video)
    assert info.frame_count == 24
    output = core.extend_media(info, 3.0, str(tmp_path / f'out{ext}'))
    result = core.probe_video(output)
    assert result.frame_count == 72


def test_extend_video_to_png_sequence(source_video, tmp_path):
    info = core.probe_video(source_video)
    core.extend_media(info, 2.0, str(tmp_path / 'seq' / 'out.%04d.png'))
    names = sorted(p.name for p in (tmp_path / 'seq').iterdir())
    assert len(names) == 48
    assert names[0] == 'out.0001.png'


@pytest.mark.parametrize('ext', ['.mov', '.mp4'])
def test_extend_video_with_resize(source_video, tmp_path, ext):
    info = core.probe_video(source_video)
    assert (info.width, info.height) == (321, 241)
    output = core.extend_media(info, 2.0, str(tmp_path / f'out{ext}'), resize=core.ResizeSpec(160, 90))
    result = core.probe_video(output)
    assert (result.width, result.height, result.frame_count) == (160, 90, 48)


def test_sequence_resize_same_format(source_video, tmp_path):
    info = core.probe_video(source_video)
    core.extend_media(info, 1.0, str(tmp_path / 'seq' / 'out.%04d.png'))
    seq = core.analyze_sequence_file(str(tmp_path / 'seq' / 'out.0001.png'), fps=24.0)
    assert (seq.width, seq.height) == (321, 241)
    core.extend_media(seq, 1.5, str(tmp_path / 'small' / 'out.%04d.png'), resize=core.ResizeSpec(100, 100, 'crop'))
    names = sorted(p.name for p in (tmp_path / 'small').iterdir())
    assert len(names) == 36
    assert core.probe_image_size(str(tmp_path / 'small' / names[-1])) == (100, 100)


def test_extract_preview_frame_from_video(source_video, tmp_path):
    info = core.probe_video(source_video)
    for which in ('first', 'last'):
        frame = core.extract_preview_frame(info, which, str(tmp_path))
        assert core.probe_image_size(frame) == (321, 241)


def test_sequence_to_mov(source_video, tmp_path):
    info = core.probe_video(source_video)
    core.extend_media(info, 1.0, str(tmp_path / 'seq' / 'out.%04d.png'))
    seq = core.analyze_sequence_file(str(tmp_path / 'seq' / 'out.0001.png'), fps=24.0)
    output = core.extend_media(seq, 2.0, str(tmp_path / 'hold.mov'))
    assert core.probe_video(output).frame_count == 48
