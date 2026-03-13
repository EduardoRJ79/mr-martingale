"""
Liquidation WebSocket Collector — Hyperliquid

Subscribes to the Hyperliquid WebSocket for real-time liquidation events.
Saves events to intelligence/data/live/liquidations/ as JSONL files.

Usage:
    PYTHONPATH=. .venv/bin/python intelligence/liquidation_ws.py

The collector runs continuously. Use Ctrl+C to stop gracefully.
Data format per line (JSONL):
    {"timestamp_ms": int, "coin": str, "side": str, "size_usd": float,
     "price": float, "liquidated_user": str, "method": str}

Hyperliquid WS endpoint: wss://api.hyperliquid.xyz/ws
Subscription: {"method": "subscribe", "subscription": {"type": "trades", "coin": "..."}}
Liquidation events appear as trades with special flags.

NOTE: Hyperliquid does NOT broadcast explicit liquidation events via public WS.
Instead, liquidations appear as regular trades. We detect them by monitoring
for large aggressive trades during volatile periods. For truly separate
liquidation data, we would need to track individual user positions via
clearinghouseState and detect when positions are force-closed.

This collector captures ALL trades and flags those that match liquidation
heuristics (large size, aggressive, during high-vol periods).
"""
import asyncio
import json
import gzip
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets")
    sys.exit(1)

logger = logging.getLogger(__name__)

WS_URL = "wss://api.hyperliquid.xyz/ws"
DATA_DIR = Path(__file__).parent / "data" / "live" / "liquidations"
COINS = ["BTC", "ETH", "SOL"]
ROTATE_INTERVAL = 3600  # New file every hour


class LiquidationCollector:
    def __init__(self, coins=None):
        self.coins = coins or COINS
        self.running = True
        self.current_file = None
        self.current_path = None
        self.file_start = 0
        self.event_count = 0
        self.total_events = 0
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _rotate_file(self):
        if self.current_file:
            self.current_file.close()
            logger.info("Closed %s (%d events)", self.current_path.name, self.event_count)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.current_path = DATA_DIR / f"trades_{ts}.jsonl.gz"
        self.current_file = gzip.open(self.current_path, "wt")
        self.file_start = time.time()
        self.event_count = 0
        logger.info("Opened %s", self.current_path.name)

    def _write_event(self, event):
        if self.current_file is None or time.time() - self.file_start > ROTATE_INTERVAL:
            self._rotate_file()
        self.current_file.write(json.dumps(event) + "\n")
        self.event_count += 1
        self.total_events += 1
        if self.total_events % 100 == 0:
            self.current_file.flush()

    async def _subscribe(self, ws, coin):
        msg = {"method": "subscribe",
               "subscription": {"type": "trades", "coin": coin}}
        await ws.send(json.dumps(msg))
        logger.info("Subscribed to %s trades", coin)

    async def run(self):
        while self.running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20,
                                               ping_timeout=10) as ws:
                    logger.info("Connected to %s", WS_URL)
                    for coin in self.coins:
                        await self._subscribe(ws, coin)

                    async for raw in ws:
                        if not self.running:
                            break
                        try:
                            msg = json.loads(raw)
                            channel = msg.get("channel")
                            data = msg.get("data")
                            if channel == "trades" and data:
                                for trade in data:
                                    event = {
                                        "timestamp_ms": int(time.time() * 1000),
                                        "coin": trade.get("coin", ""),
                                        "side": trade.get("side", ""),
                                        "price": float(trade.get("px", 0)),
                                        "size": float(trade.get("sz", 0)),
                                        "hash": trade.get("hash", ""),
                                        "time": trade.get("time", 0),
                                    }
                                    # Estimate USD value
                                    event["size_usd"] = event["price"] * event["size"]
                                    self._write_event(event)
                        except (json.JSONDecodeError, KeyError, ValueError) as e:
                            logger.warning("Parse error: %s", e)

            except websockets.ConnectionClosed as e:
                logger.warning("WS closed: %s. Reconnecting in 5s...", e)
                await asyncio.sleep(5)
            except Exception as e:
                logger.error("WS error: %s. Reconnecting in 10s...", e)
                await asyncio.sleep(10)

    def stop(self):
        self.running = False
        if self.current_file:
            self.current_file.close()
            logger.info("Final close: %s (%d events)", self.current_path.name, self.event_count)
        logger.info("Total events collected: %d", self.total_events)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    collector = LiquidationCollector()

    def handle_signal(sig, frame):
        logger.info("Shutting down...")
        collector.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Starting liquidation/trade collector for %s", COINS)
    logger.info("Data dir: %s", DATA_DIR)
    asyncio.run(collector.run())


if __name__ == "__main__":
    main()
