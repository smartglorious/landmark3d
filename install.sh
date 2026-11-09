#!/usr/bin/env bash
# Landmark3D installer for macOS and Linux.
#   ./install.sh [--yes] [--no-blender] [--start]
# 1. Python 3.11+ (found, or installed with Homebrew / apt after asking)
# 2. .venv + Python packages at the versions in requirements.lock
# 3. Blender 4.2+ (found, or installed with Homebrew / snap after asking)
# 4. `landmark3d setup`: offline 3D viewer files, Blender path, environment check
# 5. start.sh launcher
set -euo pipefail
cd "$(dirname "$0")"

YES=0; NO_BLENDER=0; START=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --no-blender) NO_BLENDER=1 ;;
    --start) START=1 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done

say()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$*"; }
ask()  { [ "$YES" = 1 ] && return 0; read -r -p "$1 [Y/n] " a; [ -z "$a" ] || [[ "$a" =~ ^[Yy] ]]; }
has()  { command -v "$1" >/dev/null 2>&1; }

find_python() {
  for c in python3.13 python3.12 python3.11 python3 python; do
    if has "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}

echo "Landmark3D installer"
OS="$(uname -s)"

# 1) Python ------------------------------------------------------------------------------------------------------
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  warn "Python 3.11 or newer was not found."
  if [ "$OS" = "Darwin" ] && has brew && ask "Install Python 3.12 with Homebrew?"; then
    brew install python@3.12
  elif has apt-get && ask "Install python3, python3-venv and python3-pip with apt (needs sudo)?"; then
    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
  elif has dnf && ask "Install python3 with dnf (needs sudo)?"; then
    sudo dnf install -y python3
  fi
  PY="$(find_python || true)"
  [ -n "$PY" ] || { echo "Python 3.11+ is required: https://www.python.org/downloads/"; exit 1; }
fi
say "Python: $("$PY" -c 'import sys; print(sys.executable, sys.version.split()[0])')"

# 2) Virtual environment + packages --------------------------------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  say "Creating .venv"
  "$PY" -m venv .venv || { warn "venv failed - on Debian/Ubuntu install python3-venv"; exit 1; }
fi
say "Installing Python packages (requirements.lock)"
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -c requirements.lock -e .

# 3) Blender --------------------------------------------------------------------------------------------------------
BLENDER="$(.venv/bin/python -c 'from landmark3d import doctor; print(doctor.find_blender() or "")')"
if [ -z "$BLENDER" ] && [ "$NO_BLENDER" = 0 ]; then
  warn "Blender was not found."
  if [ "$OS" = "Darwin" ] && has brew && ask "Install Blender with Homebrew (--cask)?"; then
    brew install --cask blender
  elif has snap && ask "Install Blender with snap (needs sudo)?"; then
    sudo snap install blender --classic
  fi
  BLENDER="$(.venv/bin/python -c 'from landmark3d import doctor; print(doctor.find_blender() or "")')"
  [ -n "$BLENDER" ] || warn "Install Blender 4.2+ from https://www.blender.org/download/ and run ./install.sh again (or set its path in the app's Settings)."
fi

# 4) Setup ------------------------------------------------------------------------------------------------------------
say "Running landmark3d setup"
READY=1
.venv/bin/python -m landmark3d setup || READY=0

# 5) Launcher -----------------------------------------------------------------------------------------------------------
chmod +x start.sh 2>/dev/null || true
echo
if [ "$READY" = 1 ]; then
  printf '\033[32mInstalled. Start Landmark3D with ./start.sh  ->  http://127.0.0.1:8770\033[0m\n'
else
  warn "Installed, but some required checks failed (see ERR above). Fix them and run ./install.sh again."
fi
cat <<'EOF'

Optional, for AI research (no API key needed - sign in with your account in the browser):
  Claude : install Claude Code  (https://claude.com/claude-code)  then  claude auth login
  ChatGPT: install Codex CLI    (npm install -g @openai/codex)    then  codex login
Then open the app -> 'Kết nối AI' and turn the provider on. Set your contact (website or e-mail) in 'Cài đặt'.
EOF
if [ "$START" = 1 ] && [ "$READY" = 1 ]; then exec ./start.sh; fi
