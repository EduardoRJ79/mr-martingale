#!/bin/bash
# Start a per-coin paper trade bot
# Usage: ./start.sh ETH [--dry-run]
set -euo pipefail

COIN="${1:?Usage: ./start.sh COIN [--dry-run]}"
COIN_UPPER=$(echo "$COIN" | tr '[:lower:]' '[:upper:]')
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MA_DIR="$ROOT_DIR/multi_asset"
PID_DIR="$MA_DIR/state"
LOG_DIR="$MA_DIR/logs"

PID_FILE="$PID_DIR/${COIN_UPPER}.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "❌ ${COIN_UPPER} bot already running (PID $PID)"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

echo "🚀 Starting ${COIN_UPPER} paper trade bot..."

cd "$ROOT_DIR"
source .venv/bin/activate 2>/dev/null || true

EXTRA_ARGS=""
if [ "${2:-}" = "--dry-run" ]; then
    EXTRA_ARGS="--dry-run"
fi

PYTHONPATH="$ROOT_DIR" nohup python3 -m multi_asset.coin_runner "$COIN_UPPER" $EXTRA_ARGS \
    >> "$LOG_DIR/grid_bot_${COIN_UPPER}.log" 2>&1 &

echo $! > "$PID_FILE"
echo "✅ ${COIN_UPPER} started (PID $(cat $PID_FILE))"
echo "   Log: $LOG_DIR/grid_bot_${COIN_UPPER}.log"
