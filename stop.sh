#!/usr/bin/env bash
# Alpha — Stop all running services
# Usage: ./stop.sh

echo "Stopping Alpha services..."
fuser -k 8001/tcp 2>/dev/null || true
fuser -k 3000/tcp 2>/dev/null || true
fuser -k 8201/tcp 2>/dev/null || true
fuser -k 2026/tcp 2>/dev/null || true
pkill -f "uvicorn app.gateway.app:app" 2>/dev/null || true
pkill -f "scripts/dev.mjs" 2>/dev/null || true
echo "Alpha services stopped."
