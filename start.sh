#!/usr/bin/env bash
# Alpha — Unified Super-Agent Platform launcher for Linux / macOS / WSL
# Usage: ./start.sh
#
# Optional environment:
#   ALPHA_GATEWAY_PORT      Gateway listen port    (default 8001)
#   ALPHA_FRONTEND_PORT     Frontend listen port   (default 3000)
#   ALPHA_NO_BROWSER=1      Do not open a browser
#   ALPHA_LOG_MAX_BYTES     Log rotation threshold (default 5 MiB)
#   ALPHA_READY_WAIT_SECONDS  Readiness budget     (default 240)
#
# Operability contract (deliberately identical to start.ps1, so one reader
# works for both platforms and nothing here is a Windows-only special case):
#
#   * Logs are rotated before launch, so a long-running container or laptop
#     cannot grow logs/gateway.log without bound. start.ps1 grew a rotator for
#     five files on Windows only; this path had none at all.
#   * Readiness is decided by GET /health/ready, never by GET /health. /health is
#     liveness only - it returns 200 whenever the process is up - so gating on
#     it made this launcher print "Alpha is LIVE!" for a Gateway whose database
#     is unreachable. That is the "Ready but cannot serve" lie, told here.
#   * logs/alpha.pid and logs/alpha_health.json are written on start and cleared
#     or given a terminal status on every exit path, and a status file whose PID
#     is gone is rewritten as "stale". A status file that outlives its process
#     is the most expensive operability bug there is: every later diagnostic
#     trusts it.
#   * A port that is already taken aborts naming the port, the PID and the
#     command line holding it, instead of a generic "failed to start".

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

GATEWAY_PORT="${ALPHA_GATEWAY_PORT:-8001}"
FRONTEND_PORT="${ALPHA_FRONTEND_PORT:-3000}"
LOG_MAX_BYTES="${ALPHA_LOG_MAX_BYTES:-}"
MAX_WAIT_SECONDS="${ALPHA_READY_WAIT_SECONDS:-240}"

LOG_DIR="$REPO_ROOT/logs"
PID_FILE="$LOG_DIR/alpha.pid"
HEALTH_FILE="$LOG_DIR/alpha_health.json"
ROTATE_SCRIPT="$REPO_ROOT/scripts/rotate_logs.py"
PORT_ERROR_FILE="$LOG_DIR/.alpha_port_error"

mkdir -p "$LOG_DIR"

# ── State ───────────────────────────────────────────────────────────────────
# Initialised before any function that references them can run (set -u).
GATEWAY_PID=""
FRONTEND_PID=""
SHUTTING_DOWN=0
LAUNCHER_STARTED_UTC=""

