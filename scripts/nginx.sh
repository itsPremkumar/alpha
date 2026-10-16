#!/usr/bin/env bash
#
# nginx.sh — Start nginx alone in the foreground with the local dev config.
#
# Mirrors how scripts/serve.sh launches nginx (same resolution order, prefix,
# config, and pre-created directories) — keep the two in sync.
#
# Usage: make nginx  (or ./scripts/nginx.sh from anywhere)

set -e

REPO_ROOT="$(builtin cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
cd "$REPO_ROOT"

mkdir -p logs
mkdir -p temp/client_body_temp temp/proxy_temp temp/fastcgi_temp temp/uwsgi_temp temp/scgi_temp

# Resolve nginx exactly as serve.sh does: project-local copy first (the pinned
# build install.ps1 provisions into .tools/, which is deliberately NOT put on
# PATH), then PATH. A bare `nginx` here died with "command not found" on a
# machine whose only nginx was the provisioned one, so `make nginx` could not
# start the entry point that `make dev` would have started.
NGINX_BIN=""
for _cand in "$REPO_ROOT/.tools/nginx/nginx.exe" "$REPO_ROOT/.tools/nginx/sbin/nginx.exe" nginx; do
    if command -v "$_cand" >/dev/null 2>&1; then NGINX_BIN="$_cand"; break; fi
done
if [ -z "$NGINX_BIN" ]; then
    echo "✗ nginx not found." >&2
    echo "  Looked for .tools/nginx/nginx.exe (installer-provisioned), .tools/nginx/sbin/nginx.exe, and 'nginx' on PATH." >&2
    echo "  Windows: run install.ps1 (or install.bat) to provision the pinned nginx into .tools/." >&2
    exit 1
fi

exec "$NGINX_BIN" -g 'daemon off;' -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT"
