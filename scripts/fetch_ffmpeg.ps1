# 앱에 내장할 ffmpeg/ffprobe 정적 빌드(Windows x64)를 내려받아 vendor/ffmpeg/ 에 둡니다.
# 출처: https://www.gyan.dev/ffmpeg/builds (GPLv3 essentials 빌드, libx264/libx265 포함)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root 'vendor\ffmpeg'

$FfmpegVersion = '9.0.2'
$Archive = "ffmpeg-$FfmpegVersion-essentials_build"
$Url = "https://github.com/GyanD/codexffmpeg/releases/download/$FfmpegVersion/$Archive.zip"
$Sha256 = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'

New-Item -ItemType Directory -Force $Dest | Out-Null
$Work = Join-Path ([System.IO.Path]::GetTempPath()) "infx_mde_ffmpeg_$PID"
New-Item -ItemType Directory -Force $Work | Out-Null
try {
    Write-Host "[받기] $Archive.zip"
    $Zip = Join-Path $Work "$Archive.zip"
    Invoke-WebRequest -Uri $Url -OutFile $Zip
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLower()
    if ($Actual -ne $Sha256) {
        throw "SHA-256 이 다릅니다. 예상 $Sha256, 실제 $Actual"
    }
    Expand-Archive -LiteralPath $Zip -DestinationPath $Work
    foreach ($Name in 'ffmpeg.exe', 'ffprobe.exe') {
        Copy-Item -LiteralPath (Join-Path $Work "$Archive\bin\$Name") -Destination (Join-Path $Dest $Name) -Force
    }
}
finally {
    Remove-Item -LiteralPath $Work -Recurse -Force
}

& (Join-Path $Dest 'ffmpeg.exe') -hide_banner -version | Select-Object -First 1
Write-Host "[완료] $Dest\ffmpeg.exe, $Dest\ffprobe.exe"
