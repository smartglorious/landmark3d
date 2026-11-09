@echo off
rem Start the Landmark3D local web app (run install.cmd once first).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Landmark3D is not installed yet - running install.cmd first.
  call "%~dp0install.cmd"
)
start "" http://127.0.0.1:8770
".venv\Scripts\python.exe" -m landmark3d serve --port 8770
