#!/usr/bin/env bash
# Alpha — Unified Super-Agent Platform launcher for Linux / macOS / WSL
# Usage: ./start.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo -e "\033[1;36m========================================================\033[0m"
echo -e "\033[1;36m    Alpha — Unified Super-Agent Platform     \033[0m"
echo -e "\033[1;36m========================================================\033[0m"

# 1. Check prerequisites
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

if ! command -v node >/dev/null 2>&1; then
    echo "Error: Node.js (v22+) is required. Please install Node.js."
    exit 1
fi

# 2. Config files
[ ! -f .env ] && cp .env.example .env 2>/dev/null || touch .env
[ ! -f frontend/.env ] && echo "NODE_ENV=development" > frontend/.env
[ ! -f config.yaml ] && cp config.example.yaml config.yaml 2>/dev/null || true
[ ! -f extensions_config.json ] && cp extensions_config.example.json extensions_config.json 2>/dev/null || echo "{}" > extensions_config.json

mkdir -p logs

export AGENT_WORKSPACE_AUTH_DISABLED=1
export AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8001
export PORT=3000
export PYTHONPATH=.

# 3. Cleanup existing ports
fuser -k 8001/tcp 2>/dev/null || true
fuser -k 3000/tcp 2>/dev/null || true

# 4. Start Gateway
echo -e "\033[1;33m[1/2] Starting Gateway API on port 8001...\033[0m"
cd "$REPO_ROOT/backend"
uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 > "$REPO_ROOT/logs/gateway.log" 2>&1 &
GATEWAY_PID=$!

# 5. Start Frontend
echo -e "\033[1;33m[2/2] Starting Frontend UI on port 3000...\033[0m"
cd "$REPO_ROOT/frontend"
node scripts/dev.mjs > "$REPO_ROOT/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!

cleanup() {
    echo -e "\nShutting down Alpha..."
    kill $GATEWAY_PID $FRONTEND_PID 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# 6. Wait for health
echo "Waiting for services to become healthy..."
for i in {1..45}; do
    if curl -s http://127.0.0.1:8001/health >/dev/null 2>&1; then
        echo -e "\033[1;32m  [OK] Gateway is healthy!\033[0m"
        break
    fi
    sleep 1
done

echo -e "\033[1;32m========================================================\033[0m"
echo -e "\033[1;32m   Alpha is LIVE! Access at: http://localhost:3000   \033[0m"
echo -e "\033[1;32m========================================================\033[0m"
echo "Press [Ctrl+C] to stop all services."

# Open browser if available
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open http://localhost:3000 >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    open http://localhost:3000 >/dev/null 2>&1 &
fi

wait
