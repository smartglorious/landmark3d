# Start the Landmark3D local web app (creates the venv on first run).
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    py -3 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -e .
}
Start-Process "http://127.0.0.1:8770"
.\.venv\Scripts\python.exe -m landmark3d serve --port 8770
