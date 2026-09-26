#!/usr/bin/env bash
# Alpha dev container first-run setup.
#
# Runs once when the container is created. Its whole job is to make the
# application PREBUILT so the first `up` is fast: sync the locked Python
# dependencies and produce the Next.js production build here, at image build
# time, rather than on the developer's machine every time they start.
#
# The uv version is whatever the base image pins (ghcr.io/astral-sh/uv:0.11.1 in
# .devcontainer/devcontainer.json), which is the same version backend/Dockerfile
# ships. Do not install a different uv here: a second resolver would be free to
# re-resolve uv.lock and produce an environment CI never tested.
set -euo pipefail

echo "==> alpha devcontainer: syncing Python dependencies from the committed lock"
cd /app/backend
# --locked so the environment is exactly the lock, not a fresh resolution.
# The dev group IS installed here (unlike a user install) because this container
# is for running the test suite.
uv sync --locked

echo "==> alpha devcontainer: installing frontend dependencies from the committed lockfile"
cd /app/frontend
corepack enable
corepack prepare pnpm@10.26.2 --activate
pnpm install --frozen-lockfile

echo "==> alpha devcontainer: prebuilding the Next.js frontend"
# A standalone server build, because next.config.mjs proxies /api/* to the
# gateway with rewrites() and a rewrite is a server-side feature: the frontend
# cannot be a static export.
NEXT_CONFIG_BUILD_OUTPUT=standalone pnpm build

echo "==> alpha devcontainer: seeding an initial config.yaml if absent"
# Never clobber an existing config: a developer's edits are theirs.
if [ ! -f /app/config.yaml ]; then
  cp /app/config.example.yaml /app/config.yaml
  echo "    created /app/config.yaml (SQLite; no database server required)"
else
  echo "    /app/config.yaml already exists, left untouched"
fi

echo "==> alpha devcontainer: ready"
echo "    Start the stack:  docker compose -f build/compose.installer.yaml --profile alpha-installer up -d"
echo "    Gateway health:   curl http://127.0.0.1:8001/health"
echo "    Web UI:           http://127.0.0.1:3000"
