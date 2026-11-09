"""core 모듈(길이 계산, 시퀀스 탐색, 출력 경로, 이미지 복제) 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from mde import core
from mde.core import MediaDurationError, MediaInfo


def _make_sequence(folder: Path, name: str = 'shot', start: int = 1001, count: int = 5, ext: str = '.png') -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for frame in range(start, start + count):
        (folder / f'{name}.{frame:04d}{ext}').write_bytes(f'frame{frame}'.encode())


def _video_info(path: str = '/tmp/shot_v001.mov', fps: float = 24.0, frames: int = 48) -> MediaInfo:
    return MediaInfo(
        path=path,
        media_type='video',
        extension=Path(path).suffix.lower(),
        fps=fps,
        frame_count=frames,
        duration_seconds=frames / fps,
    )


def test_target_frame_count_rounds_up():
    assert core.target_frame_count(10.0, 24.0) == 240
    assert core.target_frame_count(1.01, 24.0) == 25


@pytest.mark.parametrize('seconds, fps', [(0, 24.0), (-1, 24.0), (10, 0)])
def test_target_frame_count_rejects_invalid(seconds, fps):
    with pytest.raises(MediaDurationError):
        core.target_frame_count(seconds, fps)


def test_validate_target_rejects_shorter_than_source():
    with pytest.raises(MediaDurationError):
        core.validate_target(_video_info(frames=48), 1.0)
    assert core.validate_target(_video_info(frames=48), 2.0) == 48


def test_normalize_output_extension():
    assert core.normalize_output_extension('MOV') == '.mov'
    assert core.normalize_output_extension('.jpeg') == '.jpg'
    with pytest.raises(MediaDurationError):
        core.normalize_output_extension('.exr')


def test_default_output_path_video():
    info = _video_info('/data/shot_v001.mov')
    assert core.default_output_path(info, 10.0) == '/data/shot_v001_hold_10s.mov'
    assert core.default_output_path(info, 2.5, '.mp4') == '/data/shot_v001_hold_2p5s.mp4'
    assert core.default_output_path(info, 10.0, '.png') == '/data/shot_v001_hold_10s/shot_v001.%04d.png'


def test_default_output_path_sequence(tmp_path):
    _make_sequence(tmp_path)
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1001.png'))
    assert core.default_output_path(info, 10.0) == str(tmp_path / 'shot_hold_10s' / 'shot.%04d.png')
    assert core.default_output_path(info, 10.0, '.mov') == str(tmp_path / 'shot_hold_10s.mov')


def test_analyze_sequence_file(tmp_path):
    _make_sequence(tmp_path, count=5)
    (tmp_path / 'other.1001.png').write_bytes(b'x')
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1003.png'), fps=24.0)
    assert info.media_type == 'sequence'
    assert (info.start_frame, info.end_frame, info.frame_count, info.padding) == (1001, 1005, 5, 4)
    assert info.sequence_pattern == str(tmp_path / 'shot.%04d.png')
    assert info.last_frame_path == str(tmp_path / 'shot.1005.png')
    assert info.duration_seconds == pytest.approx(5 / 24.0)
    assert info.display_type == 'PNG SEQ'


def test_discover_inputs_folder_and_dedup(tmp_path):
    _make_sequence(tmp_path / 'a', name='shot')
    (tmp_path / 'a' / 'still.jpg').write_bytes(b'x')
    infos = core.discover_inputs([str(tmp_path), str(tmp_path / 'a' / 'shot.1001.png')])
    keys = sorted(info.sequence_pattern for info in infos)
    assert keys == [str(tmp_path / 'a' / 'shot.%04d.png'), str(tmp_path / 'a' / 'still.%04d.jpg')]


def test_extend_sequence_copies_and_holds_last_frame(tmp_path):
    _make_sequence(tmp_path, count=5)
    info = core.analyze_sequence_file(str(tmp_path / 'shot.1001.png'), fps=5.0)
    out_pattern = str(tmp_path / 'out' / 'shot.%04d.png')
    first = core.extend_sequence(info, 2.0, out_pattern)
    assert first == out_pattern % 1001
    outputs = sorted((tmp_path / 'out').iterdir())
    assert len(outputs) == 10
    assert (tmp_path / 'out' / 'shot.1002.png').read_bytes() == b'frame1002'
    assert (tmp_path / 'out' / 'shot.1010.png').read_bytes() == b'frame1005'


def test_extend_single_image(tmp_path):
    still = tmp_path / 'still.png'
    still.write_bytes(b'still')
    info = core.analyze_sequence_file(str(still), fps=4.0)
    out_pattern = str(tmp_path / 'out' / 'still.%04d.png')
    core.extend_sequence(info, 1.0, out_pattern)
    assert sorted(p.name for p in (tmp_path / 'out').iterdir()) == [f'still.{i:04d}.png' for i in range(1, 5)]


def test_output_path_pattern_validation(tmp_path):
    info = _video_info()
    with pytest.raises(MediaDurationError):
        core.build_video_extend_command(info, 10.0, '/data/out.png', ffmpeg_bin='ffmpeg')
    with pytest.raises(MediaDurationError):
        core.build_video_extend_command(info, 10.0, '/data/out.%04d.mov', ffmpeg_bin='ffmpeg')


def test_probe_video_missing_binary(tmp_path):
    with pytest.raises(MediaDurationError):
        core.probe_video(str(tmp_path / 'a.mov'), ffprobe_bin=str(tmp_path / 'no_ffprobe'))
