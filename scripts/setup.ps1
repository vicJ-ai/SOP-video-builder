# One-time setup on Windows. Re-runnable.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Asr] [-Ocr] [-Kokoro] [-SkellySync]
param([switch]$Asr, [switch]$Ocr, [switch]$Kokoro, [switch]$SkellySync)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Installing FFmpeg (winget Gyan.FFmpeg)..."
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
}

$extras = @("--extra", "dev", "--extra", "system-tts")
if ($Asr) { $extras += @("--extra", "asr") }
if ($Ocr) { $extras += @("--extra", "ocr") }
if ($Kokoro) { $extras += @("--extra", "tts") }
Push-Location "$root\backend"; uv sync --python 3.12 @extras; Pop-Location

Push-Location "$root\render"; npm install --no-audit --no-fund; node scripts/vendor-remocn.mjs; Pop-Location
Push-Location "$root\web"; npm install --no-audit --no-fund; npm run build; Pop-Location

if ($SkellySync) {
    # AGPL-3.0: kept in its own interpreter and called as a separate process (see README, Licensing).
    $dir = "$root\tools\skellysync"
    New-Item -ItemType Directory -Force $dir | Out-Null
    Push-Location $dir
    uv venv --python 3.12 .venv
    uv pip install --python .venv\Scripts\python.exe skelly_synchronize
    Pop-Location
}

if (-not (Test-Path "$root\backend\.env")) { Copy-Item "$root\backend\.env.example" "$root\backend\.env" }
Write-Host "`nDone. Start with:  scripts\start.ps1   then open http://localhost:8080"
