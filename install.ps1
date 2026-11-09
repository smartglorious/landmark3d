<#
  Landmark3D installer for Windows 10/11.
  Double-click install.cmd, or run:  powershell -ExecutionPolicy Bypass -File install.ps1 [-Yes] [-NoShortcut] [-NoBlender] [-Start]

  1. Python 3.11+  (found, or installed with winget after asking)
  2. .venv + Python packages at the versions in requirements.lock
  3. Blender 4.2+  (found, or installed with winget after asking)
  4. `landmark3d setup`: offline 3D viewer files, Blender path, environment check
  5. Landmark3D.cmd launcher (+ desktop shortcut)
  Nothing is installed system-wide without asking, unless -Yes is given.
#>
param([switch]$Yes, [switch]$NoShortcut, [switch]$NoBlender, [switch]$Start)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Say($m)  { Write-Host "==> $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Ask($q)  { if ($Yes) { return $true }; $a = Read-Host "$q [Y/n]"; return ($a -eq '' -or $a -match '^[Yy]') }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Has($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Find-Python {
  $cands = @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('py', '-3'), @('python'), @('python3'))
  foreach ($c in $cands) {
    if (-not (Has $c[0])) { continue }
    $rest = @($c | Select-Object -Skip 1)
    try { $v = & $c[0] @rest -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0 -and $v -and ([version]$v.Trim() -ge [version]'3.11')) { return ,$c }
  }
  return $null
}

Write-Host "Landmark3D installer" -ForegroundColor Green

# 1) Python ---------------------------------------------------------------------------------------------------
$py = Find-Python
if (-not $py) {
  Warn "Python 3.11 or newer was not found."
  if ((Has 'winget') -and (Ask "Install Python 3.12 with winget (per-user)?")) {
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $py = Find-Python
  }
  if (-not $py) { throw "Python 3.11+ is required: install it from https://www.python.org/downloads/ (tick 'Add to PATH') and run install.cmd again." }
}
$pyRest = @($py | Select-Object -Skip 1)
Say ("Python: " + (& $py[0] @pyRest -c "import sys;print(sys.executable, sys.version.split()[0])"))

# 2) Virtual environment + packages ---------------------------------------------------------------------------
$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  Say "Creating .venv"
  & $py[0] @pyRest -m venv .venv
}
Say "Installing Python packages (requirements.lock)"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -c requirements.lock -e .
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 3) Blender ---------------------------------------------------------------------------------------------------
$blender = (& $venvPy -c "from landmark3d import doctor; print(doctor.find_blender() or '')").Trim()
if (-not $blender -and -not $NoBlender) {
  Warn "Blender was not found."
  if ((Has 'winget') -and (Ask "Install Blender (latest) with winget? (~400 MB)")) {
    winget install -e --id BlenderFoundation.Blender --silent --accept-package-agreements --accept-source-agreements
    $blender = (& $venvPy -c "from landmark3d import doctor; print(doctor.find_blender() or '')").Trim()
  }
  if (-not $blender) { Warn "Install Blender 4.2+ from https://www.blender.org/download/ and run install.cmd again (or set its path in the app's Settings)." }
}

# 4) Setup: viewer files, Blender path, environment check -------------------------------------------------------
Say "Running landmark3d setup"
& $venvPy -m landmark3d setup
$ready = ($LASTEXITCODE -eq 0)

# 5) Launcher + shortcut ---------------------------------------------------------------------------------------
$launcher = Join-Path $PSScriptRoot 'Landmark3D.cmd'
if (-not $NoShortcut) {
  try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Landmark3D.lnk'))
    $lnk.TargetPath = $launcher
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.Description = 'Landmark3D local web app'
    $lnk.Save()
    Say "Desktop shortcut created"
  } catch { Warn "Could not create the desktop shortcut: $($_.Exception.Message)" }
}

Write-Host ""
if ($ready) { Write-Host "Installed. Start Landmark3D with Landmark3D.cmd (or the desktop shortcut): http://127.0.0.1:8770" -ForegroundColor Green }
else { Warn "Installed, but some required checks failed (see ERR above). Fix them and run install.cmd again." }
Write-Host @"

Optional, for AI research (no API key needed - sign in with your account in the browser):
  Claude : install Claude Code  (https://claude.com/claude-code)  then  claude auth login
  ChatGPT: install Codex CLI    (npm install -g @openai/codex)    then  codex login
Then open the app -> 'Kết nối AI' and turn the provider on. Set your contact (website or e-mail) in 'Cài đặt'.
"@
if ($Start -and $ready) { & $launcher }
