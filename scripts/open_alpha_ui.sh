#!/usr/bin/env bash
# open_alpha_ui.sh - make a successfully started Alpha visible.
#
# Usage: ./scripts/open_alpha_ui.sh <url> [timeout_seconds]
#
# Why this exists
# ---------------
# A stack that is "running" and a user who can see it running are different
# facts. `make dev` used to bring the whole system up, print a banner, and stop
# there: nothing on screen proved the thing the user asked for actually worked.
# The one signal available was "is a TCP port bound", which is not the same
# claim - the Next.js dev server binds :3000 and then needs roughly 50 seconds to
# compile `/` (measured on this checkout). Opening a browser at port-bind time
# shows a connection error at the exact moment the user is looking for proof, so
# the naive fix is worse than none.
#
# So this waits for the entry point to actually *serve* an HTTP 200, and only
# then opens anything. A timeout is reported honestly and is NOT fatal: a
# machine with no browser, or a UI that is slow to compile past the deadline,
# must not take down a stack that is otherwise healthy (the same reasoning as
# nginx being optional in serve.sh).
#
# Environment
# -----------
#   ALPHA_NO_BROWSER=1        never open a browser (headless/CI/remote boxes)
#   ALPHA_LAUNCH_DESKTOP=1   also launch the Electron desktop app
#   ALPHA_DESKTOP_DIR        override the Electron project directory
#
# Exit codes
# ----------
#   0  the entry point served, and everything requested was opened (or skipped
#      by an opt-out / genuinely unavailable opener)
#   1  the entry point never served within the timeout
#   2  bad usage

set -u

URL="${1:-}"
TIMEOUT="${2:-180}"

if [ -z "$URL" ]; then
    echo "usage: $0 <url> [timeout_seconds]" >&2
    exit 2
fi

case "$TIMEOUT" in
    ''|*[!0-9]*)
        echo "timeout must be a whole number of seconds, got '$TIMEOUT'" >&2
        exit 2
        ;;
esac

# ── Platform detection ───────────────────────────────────────────────────────
# Git Bash on Windows is a POSIX shell on a Windows host: it has no `xdg-open`
# and no `python3`, so the opener and the probe both need a Windows branch.
# `uname` reports MINGW/MSYS/CYGWIN there, not Linux.
case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT) PLATFORM="windows" ;;
    Darwin) PLATFORM="macos" ;;
    *) PLATFORM="linux" ;;
esac

# ── HTTP probe ───────────────────────────────────────────────────────────────
# Returns the status code as stdout, or nothing when the request could not be
# made at all. `000` is curl's own "no response" value, not a real status.
http_status() {
    local url="$1" budget="$2" code=""
    if command -v curl >/dev/null 2>&1; then
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time "$budget" "$url" 2>/dev/null) || code=""
    fi
    if [ -z "$code" ] || [ "$code" = "000" ]; then
        if [ "$PLATFORM" = "windows" ] && command -v powershell.exe >/dev/null 2>&1; then
            # Non-200 must come back as a number, not an exception, so a real
            # 503 is distinguishable from "could not connect".
            code=$(ALPHA_UI_PROBE_URL="$url" ALPHA_UI_PROBE_TIMEOUT="$budget" powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command '
                try {
                    $r = Invoke-WebRequest -Uri $env:ALPHA_UI_PROBE_URL -UseBasicParsing -TimeoutSec ([int]$env:ALPHA_UI_PROBE_TIMEOUT) -ErrorAction Stop
                    Write-Output $r.StatusCode
                } catch {
                    if ($_.Exception.Response) { Write-Output ([int]$_.Exception.Response.StatusCode) } else { Write-Output "" }
                }
            ' 2>/dev/null | tr -d '\r' | head -1) || code=""
        fi
    fi
    printf '%s' "$code"
}

echo "Waiting for $URL to serve (up to ${TIMEOUT}s)..."
deadline=$(( $(date +%s) + TIMEOUT ))
last_note=""
while :; do
    code=$(http_status "$URL" 15)
    if [ "$code" = "200" ]; then
        echo "  OK $URL is serving."
        SERVED=1
        break
    fi
    if [ -n "$code" ] && [ "$code" != "000" ] && [ "$code" != "$last_note" ]; then
        # Only speak up when the answer changes, so a slow compile does not
        # produce a wall of identical "000" lines.
        echo "  ... $URL answered $code, waiting."
        last_note="$code"
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        break
    fi
    sleep 2
done

if [ "${SERVED:-0}" != "1" ]; then
    echo ""
    echo "  ! $URL did not answer 200 within ${TIMEOUT}s."
    echo "    The stack may still be starting, or the UI may have failed."
    echo "    Check logs/frontend.log and logs/nginx.log, then open $URL yourself."
    exit 1
fi

# ── Open the browser ─────────────────────────────────────────────────────────
opened_browser=0
if [ "${ALPHA_NO_BROWSER:-0}" = "1" ]; then
    echo "  ALPHA_NO_BROWSER=1 - not opening a browser."
elif [ "$PLATFORM" = "windows" ]; then
    # `start` would be ambiguous in cmd.exe; Start-Process is the reliable one.
    if powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \
        "Start-Process '$URL'" >/dev/null 2>&1; then
        echo "  Opened $URL in your default browser."
        opened_browser=1
    else
        echo "  Could not open a browser automatically. Open $URL yourself."
    fi
elif [ "$PLATFORM" = "macos" ]; then
    if command -v open >/dev/null 2>&1 && open "$URL" >/dev/null 2>&1; then
        echo "  Opened $URL in your default browser."
        opened_browser=1
    else
        echo "  Could not open a browser automatically. Open $URL yourself."
    fi
else
    if command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1; then
        echo "  Opened $URL in your default browser."
        opened_browser=1
    else
        echo "  No xdg-open available. Open $URL yourself."
    fi
fi

# ── Optionally launch the desktop app ────────────────────────────────────────
# Opt-in, because Electron is a heavyweight second window and most runs only
# need the browser. When it is requested but unavailable, that is reported and
# not treated as a failure - the browser above is already serving the user.
if [ "${ALPHA_LAUNCH_DESKTOP:-0}" = "1" ]; then
    here=$(cd "$(dirname "$0")/.." && pwd)
    desktop_dir="${ALPHA_DESKTOP_DIR:-$here/electron}"
    if [ ! -d "$desktop_dir" ]; then
        echo "  ALPHA_LAUNCH_DESKTOP=1 but no Electron project at $desktop_dir."
    elif command -v npm >/dev/null 2>&1; then
        (cd "$desktop_dir" && npm start >/dev/null 2>&1 &) \
            && echo "  Launched the Alpha desktop app." \
            || echo "  Could not launch the desktop app. Try: cd electron && npm start"
    else
        echo "  ALPHA_LAUNCH_DESKTOP=1 but npm is not on PATH. Try: cd electron && npm start"
    fi
fi

exit 0