# ── Logging / status-file helpers ───────────────────────────────────────────
now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log_line() { printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" "$2"; }

# Minimal JSON string escaper: the status file must not depend on jq/python.
json_escape() {
    printf '%s' "$1" | tr -d '\r\n' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g'
}

# write_health <status> <detail> [key=value ...]
# Written atomically (temp file + mv) because the watchdog and `make doctor` may
# read this file concurrently and must never see a half-written record. Field
# names match start.ps1 exactly so scripts/deploy_status.py has one schema.
write_health() {
    local status="$1" detail="$2"
    shift 2
    local body pid pair
    pid="${STATUS_PID:-$$}"
    body="{\"pid\":${pid}"
    body="${body},\"status\":\"$(json_escape "$status")\""
    body="${body},\"detail\":\"$(json_escape "$detail")\""
    body="${body},\"timestamp_utc\":\"$(now_utc)\""
    body="${body},\"launcher_started_utc\":\"${LAUNCHER_STARTED_UTC}\""
    body="${body},\"gateway_port\":${GATEWAY_PORT}"
    body="${body},\"frontend_port\":${FRONTEND_PORT}"
    body="${body},\"repo_root\":\"$(json_escape "$REPO_ROOT")\""
    for pair in "$@"; do
        body="${body},\"${pair%%=*}\":\"$(json_escape "${pair#*=}")\""
    done
    body="${body}}"
    printf '%s' "$body" > "${HEALTH_FILE}.$$.tmp"
    mv -f "${HEALTH_FILE}.$$.tmp" "$HEALTH_FILE"
}

remove_state_files() {
    rm -f "$PID_FILE" "$HEALTH_FILE" 2>/dev/null || true
}

# A status file naming a launcher that is not running is a monitoring lie: the
# next reader cannot tell "Alpha is booting" from "Alpha died 40 minutes ago and
# left this behind". Rewrite it as an explicit `stale` record.
mark_dead_status() {
    [ -f "$HEALTH_FILE" ] || return 0
    local recorded previous
    recorded="$(sed -n 's/.*"pid":\([0-9][0-9]*\).*/\1/p' "$HEALTH_FILE" 2>/dev/null | head -n 1)"
    [ -n "$recorded" ] || return 0
    [ "$recorded" = "$$" ] && return 0
    if kill -0 "$recorded" 2>/dev/null; then
        return 0
    fi
    previous="$(sed -n 's/.*"status":"\([^"]*\)".*/\1/p' "$HEALTH_FILE" 2>/dev/null | head -n 1)"
    STATUS_PID="$recorded"
    write_health "stale" \
        "launcher PID ${recorded} is no longer running; this file was left behind by a crash, not by a live launcher (was: ${previous:-unknown})"
    unset STATUS_PID
    log_line WARN "Marked a leftover health file as stale (launcher PID ${recorded} is gone)."
}

# ── Shutdown ────────────────────────────────────────────────────────────────
# cleanup_state <status> - reap children, free ports, leave a truthful (never
# non-terminal) status file. Idempotent, because Ctrl+C runs the SIGINT handler
# and the `exit 0` inside it fires the EXIT trap as well.
cleanup_state() {
    local status="${1:-stopped}"
    if [ "$SHUTTING_DOWN" = "1" ]; then return 0; fi
    SHUTTING_DOWN=1
    trap - SIGINT SIGTERM EXIT

    local pid
    for pid in "$GATEWAY_PID" "$FRONTEND_PID"; do
        [ -n "$pid" ] || continue
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    for pid in "$GATEWAY_PID" "$FRONTEND_PID"; do
        [ -n "$pid" ] || continue
        kill -KILL "$pid" 2>/dev/null || true
    done
    # `uv run` and `next dev` both fork grandchildren that hold the port. Kill
    # the trees or the next start dies with EADDRINUSE and the operator is shown
    # a dead page with no process to blame.
    kill_port_holders "$GATEWAY_PORT"
    kill_port_holders "$FRONTEND_PORT"

    if [ "$status" = "stopped" ]; then
        remove_state_files
    else
        # Keep a terminal record: a "failed" file is the truthful post-mortem,
        # and scripts/deploy_status.py reports it as NOT ready with the reason.
        rm -f "$PID_FILE" 2>/dev/null || true
        local reason=""
        if [ -f "$PORT_ERROR_FILE" ]; then
            reason="$(cat "$PORT_ERROR_FILE")"
        else
            reason="launcher aborted before the stack was ready (see logs/gateway.log)"
        fi
        write_health "$status" "$reason"
    fi
    rm -f "$PORT_ERROR_FILE" 2>/dev/null || true
}

cleanup() {
    printf '\nShutting down Alpha...\n'
    cleanup_state "stopped"
    exit 0
}

# ── Log rotation (bounded, on every platform) ──────────────────────────────
rotate_logs() {
    local py="" candidate
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then py="$candidate"; break; fi
    done
    if [ -z "$py" ]; then
        # Never silently skip: unbounded growth is the thing this prevents.
        log_line WARN "No python3/python on PATH - logs/*.log will NOT be rotated this run."
        log_line WARN "Install python3, or run 'python3 scripts/rotate_logs.py' yourself before long runs."
        return 0
    fi
    local args=("$ROTATE_SCRIPT" --project-root "$REPO_ROOT")
    [ -n "$LOG_MAX_BYTES" ] && args+=(--max-bytes "$LOG_MAX_BYTES")
    if ! "$py" "${args[@]}"; then
        log_line WARN "Log rotation reported failures (see its output); logs may exceed the budget."
    fi
}

# ── Port helpers ───────────────────────────────────────────────────────────
# port_listener_pids <port> - PIDs listening on <port>, across whichever of
# lsof / ss / netstat exists.
port_listener_pids() {
    local port="$1" pids=""
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    fi
    if [ -z "$pids" ] && command -v ss >/dev/null 2>&1; then
        pids="$(ss -lntp 2>/dev/null | awk -v p=":$port\$" '$4 ~ p' \
            | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true)"
    fi
    if [ -z "$pids" ] && command -v netstat >/dev/null 2>&1; then
        pids="$(netstat -lntp 2>/dev/null | awk -v p=":$port\$" '$4 ~ p' \
            | grep -o '[0-9][0-9]*/[A-Za-z_]*' | cut -d/ -f1 | sort -u || true)"
    fi
    printf '%s' "$pids"
}

# describe_port_holders <port> - "PID <n>: <command line>" per holder, to the
# given stream. An operator must never have to read this script to learn which
# port is taken and by what.
describe_port_holders() {
    local port="$1" pid cmd
    for pid in $(port_listener_pids "$port"); do
        [ -n "$pid" ] || continue
        cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
        [ -n "$cmd" ] || cmd="(command line unavailable)"
        printf '    PID %s: %s\n' "$pid" "$cmd"
    done
}

kill_port_holders() {
    local port="$1" pids pid
    if command -v fuser >/dev/null 2>&1; then
        fuser -k "${port}/tcp" >/dev/null 2>&1 || true
    fi
    pids="$(port_listener_pids "$port")"
    for pid in $pids; do
        [ -n "$pid" ] || continue
        kill -TERM "$pid" 2>/dev/null || true
    done
    sleep 1
    pids="$(port_listener_pids "$port")"
    for pid in $pids; do
        [ -n "$pid" ] || continue
        kill -KILL "$pid" 2>/dev/null || true
    done
}

free_port_or_exit() {
    local port="$1" label="$2" holders
    holders="$(port_listener_pids "$port")"
    [ -z "$holders" ] && return 0
    log_line INFO "Port $port ($label) is in use; terminating the holder(s)."
    printf '  Port %s (%s) is held by:\n' "$port" "$label"
    describe_port_holders "$port" || true
    kill_port_holders "$port"
    holders="$(port_listener_pids "$port")"
    if [ -n "$holders" ]; then
        printf '  Port %s (%s) is STILL in use and could not be freed.\n' "$port" "$label" >&2
        describe_port_holders "$port" >&2 || true
        {
            printf 'port %s (%s) is already in use and could not be freed. Holders:\n' "$port" "$label"
            describe_port_holders "$port"
            printf 'Stop that program and re-run ./start.sh\n'
        } > "$PORT_ERROR_FILE"
        cleanup_state "failed"
        exit 1
    fi
    return 0
}

# ── Start ───────────────────────────────────────────────────────────────────
echo -e "\033[1;36m========================================================\033[0m"
echo -e "\033[1;36m    Alpha — Unified Super-Agent Platform     \033[0m"
echo -e "\033[1;36m========================================================\033[0m"

LAUNCHER_STARTED_UTC="$(now_utc)"
trap cleanup SIGINT SIGTERM
trap 'cleanup_state "stopped"' EXIT

# 1. Prerequisites. Every abort below records a terminal status first: a launcher
#    that exits without touching its own status file leaves whatever the previous
#    run wrote behind, and that is how "status: starting" outlives its process.
fail_startup() {
    printf '%s\n' "$1" > "$PORT_ERROR_FILE"
    log_line ERROR "$1"
    cleanup_state "failed"
    exit 1
}

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
fi

if ! command -v node >/dev/null 2>&1; then
    fail_startup "dependency missing: Node.js (v22+) not found on PATH - install from https://nodejs.org/"
fi

NODE_MAJOR="$(node -v 2>/dev/null | sed 's/^v//' | cut -d. -f1 || true)"
if [ -z "$NODE_MAJOR" ] || [ "$NODE_MAJOR" -lt 22 ] 2>/dev/null; then
    fail_startup "dependency missing: Node.js v22+ is required, found $(node -v 2>/dev/null || echo 'unknown') - install from https://nodejs.org/"
fi

# 2. Config files
if [ ! -f .env ]; then
    cp .env.example .env 2>/dev/null || touch .env
fi
if [ ! -f frontend/.env ]; then
    echo "NODE_ENV=development" > frontend/.env
fi
if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml 2>/dev/null || true
fi
if [ ! -f extensions_config.json ]; then
    cp extensions_config.example.json extensions_config.json 2>/dev/null || echo "{}" > extensions_config.json
fi

export AGENT_WORKSPACE_AUTH_DISABLED=1
export AGENT_WORKSPACE_INTERNAL_GATEWAY_BASE_URL="http://127.0.0.1:${GATEWAY_PORT}"
export PORT="$FRONTEND_PORT"
export PYTHONPATH=.

# 3. Bounded logs, then claim the ports
rotate_logs
printf '%s' "$$" > "${PID_FILE}.$$.tmp"
mv -f "${PID_FILE}.$$.tmp" "$PID_FILE"
mark_dead_status
free_port_or_exit "$GATEWAY_PORT" "Gateway API"
free_port_or_exit "$FRONTEND_PORT" "Frontend UI"

# 4. Start Gateway
echo -e "\033[1;33m[1/2] Starting Gateway API on port ${GATEWAY_PORT}...\033[0m"
cd "$REPO_ROOT/backend"
uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port "$GATEWAY_PORT" \
    > "$LOG_DIR/gateway.log" 2>&1 &
GATEWAY_PID=$!

# 5. Start Frontend
#    NB: the old `node scripts/dev.mjs` referenced a file that never existed
#    in this repo, so the frontend never started on Unix. Invoke the same binary
#    start.ps1 uses (works with npm and pnpm node_modules layouts).
echo -e "\033[1;33m[2/2] Starting Frontend UI on port ${FRONTEND_PORT}...\033[0m"
cd "$REPO_ROOT/frontend"
node node_modules/next/dist/bin/next dev -p "$FRONTEND_PORT" > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
cd "$REPO_ROOT"

write_health "starting" "launcher initialising; waiting for gateway readiness"

# 6. Wait for readiness. /health/ready probes the persistence backends, so it is
#    the only route that can answer "can this serve a run?". /health returns 200
#    for a Gateway whose database is down, and gating on it is what made this
#    launcher announce a dead product as LIVE.
echo "Waiting for services to become ready (GET /health/ready)..."
GW_OK=0
GW_REASON="timed out after ${MAX_WAIT_SECONDS}s (ALPHA_READY_WAIT_SECONDS)"
elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT_SECONDS" ]; do
    body="$(curl -s -m 5 -w '\n%{http_code}' "http://127.0.0.1:${GATEWAY_PORT}/health/ready" 2>/dev/null || true)"
    code="$(printf '%s' "$body" | tail -n 1)"
    payload="$(printf '%s' "$body" | sed '$d')"
    if [ "$code" = "200" ]; then
        GW_OK=1
        GW_REASON="HTTP 200 ${payload}"
        break
    fi
    if [ "$code" = "503" ]; then
        # The Gateway is up and reporting it cannot serve. Surface that reason
        # verbatim instead of looping to the timeout and calling it "not healthy".
        GW_REASON="HTTP 503 - Gateway up but NOT ready: ${payload}"
        break
    fi
    if [ -z "$code" ] || [ "$code" = "000" ]; then
        if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
            GW_REASON="the Gateway process (PID ${GATEWAY_PID}) exited; see logs/gateway.log"
            break
        fi
        GW_REASON="nothing is listening on port ${GATEWAY_PORT} yet (elapsed ${elapsed}s)"
    else
        GW_REASON="unexpected HTTP ${code} from /health/ready: ${payload}"
    fi
    write_health "starting" "waiting for gateway readiness: ${GW_REASON}" \
        "phase=boot_wait" "elapsed_seconds=${elapsed}" "gateway_ready=false" \
        "frontend_ready=false" "gateway_last_error=${GW_REASON}"
    sleep 2
    elapsed=$((elapsed + 2))
done

# Frontend: liveness only (it exposes no readiness route), reported separately so
# a dead UI is never hidden behind a healthy Gateway.
FE_OK=0
if curl -s -o /dev/null -m 5 -f "http://127.0.0.1:${FRONTEND_PORT}/" 2>/dev/null; then
    FE_OK=1
    FE_REASON="HTTP 200 on port ${FRONTEND_PORT}"
else
    FE_REASON="not serving HTTP 200 on port ${FRONTEND_PORT} (see logs/frontend.log)"
fi

if [ "$GW_OK" = "1" ] && [ "$FE_OK" = "1" ]; then
    write_health "healthy" "gateway ready and frontend serving" \
        "phase=ready" "gateway_ready=true" "frontend_ready=true" "elapsed_seconds=${elapsed}"
    echo -e "\033[1;32m========================================================\033[0m"
    echo -e "\033[1;32m   Alpha is LIVE! Access at: http://localhost:${FRONTEND_PORT}   \033[0m"
    echo -e "\033[1;32m========================================================\033[0m"
else
    # Always record *both* reasons. The gateway-only branch used to fall back to
    # a generic message when the frontend was the one that failed, which is the
    # exact "generic error" this launcher was supposed to stop producing.
    printf 'not ready: gateway: %s; frontend: %s. Logs: logs/gateway.log, logs/frontend.log\n' \
        "$GW_REASON" "$FE_REASON" > "$PORT_ERROR_FILE"
    echo -e "\033[1;31m========================================================\033[0m"
    echo -e "\033[1;31m   Alpha is NOT ready - it will NOT be reported healthy.  \033[0m"
    echo -e "\033[1;31m   gateway:  ${GW_REASON}\033[0m"
    echo -e "\033[1;31m   frontend: ${FE_REASON}\033[0m"
    echo -e "\033[1;31m   logs:     logs/gateway.log, logs/frontend.log          \033[0m"
    echo -e "\033[1;31m========================================================\033[0m"
    # Reap the children and leave a terminal status behind: a failed launcher
    # must not leave a "starting" claim for the next reader to believe.
    cleanup_state "failed"
    exit 1
fi
echo "Press [Ctrl+C] to stop all services."

if [ "${ALPHA_NO_BROWSER:-0}" != "1" ]; then
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1 &
    fi
fi

wait
