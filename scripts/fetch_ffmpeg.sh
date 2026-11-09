#!/bin/bash
# 앱에 내장할 ffmpeg/ffprobe 정적 빌드(macOS arm64)를 내려받아 vendor/ffmpeg/ 에 둡니다.
# 출처: https://ffmpeg.martin-riedl.de (GPLv3 빌드, libx264/libx265 포함)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/vendor/ffmpeg"

FFMPEG_VERSION="9.0.2"
BASE_URL="https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_${FFMPEG_VERSION}"
FFMPEG_SHA256="c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924"
FFPROBE_SHA256="fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6"

mkdir -p "$DEST"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() {
    local name="$1" expected="$2"
    echo "[받기] $name ($FFMPEG_VERSION)"
    curl -fsSL "$BASE_URL/$name.zip" -o "$WORK/$name.zip"
    echo "$expected  $WORK/$name.zip" | shasum -a 256 -c - >/dev/null
    unzip -q -o "$WORK/$name.zip" "$name" -d "$WORK"
    install -m 755 "$WORK/$name" "$DEST/$name"
    xattr -d com.apple.quarantine "$DEST/$name" 2>/dev/null || true
}

fetch ffmpeg "$FFMPEG_SHA256"
fetch ffprobe "$FFPROBE_SHA256"

"$DEST/ffmpeg" -hide_banner -version | head -1
echo "[완료] $DEST/ffmpeg, $DEST/ffprobe"
