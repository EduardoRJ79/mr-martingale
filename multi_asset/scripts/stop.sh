#!/bin/bash
# Stop a per-coin paper trade bot
# Usage: ./stop.sh ETH
set -euo pipefail

COIN="${1:?Usage: ./stop.sh COIN}"
COIN_UPPER=$(echo "$COIN" | tr '[:lower:]' '[:upper:]')
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PID_FILE="$ROOT_DIR/multi_asset/state/${COIN_UPPER}.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "⚠️  No PID file for ${COIN_UPPER}"
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    echo "🛑 Stopped ${COIN_UPPER} (PID $PID)"
else
    echo "⚠️  Process $PID not running"
fi
rm -f "$PID_FILE"
