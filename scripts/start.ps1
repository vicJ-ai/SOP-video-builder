# Starts the API and the built review UI on http://localhost:8080
#   scripts\start.ps1 [-Dev]   (-Dev also runs the Vite dev server on :5173 with hot reload)
param([switch]$Dev)
$root = Split-Path -Parent $PSScriptRoot
if ($Dev) { Start-Process -WorkingDirectory "$root\web" -FilePath "npm.cmd" -ArgumentList "run", "dev" }
Push-Location "$root\backend"
try { .venv\Scripts\python -m uvicorn app.main:app --port 8080 } finally { Pop-Location }
