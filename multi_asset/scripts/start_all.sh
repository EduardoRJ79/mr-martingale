#!/bin/bash
# Start all per-coin paper bots
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for COIN in ETH SOL XRP; do
    bash "$SCRIPT_DIR/start.sh" "$COIN" "$@"
    sleep 2
done
