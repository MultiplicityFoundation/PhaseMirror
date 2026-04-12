#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m pip install --upgrade pip
python -m pip install nuitka
python -m nuitka --standalone --output-dir=dist mcp_server/app.py

echo "Build complete: dist/"
