# 내장 ffmpeg / ffprobe

앱에 함께 포장되는 ffmpeg, ffprobe 정적 빌드(macOS Apple Silicon)가 놓이는 폴더입니다.
바이너리는 용량 문제로 git 에 포함하지 않으며, 아래 스크립트로 받습니다.

```bash
scripts/fetch_ffmpeg.sh
```

## 출처와 라이선스

| 항목 | 내용 |
| --- | --- |
| 배포처 | <https://ffmpeg.martin-riedl.de> (Martin Riedl 빌드) |
| 버전 | 9.0.2 |
| 빌드 구성 | `--enable-gpl --enable-version3 --enable-libx264 --enable-libx265` 등 |
| 라이선스 | **GPL v3** (libx264, libx265 포함) |
| 소스 코드 | FFmpeg: <https://ffmpeg.org/download.html> / 빌드 스크립트: <https://github.com/martin-riedl/ffmpeg-build-script> |

다운로드 파일의 SHA-256 은 `scripts/fetch_ffmpeg.sh` 에 고정되어 있으며, 값이 다르면 스크립트가 중단됩니다.

## 요구 사항 (다른 빌드로 교체할 때)

- 아키텍처: arm64
- 외부 dylib 의존이 없는 정적 빌드 (Homebrew 설치본은 사용할 수 없습니다)
- 인코더: `prores_ks`, `libx264`, `png`, `mjpeg`
- 필터: `tpad`, `scale`

```bash
file vendor/ffmpeg/ffmpeg
otool -L vendor/ffmpeg/ffmpeg        # /usr/lib, /System 외 의존이 없어야 합니다
vendor/ffmpeg/ffmpeg -hide_banner -encoders | grep -E 'prores_ks|libx264'
```
