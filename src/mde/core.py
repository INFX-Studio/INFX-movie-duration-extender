"""영상/이미지 시퀀스 길이 연장 로직."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_bin import ffmpeg_path, ffprobe_path
from .ffmpeg_cmd import EVEN_SCALE_FILTER, needs_even_scale, output_encode_args

VIDEO_EXTENSIONS = {".mov", ".mp4"}
SEQUENCE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | SEQUENCE_EXTENSIONS
OUTPUT_VIDEO_EXTENSIONS = {".mov", ".mp4"}
OUTPUT_SEQUENCE_EXTENSIONS = {".jpg", ".png"}
OUTPUT_EXTENSIONS = OUTPUT_VIDEO_EXTENSIONS | OUTPUT_SEQUENCE_EXTENSIONS
DEFAULT_SEQUENCE_FPS = 24.0
SEQUENCE_RE = re.compile(r"^(.*?)([._])(\d+)(\.[^.]+)$", re.IGNORECASE)
# Windows 의 창 없는 앱에서 ffmpeg 를 실행할 때 콘솔 창이 뜨지 않게 합니다.
_SUBPROCESS_OPTIONS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


class MediaDurationError(Exception):
    """길이 연장 처리 중 사용자에게 보여줄 수 있는 오류."""


@dataclass(frozen=True)
class MediaInfo:
    path: str
    media_type: str
    extension: str
    fps: float
    frame_count: int
    duration_seconds: float
    start_frame: int | None = None
    end_frame: int | None = None
    padding: int | None = None
    sequence_pattern: str | None = None
    last_frame_path: str | None = None

    @property
    def display_type(self) -> str:
        if self.media_type == "video":
            return self.extension.lstrip(".").upper()
        return f"{self.extension.lstrip('.').upper()} SEQ"


def is_supported_path(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def normalize_output_extension(extension: str | None) -> str:
    ext = (extension or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in OUTPUT_EXTENSIONS:
        raise MediaDurationError(f"지원하지 않는 출력 포맷입니다: {extension}")
    return ext


def target_frame_count(target_seconds: float, fps: float) -> int:
    if target_seconds <= 0:
        raise MediaDurationError("목표 길이는 0초보다 커야 합니다.")
    if fps <= 0:
        raise MediaDurationError("FPS는 0보다 커야 합니다.")
    return math.ceil((target_seconds * fps) - 0.000001)


def validate_target(info: MediaInfo, target_seconds: float) -> int:
    frames = target_frame_count(target_seconds, info.fps)
    if frames < info.frame_count:
        raise MediaDurationError(
            f"목표 길이가 원본보다 짧습니다. "
            f"원본 {info.duration_seconds:.3f}초, 목표 {target_seconds:.3f}초"
        )
    return frames


def default_output_path(
    info: MediaInfo,
    target_seconds: float,
    output_extension: str | None = None,
) -> str:
    output_ext = normalize_output_extension(output_extension or info.extension)
    suffix = _format_seconds_for_name(target_seconds)
    source = Path(info.path)
    stem = source.stem
    parent = source.parent

    if info.media_type == "sequence":
        pattern = Path(info.sequence_pattern or info.path)
        stem = _sequence_stem(pattern)
        parent = pattern.parent

    if output_ext in OUTPUT_VIDEO_EXTENSIONS:
        return str(parent / f"{stem}_hold_{suffix}s{output_ext}")

    padding = info.padding or 4
    out_dir = parent / f"{stem}_hold_{suffix}s"
    return str(out_dir / f"{stem}.%0{padding}d{output_ext}")


def discover_inputs(paths: Iterable[str], sequence_fps: float = DEFAULT_SEQUENCE_FPS) -> list[MediaInfo]:
    infos: list[MediaInfo] = []
    seen: set[str] = set()
    video_paths: list[str] = []

    for raw_path in paths:
        path = str(Path(raw_path))
        if os.path.isdir(path):
            for info in discover_folder(path, sequence_fps=sequence_fps):
                key = info.sequence_pattern or info.path
                if key not in seen:
                    infos.append(info)
                    seen.add(key)
            continue

        if not os.path.isfile(path):
            continue

        ext = Path(path).suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            video_paths.append(path)
        elif ext in SEQUENCE_EXTENSIONS:
            info = analyze_sequence_file(path, fps=sequence_fps)
            key = info.sequence_pattern or info.path
            if key not in seen:
                infos.append(info)
                seen.add(key)

    if video_paths:
        with ThreadPoolExecutor(max_workers=min(8, len(video_paths))) as executor:
            futures = {executor.submit(probe_video, p): p for p in video_paths}
            for future in as_completed(futures):
                try:
                    info = future.result()
                    key = info.sequence_pattern or info.path
                    if key not in seen:
                        infos.append(info)
                        seen.add(key)
                except MediaDurationError:
                    pass

    return infos


def discover_folder(folder_path: str, sequence_fps: float = DEFAULT_SEQUENCE_FPS) -> list[MediaInfo]:
    infos: list[MediaInfo] = []
    for root, _, files in os.walk(folder_path):
        sequence_groups: dict[tuple[str, str, int, str], list[int]] = {}
        sequence_names: set[str] = set()
        video_paths: list[str] = []

        for name in files:
            ext = Path(name).suffix.lower()
            full_path = str(Path(root) / name)

            if ext in VIDEO_EXTENSIONS:
                video_paths.append(full_path)
                continue

            match = SEQUENCE_RE.match(name)
            if match and ext in SEQUENCE_EXTENSIONS:
                prefix, sep, frame_text, seq_ext = match.groups()
                sequence_groups.setdefault((prefix, sep, len(frame_text), seq_ext.lower()), []).append(
                    int(frame_text)
                )
                sequence_names.add(name)

        if video_paths:
            with ThreadPoolExecutor(max_workers=min(8, len(video_paths))) as executor:
                futures = {executor.submit(probe_video, p): p for p in video_paths}
                for future in as_completed(futures):
                    try:
                        infos.append(future.result())
                    except MediaDurationError:
                        pass

        for (prefix, sep, padding, ext), frames in sequence_groups.items():
            infos.append(_build_sequence_info(root, prefix, sep, padding, ext, frames, sequence_fps))

        for name in files:
            if name in sequence_names:
                continue
            ext = Path(name).suffix.lower()
            if ext in SEQUENCE_EXTENSIONS:
                infos.append(_build_single_image_info(str(Path(root) / name), sequence_fps))

    return infos


def probe_video(path: str, ffprobe_bin: str | None = None) -> MediaInfo:
    ffprobe_bin = ffprobe_bin or ffprobe_path()
    cmd = [
        str(ffprobe_bin),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_frames,r_frame_rate,avg_frame_rate,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=True, **_SUBPROCESS_OPTIONS)
    except FileNotFoundError as exc:
        raise MediaDurationError(f"ffprobe 실행 파일을 찾을 수 없습니다: {ffprobe_bin}") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaDurationError(f"ffprobe 분석 실패: {exc.stderr}") from exc

    try:
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
    except Exception as exc:
        raise MediaDurationError("ffprobe 결과를 읽을 수 없습니다.") from exc

    fps = _parse_rate(stream.get("r_frame_rate")) or _parse_rate(stream.get("avg_frame_rate"))
    if fps <= 0:
        raise MediaDurationError("영상 FPS를 확인할 수 없습니다.")

    duration = _parse_float(stream.get("duration")) or _parse_float((data.get("format") or {}).get("duration"))
    if duration > 0:
        frame_count = round(duration * fps)
    else:
        frame_count = _parse_int(stream.get("nb_frames"))
        if frame_count > 0:
            duration = frame_count / fps
    if frame_count <= 0:
        raise MediaDurationError("영상 프레임 수를 확인할 수 없습니다.")

    return MediaInfo(
        path=path,
        media_type="video",
        extension=Path(path).suffix.lower(),
        fps=fps,
        frame_count=frame_count,
        duration_seconds=duration,
    )


def analyze_sequence_file(path: str, fps: float = DEFAULT_SEQUENCE_FPS) -> MediaInfo:
    p = Path(path)
    match = SEQUENCE_RE.match(p.name)
    if not match:
        return _build_single_image_info(path, fps)

    prefix, sep, frame_text, ext = match.groups()
    frames = []
    for candidate in p.parent.iterdir():
        if not candidate.is_file():
            continue
        candidate_match = SEQUENCE_RE.match(candidate.name)
        if not candidate_match:
            continue
        c_prefix, c_sep, c_frame_text, c_ext = candidate_match.groups()
        if (
            c_prefix == prefix
            and c_sep == sep
            and len(c_frame_text) == len(frame_text)
            and c_ext.lower() == ext.lower()
        ):
            frames.append(int(c_frame_text))

    if not frames:
        frames = [int(frame_text)]
    return _build_sequence_info(str(p.parent), prefix, sep, len(frame_text), ext.lower(), frames, fps)


def build_video_extend_command(
    info: MediaInfo,
    target_seconds: float,
    output_path: str | None = None,
    ffmpeg_bin: str | None = None,
) -> list[str]:
    if info.media_type != "video":
        raise MediaDurationError("영상 입력만 ffmpeg 명령을 만들 수 있습니다.")

    validate_target(info, target_seconds)
    output = output_path or default_output_path(info, target_seconds)
    output_ext = normalize_output_extension(Path(output).suffix)
    _validate_output_path(output, output_ext)
    hold_seconds = max(0.0, target_seconds - info.duration_seconds)
    filters = [f"tpad=stop_mode=clone:stop_duration={hold_seconds:.6f}"]
    if needs_even_scale(output_ext):
        filters.append(EVEN_SCALE_FILTER)
    cmd = [
        str(ffmpeg_bin or ffmpeg_path()),
        "-y",
        "-hide_banner",
        "-i",
        info.path,
        "-an",
        "-vf",
        ",".join(filters),
        "-t",
        f"{target_seconds:.6f}",
    ]
    cmd.extend(output_encode_args(output_ext))
    if output_ext in OUTPUT_SEQUENCE_EXTENSIONS:
        cmd.extend(["-start_number", "1"])
    cmd.append(output)
    return cmd


def extend_video(
    info: MediaInfo,
    target_seconds: float,
    output_path: str | None = None,
    ffmpeg_bin: str | None = None,
) -> str:
    output = output_path or default_output_path(info, target_seconds)
    _run_ffmpeg(build_video_extend_command(info, target_seconds, output, ffmpeg_bin=ffmpeg_bin), output)
    return output


def build_sequence_convert_command(
    info: MediaInfo,
    target_seconds: float,
    output_path: str,
    input_pattern: str | None = None,
    ffmpeg_bin: str | None = None,
) -> list[str]:
    if info.media_type != "sequence":
        raise MediaDurationError("시퀀스 입력만 변환 명령을 만들 수 있습니다.")
    if info.start_frame is None:
        raise MediaDurationError("시퀀스 시작 프레임을 확인할 수 없습니다.")

    validate_target(info, target_seconds)
    output_ext = normalize_output_extension(Path(output_path).suffix)
    _validate_output_path(output_path, output_ext)
    pattern = input_pattern or info.sequence_pattern
    if not pattern:
        raise MediaDurationError("시퀀스 입력 패턴을 확인할 수 없습니다.")

    framerate = f"{info.fps:.6f}"
    cmd = [
        str(ffmpeg_bin or ffmpeg_path()),
        "-y",
        "-hide_banner",
        "-start_number",
        str(info.start_frame),
        "-framerate",
        framerate,
        "-i",
        pattern,
        "-an",
    ]
    if needs_even_scale(output_ext):
        cmd.extend(["-vf", EVEN_SCALE_FILTER])
    cmd.extend(output_encode_args(output_ext))
    cmd.extend(["-r", framerate])
    if output_ext in OUTPUT_SEQUENCE_EXTENSIONS:
        cmd.extend(["-start_number", str(info.start_frame)])
    cmd.append(output_path)
    return cmd


def extend_media(
    info: MediaInfo,
    target_seconds: float,
    output_path: str | None = None,
    output_extension: str | None = None,
    ffmpeg_bin: str | None = None,
) -> str:
    output = output_path or default_output_path(info, target_seconds, output_extension)
    output_ext = normalize_output_extension(Path(output).suffix)
    if info.media_type == "video":
        return extend_video(info, target_seconds, output, ffmpeg_bin=ffmpeg_bin)

    if output_ext == normalize_output_extension(info.extension):
        return extend_sequence(info, target_seconds, output)
    return convert_extended_sequence(info, target_seconds, output, ffmpeg_bin=ffmpeg_bin)


def convert_extended_sequence(
    info: MediaInfo,
    target_seconds: float,
    output_path: str,
    ffmpeg_bin: str | None = None,
) -> str:
    output_ext = normalize_output_extension(Path(output_path).suffix)
    _validate_output_path(output_path, output_ext)

    padding = info.padding or 4
    with tempfile.TemporaryDirectory(prefix="media_duration_hold_") as temp_dir:
        temp_pattern = str(Path(temp_dir) / f"hold.%0{padding}d{info.extension}")
        extend_sequence(info, target_seconds, temp_pattern)
        cmd = build_sequence_convert_command(
            info, target_seconds, output_path, input_pattern=temp_pattern, ffmpeg_bin=ffmpeg_bin
        )
        _run_ffmpeg(cmd, output_path)
    return output_path


def _run_ffmpeg(cmd: list[str], output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(cmd, capture_output=True, encoding="utf-8", check=True, **_SUBPROCESS_OPTIONS)
    except FileNotFoundError as exc:
        raise MediaDurationError(f"ffmpeg 실행 파일을 찾을 수 없습니다: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise MediaDurationError(f"ffmpeg 처리 실패: {exc.stderr}") from exc


def extend_sequence(info: MediaInfo, target_seconds: float, output_pattern: str | None = None) -> str:
    if info.media_type != "sequence":
        raise MediaDurationError("시퀀스 입력만 이미지 복제를 수행할 수 있습니다.")
    if info.start_frame is None or info.padding is None or not info.sequence_pattern or not info.last_frame_path:
        raise MediaDurationError("시퀀스 정보를 확인할 수 없습니다.")

    target_frames = validate_target(info, target_seconds)
    output = output_pattern or default_output_path(info, target_seconds, info.extension)
    output_ext = normalize_output_extension(Path(output).suffix)
    _validate_output_path(output, output_ext)
    output_dir = Path(output).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    input_pattern = info.sequence_pattern
    first_output = None
    for offset in range(target_frames):
        frame = info.start_frame + offset
        source_frame = min(frame, info.end_frame or frame)
        source = input_pattern % source_frame
        if not os.path.isfile(source):
            source = info.last_frame_path
        destination = output % frame
        shutil.copy2(source, destination)
        first_output = first_output or destination

    return first_output or output


def _build_sequence_info(
    folder: str,
    prefix: str,
    sep: str,
    padding: int,
    ext: str,
    frames: Iterable[int],
    fps: float,
) -> MediaInfo:
    sorted_frames = sorted(set(frames))
    start = sorted_frames[0]
    end = sorted_frames[-1]
    pattern = str(Path(folder) / f"{prefix}{sep}%0{padding}d{ext}")
    return MediaInfo(
        path=pattern,
        media_type="sequence",
        extension=ext,
        fps=float(fps),
        frame_count=len(sorted_frames),
        duration_seconds=len(sorted_frames) / float(fps),
        start_frame=start,
        end_frame=end,
        padding=padding,
        sequence_pattern=pattern,
        last_frame_path=pattern % end,
    )


def _build_single_image_info(path: str, fps: float) -> MediaInfo:
    p = Path(path)
    pattern = str(p.with_name(f"{p.stem}.%04d{p.suffix.lower()}"))
    return MediaInfo(
        path=path,
        media_type="sequence",
        extension=p.suffix.lower(),
        fps=float(fps),
        frame_count=1,
        duration_seconds=1 / float(fps),
        start_frame=1,
        end_frame=1,
        padding=4,
        sequence_pattern=pattern,
        last_frame_path=path,
    )


def _validate_output_path(output: str, output_ext: str) -> None:
    has_frame_pattern = "%" in output
    if output_ext in OUTPUT_SEQUENCE_EXTENSIONS and not has_frame_pattern:
        raise MediaDurationError("이미지 시퀀스 출력 경로에는 %04d 같은 프레임 패턴이 필요합니다.")
    if output_ext in OUTPUT_VIDEO_EXTENSIONS and has_frame_pattern:
        raise MediaDurationError("영상 출력 경로에는 프레임 패턴을 사용할 수 없습니다.")


def _sequence_stem(pattern: Path) -> str:
    match = re.match(r"^(.*?)([._])%0?\d+d$", pattern.stem)
    if match:
        return match.group(1)
    return pattern.stem


def _parse_rate(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            return float(numerator) / float(denominator)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
    return _parse_float(value)


def _parse_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def warmup_ffprobe(ffprobe_bin: str | None = None) -> None:
    ffprobe_bin = ffprobe_bin or ffprobe_path()
    try:
        subprocess.run(
            [str(ffprobe_bin), "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **_SUBPROCESS_OPTIONS,
        )
    except OSError:
        return


def _format_seconds_for_name(seconds: float) -> str:
    text = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", "p")
