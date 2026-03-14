"""
client.py: MacroDash API client for EarningsLens.

Fetches technical indicators, stock detail, economic data, sentiment, and news
concurrently via httpx.AsyncClient. Caches results in Redis with a 5-minute TTL.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx
import redis
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MACRODASH_BASE_URL = os.getenv(
    "MACRODASH_BASE_URL",
    "https://macrodash-server.5249c0fmwzjkc.us-east-1.cs.amazonlightsail.com",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MACRODASH_TTL = 300  # 5 minutes

CACHE_KEYS = [
    "technical_indicators",
    "stock_detail",
    "economic_data",
    "sentiment",
    "news",
]


class MacroDashClient:
    """
    Async HTTP client for the MacroDash financial data API.

    All fetch methods use httpx.AsyncClient. Results are cached in Redis
    under keys: macrodash:{session_id}:{data_type}
    """

    def __init__(self, timeout: float = 30.0):
        self.base_url = MACRODASH_BASE_URL.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Individual fetchers
    # ------------------------------------------------------------------

    async def fetch_technical_indicators(self, symbol: str, period: str = "3mo") -> dict:
        """
        GET /api/technical-indicators/{symbol}/?period={period}

        Returns RSI, MACD signal/histogram, Bollinger Bands (upper/mid/lower),
        SMA 20/50/200, EMA 12/26/50.
        """
        url = f"{self.base_url}/api/technical-indicators/{symbol}/"
        params = {"period": period}
        return await self._get(url, params=params)

    async def fetch_stock_detail(self, symbol: str) -> dict:
        """GET /api/stocks/{symbol}/"""
        url = f"{self.base_url}/api/stocks/{symbol}/"
        return await self._get(url)

    async def fetch_economic_data(self) -> dict:
        """
        GET /api/economic-data/

        Returns GDP, unemployment, inflation, consumer spending, interest rates
        (sourced from FRED).
        """
        url = f"{self.base_url}/api/economic-data/"
        return await self._get(url)

    async def fetch_sentiment(self, symbol: str) -> dict:
        """GET /api/sentiment/{symbol}/"""
        url = f"{self.base_url}/api/sentiment/{symbol}/"
        return await self._get(url)

    async def fetch_news(self, symbol: str) -> dict:
        """GET /api/news/{symbol}/"""
        url = f"{self.base_url}/api/news/{symbol}/"
        return await self._get(url)

    # ------------------------------------------------------------------
    # Concurrent prefetch
    # ------------------------------------------------------------------

    async def prefetch_all(self, symbol: str) -> dict:
        """
        Fire all five fetches concurrently via asyncio.gather.

        Returns:
            {
                "technical_indicators": {...},
                "stock_detail": {...},
                "economic_data": {...},
                "sentiment": {...},
                "news": {...},
            }
        """
        symbol = symbol.upper()
        results = await asyncio.gather(
            self.fetch_technical_indicators(symbol),
            self.fetch_stock_detail(symbol),
            self.fetch_economic_data(),
            self.fetch_sentiment(symbol),
            self.fetch_news(symbol),
            return_exceptions=True,
        )

        output: dict[str, Any] = {}
        for key, result in zip(CACHE_KEYS, results):
            if isinstance(result, Exception):
                logger.warning("MacroDash fetch failed for '%s': %s", key, result)
                output[key] = {}
            else:
                output[key] = result

        return output

    # ------------------------------------------------------------------
    # Redis cache helpers
    # ------------------------------------------------------------------

    def cache_to_redis(self, session_id: str, symbol: str, data: dict) -> None:
        """
        Store each data key separately in Redis with MACRODASH_TTL (5 min).

        Keys: macrodash:{session_id}:{data_type}
        Also stores a metadata key: macrodash:{session_id}:meta
        """
        client = self._get_redis()
        for key in CACHE_KEYS:
            redis_key = f"macrodash:{session_id}:{key}"
            payload = data.get(key, {})
            try:
                client.set(redis_key, json.dumps(payload), ex=MACRODASH_TTL)
            except Exception as exc:
                logger.warning("Failed to cache %s to Redis: %s", redis_key, exc)

        # Store metadata so we know what was cached and for which symbol
        meta_key = f"macrodash:{session_id}:meta"
        try:
            client.set(
                meta_key,
                json.dumps({"symbol": symbol, "cached_keys": CACHE_KEYS}),
                ex=MACRODASH_TTL,
            )
        except Exception as exc:
            logger.warning("Failed to cache meta to Redis: %s", exc)

    def get_cached(self, session_id: str, key: str) -> dict | None:
        """
        Retrieve a single cached entry from Redis.

        Returns None if the key is missing or expired.
        """
        client = self._get_redis()
        redis_key = f"macrodash:{session_id}:{key}"
        try:
            raw = client.get(redis_key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Redis get failed for %s: %s", redis_key, exc)
            return None

    def get_all_cached(self, session_id: str) -> dict:
        """
        Retrieve all cached MacroDash data for a session.

        Returns a dict with keys from CACHE_KEYS; missing/expired keys are {}.
        """
        return {key: (self.get_cached(session_id, key) or {}) for key in CACHE_KEYS}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: dict | None = None) -> dict:
        """Perform a GET request and return the parsed JSON body."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "MacroDash HTTP error %s for %s: %s",
                    exc.response.status_code,
                    url,
                    exc,
                )
                return {}
            except httpx.RequestError as exc:
                logger.warning("MacroDash request error for %s: %s", url, exc)
                return {}

    @staticmethod
    def _get_redis() -> redis.Redis:
        return redis.from_url(REDIS_URL, decode_responses=True)
