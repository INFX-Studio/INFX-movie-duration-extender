# INFX Movie Duration Extender 실행 폴더와 배포용 설치 파일(setup.exe)을 만듭니다. (Windows x64 전용)
# 설치 파일 생성에는 Inno Setup 6 이 필요합니다. (winget install JRSoftware.InnoSetup)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$AppName = 'INFX Movie Duration Extender'
$Python = Join-Path $Root '.venv\Scripts\python.exe'

foreach ($Bin in 'ffmpeg.exe', 'ffprobe.exe') {
    if (-not (Test-Path -LiteralPath "vendor\ffmpeg\$Bin")) {
        throw "[오류] vendor\ffmpeg\$Bin 실행 파일이 없습니다. vendor\ffmpeg\README.md 를 확인해 주세요."
    }
}

# Inno Setup 은 사용자 설치(winget 기본)와 전체 사용자 설치 위치 중 있는 곳을 사용합니다.
$Iscc = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Iscc) { throw '[오류] Inno Setup 6 (ISCC.exe) 이 없습니다. winget install JRSoftware.InnoSetup 으로 설치해 주세요.' }

$Version = & $Python -c "import sys; sys.path.insert(0, 'src'); import mde; print(mde.__version__)"
if ($LASTEXITCODE -ne 0) { throw '[오류] 버전을 읽지 못했습니다.' }

foreach ($Dir in 'build', 'dist') {
    if (Test-Path -LiteralPath $Dir) { Remove-Item -LiteralPath $Dir -Recurse -Force }
}
& $Python -m PyInstaller --noconfirm mde.spec
if ($LASTEXITCODE -ne 0) { throw '[오류] PyInstaller 빌드에 실패했습니다.' }

& $Iscc /Q "/DAppVersion=$Version" installer.iss
if ($LASTEXITCODE -ne 0) { throw '[오류] 설치 파일 생성에 실패했습니다.' }
$Setup = "dist\INFX-Movie-Duration-Extender-$Version-win64-setup.exe"

# 자동 업데이트가 다운로드 후 검증하는 해시 파일입니다. 릴리즈에 설치 파일과 함께 올립니다.
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Setup).Hash.ToLower()
$SetupName = Split-Path -Leaf $Setup
Set-Content -LiteralPath "$Setup.sha256" -Value "$Hash  $SetupName" -Encoding ascii

Write-Host "[완료] dist\$AppName\$AppName.exe"
Write-Host "[완료] $Setup"
Write-Host "[완료] $Setup.sha256"
