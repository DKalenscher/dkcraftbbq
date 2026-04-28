#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Create .env from example if missing
if [ ! -f .env ]; then
  echo "[!] No .env file found. Copying .env.example — EDIT IT BEFORE USE."
  cp .env.example .env
fi

# Create virtual environment if needed
if [ ! -d .venv ]; then
  echo "[*] Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install / upgrade dependencies
echo "[*] Installing dependencies..."
pip install -q -r requirements.txt

# Ensure data dirs exist
mkdir -p data/uploads

# Source the .env for PORT/HOST (optional, uvicorn reads it too)
export $(grep -v '^#' .env | xargs 2>/dev/null) || true

echo "[*] Starting DKCraftBBQ..."
echo "    Public page:  http://${HOST:-0.0.0.0}:${PORT:-8000}/"
echo "    Admin panel:  http://${HOST:-0.0.0.0}:${PORT:-8000}/admin.html"
echo ""

uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8000}" \
  --workers 1 \
  --log-level info
