#!/usr/bin/env bash
# Alpha — Automated installer for Linux / macOS / WSL
# Usage: ./install.sh

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo "=== Alpha Setup ==="

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

if ! command -v node >/dev/null 2>&1; then
    echo "Error: Node.js (v22+) is required."
    exit 1
fi

[ ! -f .env ] && cp .env.example .env 2>/dev/null || touch .env
[ ! -f frontend/.env ] && echo "NODE_ENV=development" > frontend/.env
[ ! -f config.yaml ] && cp config.example.yaml config.yaml 2>/dev/null || true
[ ! -f extensions_config.json ] && cp extensions_config.example.json extensions_config.json 2>/dev/null || echo "{}" > extensions_config.json

echo "Installing backend dependencies..."
cd "$REPO_ROOT/backend"
uv sync

echo "Installing frontend dependencies..."
cd "$REPO_ROOT/frontend"
node ../scripts/pnpm.py install || npm install

echo "=== Installation complete! Run ./start.sh to launch ==="
