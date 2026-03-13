"""
Order Book (L2) Snapshot Collector — Hyperliquid

Periodically snapshots the L2 order book for configured assets.
Saves to intelligence/data/live/orderbook/ as compressed JSONL.

Usage:
    PYTHONPATH=. .venv/bin/python intelligence/orderbook_collector.py

Parameters:
    --interval 60    Seconds between snapshots (default: 60)
    --depth 20       Number of price levels per side (default: 20)

Data format per line:
    {"timestamp_ms": int, "coin": str, "mid_price": float,
     "bid_vol_total": float, "ask_vol_total": float,
     "imbalance": float,  # (bid-ask)/(bid+ask), range -1 to +1
     "spread_bps": float,
     "bids": [{"price": float, "size": float, "n_orders": int}, ...],
     "asks": [{"price": float, "size": float, "n_orders": int}, ...]}
"""
import argparse
import gzip
import json
import logging
import signal as sig_mod
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx")
    sys.exit(1)

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"
DATA_DIR = Path(__file__).parent / "data" / "live" / "orderbook"
COINS = ["BTC", "ETH", "SOL"]
ROTATE_INTERVAL = 3600


class OrderBookCollector:
    def __init__(self, coins=None, interval=60, depth=20):
        self.coins = coins or COINS
        self.interval = interval
        self.depth = depth
        self.running = True
        self.current_file = None
        self.current_path = None
        self.file_start = 0
        self.snap_count = 0
        self.client = httpx.Client(timeout=15.0)
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _rotate_file(self):
        if self.current_file:
            self.current_file.close()
            logger.info("Closed %s (%d snapshots)", self.current_path.name, self.snap_count)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.current_path = DATA_DIR / f"l2_{ts}.jsonl.gz"
        self.current_file = gzip.open(self.current_path, "wt")
        self.file_start = time.time()
        self.snap_count = 0
        logger.info("Opened %s", self.current_path.name)

    def _fetch_l2(self, coin):
        resp = self.client.post(INFO_URL, json={
            "type": "l2Book", "coin": coin, "nSigFigs": 5
        })
        resp.raise_for_status()
        return resp.json()

    def _snapshot_coin(self, coin):
        try:
            book = self._fetch_l2(coin)
            levels = book.get("levels", [[], []])
            if len(levels) < 2:
                return None

            bids_raw = levels[0][:self.depth]
            asks_raw = levels[1][:self.depth]

            bids = [{"price": float(b["px"]), "size": float(b["sz"]),
                     "n_orders": int(b["n"])} for b in bids_raw]
            asks = [{"price": float(a["px"]), "size": float(a["sz"]),
                     "n_orders": int(a["n"])} for a in asks_raw]

            bid_vol = sum(b["size"] for b in bids)
            ask_vol = sum(a["size"] for a in asks)
            total = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / total if total > 0 else 0

            best_bid = bids[0]["price"] if bids else 0
            best_ask = asks[0]["price"] if asks else 0
            mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
            spread_bps = ((best_ask - best_bid) / mid * 10000) if mid > 0 else 0

            return {
                "timestamp_ms": int(time.time() * 1000),
                "coin": coin,
                "mid_price": round(mid, 2),
                "bid_vol_total": round(bid_vol, 4),
                "ask_vol_total": round(ask_vol, 4),
                "imbalance": round(imbalance, 4),
                "spread_bps": round(spread_bps, 2),
                "bids": bids,
                "asks": asks,
            }
        except Exception as e:
            logger.warning("Failed to snapshot %s: %s", coin, e)
            return None

    def _write(self, record):
        if self.current_file is None or time.time() - self.file_start > ROTATE_INTERVAL:
            self._rotate_file()
        self.current_file.write(json.dumps(record) + "\n")
        self.snap_count += 1

    def run(self):
        logger.info("Starting L2 collector: %s, interval=%ds, depth=%d",
                     self.coins, self.interval, self.depth)
        while self.running:
            for coin in self.coins:
                snap = self._snapshot_coin(coin)
                if snap:
                    self._write(snap)
                    logger.debug("%s imb=%.3f spread=%.1fbps", coin, snap["imbalance"], snap["spread_bps"])
            if self.current_file:
                self.current_file.flush()
            time.sleep(self.interval)

    def stop(self):
        self.running = False
        if self.current_file:
            self.current_file.close()
        self.client.close()
        logger.info("Stopped. Total snapshots: %d", self.snap_count)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="L2 Order Book Collector")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--depth", type=int, default=20)
    args = parser.parse_args()

    collector = OrderBookCollector(interval=args.interval, depth=args.depth)

    def handle_sig(s, f):
        collector.stop()
        sys.exit(0)

    sig_mod.signal(sig_mod.SIGINT, handle_sig)
    sig_mod.signal(sig_mod.SIGTERM, handle_sig)
    collector.run()


if __name__ == "__main__":
    main()
