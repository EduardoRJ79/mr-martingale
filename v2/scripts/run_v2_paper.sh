#!/usr/bin/env bash
# ============================================================
# Run Mr Martingale v2 paper bot
# ============================================================
# This script launches v2 as a background process.
# Logs go to: v2/logs/v2_paper_bot.log  (also stdout in nohup.out)
# State goes to: v2/state/v2_paper_state.json
#
# Usage:
#   bash v2/scripts/run_v2_paper.sh         # background (nohup)
#   bash v2/scripts/run_v2_paper.sh --fg    # foreground (ctrl+c to stop)
#   bash v2/scripts/run_v2_paper.sh --dry-run  # single check, no loop
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MRM_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Source secrets (same as v1)
SECRETS="$HOME/.openclaw/ws-731228/.secrets/hyperliquid.env"
if [[ -f "$SECRETS" ]]; then
    set -a
    source "$SECRETS"
    set +a
fi

cd "$MRM_DIR"

# Ensure log/state dirs exist
mkdir -p "$MRM_DIR/v2/logs" "$MRM_DIR/v2/state"

LOGFILE="$MRM_DIR/v2/logs/v2_paper_bot.log"
PIDFILE="$MRM_DIR/v2/state/v2_paper_bot.pid"
CMD="python3 -m v2.paper_bot"

if [[ "${1:-}" == "--dry-run" ]]; then
    echo "[run_v2_paper] Dry-run check..."
    python3 -m v2.paper_bot --dry-run
    exit 0
fi

if [[ "${1:-}" == "--fg" ]]; then
    echo "[run_v2_paper] Starting in FOREGROUND (Ctrl+C to stop)..."
    exec $CMD
fi

# Background mode
echo "[run_v2_paper] Starting Mr Martingale v2 paper bot in background..."
echo "[run_v2_paper] Logs: $LOGFILE"

nohup $CMD >> "$LOGFILE" 2>&1 &
PID=$!

echo $PID > "$PIDFILE"
echo "[run_v2_paper] PID $PID written to $PIDFILE"
echo "[run_v2_paper] Monitor: tail -f $LOGFILE"
echo "[run_v2_paper] Stop:    kill \$(cat $PIDFILE)"
