#!/bin/bash
# INFX Movie Duration Extender.app 과 배포용 DMG 를 만듭니다. (macOS Apple Silicon 전용)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

APP_NAME="INFX Movie Duration Extender"
PYTHON="$ROOT/.venv/bin/python"

for bin in ffmpeg ffprobe; do
    if [ ! -x "vendor/ffmpeg/$bin" ]; then
        echo "[오류] vendor/ffmpeg/$bin 실행 파일이 없습니다. vendor/ffmpeg/README.md 를 확인해 주세요." >&2
        exit 1
    fi
done

VERSION="$("$PYTHON" -c "import sys; sys.path.insert(0, 'src'); import mde; print(mde.__version__)")"

rm -rf build dist
"$PYTHON" -m PyInstaller --noconfirm mde.spec

# 번들 전체를 ad-hoc 서명합니다. (Developer ID 미사용, 공증 없음)
codesign --force --deep --sign - "dist/$APP_NAME.app"
codesign --verify --deep --strict "dist/$APP_NAME.app"

DMG="dist/INFX-Movie-Duration-Extender-$VERSION-arm64.dmg"
STAGE="$(mktemp -d)"
cp -R "dist/$APP_NAME.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

# 자동 업데이트가 다운로드 후 검증하는 해시 파일입니다. 릴리즈에 DMG 와 함께 올립니다.
(cd dist && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")

echo "[완료] dist/$APP_NAME.app"
echo "[완료] $DMG"
echo "[완료] $DMG.sha256"
