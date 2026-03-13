"""
Hyperliquid Public API Client

Async wrapper around Hyperliquid's info API (https://api.hyperliquid.xyz/info).
All public endpoints use POST with JSON body — no auth required.

API Quirks discovered:
- All info endpoints are POST to /info with {"type": "..."} body
- Asset names are case-sensitive (e.g., "BTC", "ETH", not "btc")
- Meta endpoint returns universe[] with asset info including szDecimals
- Clearinghouse state requires a user address
- Funding rates returned as strings, need float conversion
- Rate limits are generous but we throttle to be safe
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hyperliquid.xyz"
INFO_URL = f"{BASE_URL}/info"

# Minimum seconds between requests
RATE_LIMIT_INTERVAL = 0.1


class HyperliquidClient:
    """Async client for Hyperliquid's public info API."""

    def __init__(self, base_url: str = INFO_URL, rate_limit: float = RATE_LIMIT_INTERVAL):
        self._url = base_url
        self._rate_limit = rate_limit
        self._last_request: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HyperliquidClient":
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request = time.monotonic()

    async def _post(self, payload: dict[str, Any]) -> Any:
        if not self._client:
            raise RuntimeError("Client not initialized — use 'async with' context manager")
        await self._throttle()
        logger.debug("POST %s  payload=%s", self._url, payload.get("type"))
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── Public endpoints ──

    async def meta(self) -> dict[str, Any]:
        """Get exchange metadata — asset universe, szDecimals, etc."""
        return await self._post({"type": "meta"})

    async def all_mids(self) -> dict[str, str]:
        """Get current mid prices for all assets. Returns {asset: price_str}."""
        return await self._post({"type": "allMids"})

    async def meta_and_asset_ctxs(self) -> list[Any]:
        """Get meta + per-asset context (funding, open interest, mark price, etc.)."""
        return await self._post({"type": "metaAndAssetCtxs"})

    async def funding_history(self, coin: str, start_time: int, end_time: int | None = None) -> list[dict]:
        """Get historical funding rates for a coin. Times in milliseconds."""
        payload: dict[str, Any] = {"type": "fundingHistory", "coin": coin, "startTime": start_time}
        if end_time is not None:
            payload["endTime"] = end_time
        return await self._post(payload)

    async def clearinghouse_state(self, user: str) -> dict[str, Any]:
        """Get a user's clearinghouse state (positions, leverage, margin)."""
        return await self._post({"type": "clearinghouseState", "user": user})

    async def user_fills(self, user: str) -> list[dict]:
        """Get a user's recent fills."""
        return await self._post({"type": "userFills", "user": user})

    async def open_orders(self, user: str) -> list[dict]:
        """Get a user's open orders."""
        return await self._post({"type": "openOrders", "user": user})

    async def l2_book(self, coin: str) -> dict[str, Any]:
        """Get L2 order book for a coin."""
        return await self._post({"type": "l2Book", "coin": coin})


async def _demo() -> None:
    """Quick demo — fetch meta and mid prices."""
    logging.basicConfig(level=logging.INFO)
    async with HyperliquidClient() as client:
        meta = await client.meta()
        print(f"Universe: {len(meta.get('universe', []))} assets")
        mids = await client.all_mids()
        for coin in ["BTC", "ETH", "SOL", "DOGE"]:
            print(f"  {coin}: ${mids.get(coin, 'N/A')}")


if __name__ == "__main__":
    asyncio.run(_demo())
