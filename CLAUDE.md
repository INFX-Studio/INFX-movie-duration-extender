# 프로젝트 지침

## 릴리즈

- "릴리즈 해" 요청 시 항상 다음 버전으로 올려 macOS(DMG)와 Windows(setup.exe)를 함께 릴리즈한다. 한 OS만 수정했어도 동일하다.
- 절차는 README.md 의 "릴리즈" 절을 따른다. DMG, setup.exe 와 각각의 `.sha256` 4개가 모두 올라간 뒤에만 릴리즈를 공개한다(draft → 공개).
- macOS 빌드는 macOS 에서만 가능하다. Windows 세션에서 릴리즈할 때는 아래 "Mac 세션 연동" 방식으로 Mac 에 빌드·업로드를 맡긴다.

### Mac 세션 연동 (Windows 세션에서 릴리즈할 때)

Mac 의 Claude 세션을 Remote Control 로 연결해 메시지로 작업을 지시한다. SSH 는 사용하지 않는다.

1. Windows 세션: `__version__` 을 다음 버전으로 올려 커밋·push 하고, `scripts\build_app.ps1` 로 빌드한 뒤 draft 릴리즈를 만들어 setup.exe 와 `.sha256` 을 올린다.
2. `ListAgents` 로 Mac 의 Remote Control 세션을 찾는다. 저장소 폴더(`/Users/gmdirect/dev/infx/media_duration_extender`)에서 열린 세션이어야 한다. 없거나 offline 이면 사용자에게 Mac 에서 해당 폴더로 세션을 열고 Remote Control 을 켜 달라고 요청한다.
3. `SendMessage` 로 Mac 세션에 다음을 요청한다.
   - 규칙: main 만 사용, 소스 수정·커밋·push 금지, 오류·충돌 시 중단 후 보고, 공개(`--draft=false`) 금지
   - `git status` 확인 후 `git pull`, HEAD 가 1번 커밋인지와 `__version__` 확인
   - `.venv`·`vendor/ffmpeg` 가 없으면 준비, `.venv/bin/pytest -q` 실행(실패 시 빌드 중단)
   - `scripts/build_app.sh` 빌드 후 `gh release upload v<버전> dist/*.dmg dist/*.dmg.sha256`
   - 회신 항목: HEAD 커밋, pytest 결과, DMG 파일명·크기·sha256, 업로드 결과, 릴리즈 자산 목록
4. 회신을 받으면 Windows 세션에서 `gh release view v<버전> --json assets` 로 4개 자산과 각 `.sha256` 내용이 GitHub digest 와 일치하는지 직접 확인한다. Mac 세션의 보고만으로 완료 처리하지 않는다.
5. 확인 후 Windows 세션에서 `gh release edit v<버전> --draft=false --latest` 로 공개하고, `releases/latest` 가 새 버전인지 확인한다.
