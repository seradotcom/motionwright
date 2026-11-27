$ErrorActionPreference = 'Stop'
$archive = Join-Path $env:RUNNER_TEMP 'ffmpeg-9.1-essentials_build.7z'
$release = 'https://github.com/GyanD/codexffmpeg/releases/download/9.1'
Invoke-WebRequest "$release/ffmpeg-9.1-essentials_build.7z" -OutFile $archive
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '835b9a733445ba7a56a37a26cf7bde84e2f3ce7da2a8ec984276bb2abb1b922c') {
  throw 'FFmpeg archive checksum mismatch'
}
$members = @(
  'ffmpeg-9.1-essentials_build/bin/ffmpeg.exe'
  'ffmpeg-9.1-essentials_build/bin/ffprobe.exe'
)
tar.exe -xf $archive -C $env:RUNNER_TEMP -- $members
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Join-Path $env:RUNNER_TEMP 'ffmpeg-9.1-essentials_build/bin' |
  Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
