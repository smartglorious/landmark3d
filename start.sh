#!/usr/bin/env bash
# Start the Landmark3D local web app (run ./install.sh once first).
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || ./install.sh
URL=http://127.0.0.1:8770
( sleep 2; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) >/dev/null 2>&1 &
exec .venv/bin/python -m landmark3d serve --port 8770
