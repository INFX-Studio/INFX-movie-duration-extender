"""출력 포맷별 ffmpeg 인코딩 인자."""

from __future__ import annotations

# Apple ProRes 4:2:2 HQ (MOV 출력)
PRORES_HQ_SPEC = {
    'codec': 'prores_ks',
    'profile': 3,
    'pix_fmt': 'yuv422p10le',
    'vendor': 'apl0',
    'bits_per_mb': 8000,
}

# 배포용 H.264 High (MP4 출력)
H264_HIGH_DELIVERY_SPEC = {
    'codec': 'libx264',
    'profile': 'high',
    'preset': 'veryslow',
    'crf': 17,
    'pix_fmt': 'yuv420p',
}

# yuv420p 는 홀수 해상도를 허용하지 않으므로 가로/세로를 짝수로 맞춥니다.
EVEN_SCALE_FILTER = 'scale=trunc(iw/2)*2:trunc(ih/2)*2'

_SPEC_OPTIONS = (
    ('codec', '-c:v'),
    ('profile', '-profile:v'),
    ('preset', '-preset'),
    ('crf', '-crf'),
    ('pix_fmt', '-pix_fmt'),
    ('vendor', '-vendor'),
    ('bits_per_mb', '-bits_per_mb'),
)


def build_codec_args(spec: dict) -> list[str]:
    args: list[str] = []
    for key, option in _SPEC_OPTIONS:
        if key in spec:
            args += [option, str(spec[key])]
    return args


def output_encode_args(ext: str) -> list[str]:
    """출력 확장자에 맞는 코덱·포맷 인자를 반환합니다."""
    if ext == '.mov':
        return build_codec_args(PRORES_HQ_SPEC)
    if ext == '.mp4':
        return build_codec_args(H264_HIGH_DELIVERY_SPEC) + ['-movflags', '+faststart']
    if ext == '.png':
        return ['-c:v', 'png', '-pix_fmt', 'rgb24', '-compression_level', '2', '-f', 'image2']
    if ext == '.jpg':
        return ['-c:v', 'mjpeg', '-pix_fmt', 'yuvj420p', '-q:v', '1', '-f', 'image2']
    raise ValueError(f'지원하지 않는 출력 확장자입니다: {ext}')


def needs_even_scale(ext: str) -> bool:
    return ext == '.mp4'
