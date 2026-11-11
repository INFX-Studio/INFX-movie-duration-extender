# INFX Movie Duration Extender 실행 폴더와 배포용 zip 을 만듭니다. (Windows x64 전용)
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

$Version = & $Python -c "import sys; sys.path.insert(0, 'src'); import mde; print(mde.__version__)"
if ($LASTEXITCODE -ne 0) { throw '[오류] 버전을 읽지 못했습니다.' }

foreach ($Dir in 'build', 'dist') {
    if (Test-Path -LiteralPath $Dir) { Remove-Item -LiteralPath $Dir -Recurse -Force }
}
& $Python -m PyInstaller --noconfirm mde.spec
if ($LASTEXITCODE -ne 0) { throw '[오류] PyInstaller 빌드에 실패했습니다.' }

# zip 최상위에 앱 폴더가 그대로 들어갑니다. 자동 업데이트가 이 폴더 이름을 기준으로 교체합니다.
$Zip = "dist\INFX-Movie-Duration-Extender-$Version-win64.zip"
Compress-Archive -LiteralPath "dist\$AppName" -DestinationPath $Zip

# 자동 업데이트가 다운로드 후 검증하는 해시 파일입니다. 릴리즈에 zip 과 함께 올립니다.
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLower()
$ZipName = Split-Path -Leaf $Zip
Set-Content -LiteralPath "$Zip.sha256" -Value "$Hash  $ZipName" -Encoding ascii

Write-Host "[완료] dist\$AppName\$AppName.exe"
Write-Host "[완료] $Zip"
Write-Host "[완료] $Zip.sha256"
