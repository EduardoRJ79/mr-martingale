#!/bin/bash
# Stop all per-coin paper bots
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for COIN in ETH SOL XRP; do
    bash "$SCRIPT_DIR/stop.sh" "$COIN"
done
