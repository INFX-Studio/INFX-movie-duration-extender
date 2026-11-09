# INFX Movie Duration Extender

영상(MOV, MP4) 또는 이미지 시퀀스(PNG, JPG)의 **마지막 프레임을 유지(hold)한 채 전체 길이를 목표 시간까지 늘려 주는** macOS 앱입니다.
ffmpeg 가 내장되어 있어 별도 설치 없이 바로 사용할 수 있습니다.

- 대상: macOS 26 이상, Apple Silicon(arm64)
- 입력: `.mov`, `.mp4`, `.png` / `.jpg` 이미지 시퀀스(폴더 또는 파일 하나)
- 출력: ProRes 422 HQ MOV, H.264 MP4, PNG 시퀀스, JPG 시퀀스
- 라이선스: GPL v3 ([LICENSE](LICENSE))

## 설치

1. [Releases](https://github.com/INFX-Studio/INFX-movie-duration-extender/releases/latest) 에서 `INFX-Movie-Duration-Extender-<버전>-arm64.dmg` 를 내려받습니다.
2. DMG 를 열고 앱을 `Applications` 폴더로 끌어다 놓습니다.
3. 처음 실행할 때 "확인되지 않은 개발자" 경고가 나오면 **시스템 설정 → 개인정보 보호 및 보안 → 그래도 열기** 를 한 번 눌러 주세요.
   - 또는 터미널에서 다음 명령을 실행해 주세요.

     ```bash
     xattr -dr com.apple.quarantine "/Applications/INFX Movie Duration Extender.app"
     ```

앱은 Apple Developer ID 로 서명·공증되어 있지 않습니다(ad-hoc 서명). 이 경고는 첫 실행 때 한 번만 나옵니다.

## 자동 업데이트

- 앱을 실행하면 GitHub Releases 의 최신 버전을 확인합니다. 새 버전이 있으면 내려받아 설치한 뒤 앱을 다시 시작합니다.
- 다운로드한 DMG 는 릴리즈에 함께 올라간 `.sha256` 파일로 무결성을 확인합니다.
- `/Applications` 에 설치된 앱에서만 동작합니다. 다른 위치에서 실행하거나 네트워크에 연결되어 있지 않으면 확인을 건너뛰고 현재 버전으로 실행됩니다.
- 서버로 전송하는 정보는 없습니다. GitHub API 조회(GET)만 수행합니다.

## 사용 방법

1. **파일 추가 / 폴더 추가** 버튼 또는 드래그 앤 드롭으로 입력을 추가합니다.
2. **목표 길이(초)** 를 항목별로 입력하거나, 상단 **전체 목표 길이(초)** 를 **선택 항목에 적용** 합니다.
3. 이미지 시퀀스는 **FPS** 를 직접 지정할 수 있습니다. (기본 24fps)
4. **출력 포맷** 을 선택합니다.

   | 포맷 | 내용 |
   | --- | --- |
   | MOV | Apple ProRes 422 HQ |
   | MP4 | H.264 High, CRF 17, `faststart` |
   | PNG | PNG 이미지 시퀀스 |
   | JPG | JPG 이미지 시퀀스 |

5. **시작** 을 누르면 목록을 순서대로 처리하고, 완료 후 성공 / 스킵 / 실패 건수를 보여 줍니다.

- 출력 경로 기본값은 입력과 같은 위치의 `원본이름_hold_<목표길이>s` 입니다.
- 목표 길이가 원본보다 짧으면 처리하지 않습니다.
- 같은 이름의 출력 파일이 있으면 덮어씁니다.
- 오디오는 출력에 포함되지 않습니다.
- MP4 출력 시 홀수 해상도는 짝수로 보정됩니다.
- 로그: `~/Library/Logs/INFX Movie Duration Extender/app.log`

## 개발

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
scripts/fetch_ffmpeg.sh          # vendor/ffmpeg/ 에 ffmpeg, ffprobe 내려받기
```

```bash
.venv/bin/python run.py          # 실행
.venv/bin/pytest                 # 테스트 (ffmpeg 가 없으면 통합 테스트는 건너뜁니다)
.venv/bin/ruff check .           # lint
```

### 빌드

```bash
scripts/build_app.sh
```

| 산출물 | 내용 |
| --- | --- |
| `dist/INFX Movie Duration Extender.app` | ad-hoc 서명된 앱 번들 |
| `dist/INFX-Movie-Duration-Extender-<버전>-arm64.dmg` | 배포용 디스크 이미지 |
| `dist/INFX-Movie-Duration-Extender-<버전>-arm64.dmg.sha256` | 자동 업데이트용 해시 |

버전은 `src/mde/__init__.py` 의 `__version__` 을 사용합니다.

### 릴리즈

1. `__version__` 을 올리고 커밋합니다.
2. `scripts/build_app.sh` 로 빌드합니다.
3. `v<버전>` 태그로 GitHub Release 를 만들고 **DMG 와 `.sha256` 파일을 함께** 업로드합니다. 두 파일이 모두 있어야 자동 업데이트가 동작합니다.

```bash
gh release create v1.0.0 dist/*.dmg dist/*.dmg.sha256 --title "INFX Movie Duration Extender 1.0.0" --notes "변경 내용"
```

### 구조

| 경로 | 내용 |
| --- | --- |
| `src/mde/core.py` | 입력 분석, 길이 계산, 시퀀스 복제, ffmpeg 실행 |
| `src/mde/ffmpeg_cmd.py` | 출력 포맷별 인코딩 인자 |
| `src/mde/ffmpeg_bin.py` | 내장 ffmpeg / ffprobe 경로 해석 |
| `src/mde/updater.py` | GitHub Release 확인, 다운로드, 해시 검증, 교체 스크립트 |
| `src/mde/update_ui.py` | 시작 시 업데이트 진행 창 |
| `src/mde/app.py` | PySide6 UI |
| `run.py` | 실행 진입점 |
| `mde.spec`, `scripts/build_app.sh` | PyInstaller 빌드와 DMG 생성 |
| `scripts/fetch_ffmpeg.sh` | 내장 ffmpeg 다운로드 (출처·해시 고정) |

## 라이선스

Copyright (C) 2026 INFX Studios

이 프로그램은 GNU General Public License v3 에 따라 배포됩니다. 전문은 [LICENSE](LICENSE) 를 참고해 주세요.

내장된 ffmpeg / ffprobe 는 libx264, libx265 를 포함한 GPL v3 빌드이며, 출처와 소스 코드 위치는 [vendor/ffmpeg/README.md](vendor/ffmpeg/README.md) 에 있습니다.
UI 는 [PySide6](https://pypi.org/project/PySide6/) (LGPL v3) 을 사용합니다.
