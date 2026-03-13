#!/bin/bash
# Show status of all per-coin bots
# Usage: ./status.sh [COIN]
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
MA_DIR="$ROOT_DIR/multi_asset"
PID_DIR="$MA_DIR/state"
STATE_DIR="$MA_DIR/state"

COINS="${1:-ETH SOL XRP}"
COINS=$(echo "$COINS" | tr '[:lower:]' '[:upper:]')

echo "═══════════════════════════════════════════"
echo "  Mr Martingale — Multi-Asset Status"
echo "═══════════════════════════════════════════"
echo ""

for COIN in $COINS; do
    PID_FILE="$PID_DIR/${COIN}.pid"
    STATE_FILE="$STATE_DIR/grid_state_${COIN}.json"

    # Process status
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            STATUS="🟢 RUNNING (PID $PID)"
        else
            STATUS="🔴 DEAD (stale PID $PID)"
        fi
    else
        STATUS="⚪ STOPPED"
    fi

    echo "  $COIN: $STATUS"

    # State
    if [ -f "$STATE_FILE" ]; then
        python3 -c "
import json
with open('$STATE_FILE') as f:
    s = json.load(f)
eq = s.get('equity', '?')
lg = '📈 LONG' if s.get('long_grid') else '—'
sg = '📉 SHORT' if s.get('short_grid') else '—'
updated = s.get('updated_at', '?')[:19]
print(f'    Equity: \${eq}  |  {lg}  |  {sg}  |  Updated: {updated}')
" 2>/dev/null || echo "    (no state)"
    else
        echo "    (no state file)"
    fi
    echo ""
done

# Also show BTC live bot status
BTC_PID=$(pgrep -f "execution.grid_bot" 2>/dev/null || echo "")
if [ -n "$BTC_PID" ]; then
    echo "  BTC: 🟢 LIVE (PID $BTC_PID) — production bot"
else
    echo "  BTC: ⚪ NOT RUNNING"
fi
echo ""
echo "═══════════════════════════════════════════"
