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
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MACRODASH_BASE_URL = os.getenv(
    "MACRODASH_BASE_URL",
    "https://macrodash-server.5249c0fmwzjkc.us-east-1.cs.amazonlightsail.com",
)

# In-memory cache (replaces Redis)
_cache: dict[str, str] = {}

CACHE_KEYS = [
    "technical_indicators",
    "stock_detail",
    "economic_data",
    "sentiment",
    "news",
]


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _payload_data(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


class MacroDashClient:
    """
    Async HTTP client for the MacroDash financial data API.

    All fetch methods use httpx.AsyncClient. Results are cached in Redis
    under keys: macrodash:{session_id}:{data_type}
    """

    def __init__(self, timeout: float = 10.0):
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
        import asyncio as _asyncio
        try:
            results = await _asyncio.wait_for(
                asyncio.gather(
                    self.fetch_technical_indicators(symbol),
                    self.fetch_stock_detail(symbol),
                    self.fetch_economic_data(),
                    self.fetch_sentiment(symbol),
                    self.fetch_news(symbol),
                    return_exceptions=True,
                ),
                timeout=25.0,
            )
        except _asyncio.TimeoutError:
            logger.warning("MacroDash prefetch timed out for %s — returning empty data", symbol)
            return {k: {} for k in CACHE_KEYS}

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
        for key in CACHE_KEYS:
            _cache[f"macrodash:{session_id}:{key}"] = json.dumps(data.get(key, {}))
        _cache[f"macrodash:{session_id}:meta"] = json.dumps(
            {"symbol": symbol, "cached_keys": CACHE_KEYS}
        )

    def get_cached(self, session_id: str, key: str) -> dict | None:
        raw = _cache.get(f"macrodash:{session_id}:{key}")
        if raw is None:
            return None
        return json.loads(raw)

    def get_all_cached(self, session_id: str) -> dict:
        """
        Retrieve all cached MacroDash data for a session.

        Returns a dict with keys from CACHE_KEYS; missing/expired keys are {}.
        """
        return {key: (self.get_cached(session_id, key) or {}) for key in CACHE_KEYS}

    def build_demo_snapshot(self, data: dict) -> dict:
        """
        Normalize mixed MacroDash payloads into a compact demo/report snapshot.
        """
        technical = _payload_data(data.get("technical_indicators", {}) or {})
        stock = _payload_data(data.get("stock_detail", {}) or {})
        economic = _payload_data(data.get("economic_data", {}) or {})
        sentiment = _payload_data(data.get("sentiment", {}) or {})
        news = _payload_data(data.get("news", {}) or {})

        rsi_block = technical.get("rsi", {})
        if not isinstance(rsi_block, dict):
            rsi_block = {}
        macd_block = technical.get("macd", {})
        if not isinstance(macd_block, dict):
            macd_block = {}

        news_items = news.get("news") if isinstance(news.get("news"), list) else (
            news if isinstance(news, list) else (
                news.get("articles")
                or news.get("items")
                or news.get("results")
                or []
            )
        )
        headlines: list[str] = []
        for item in news_items[:3]:
            if isinstance(item, dict):
                title = item.get("title") or item.get("headline")
                if title:
                    headlines.append(str(title))
            elif item:
                headlines.append(str(item))

        return {
            "price": _first_number(
                stock.get("current_price"),
                stock.get("price"),
                stock.get("regularMarketPrice"),
            ),
            "change_pct": _first_number(
                stock.get("change_percent"),
                stock.get("change_pct"),
                stock.get("changePercent"),
                stock.get("percent_change"),
            ),
            "market_cap": _first_number(stock.get("market_cap"), stock.get("marketCap")),
            "pe_ratio": _first_number(stock.get("pe_ratio"), stock.get("pe"), stock.get("trailingPE")),
            "rsi": _first_number(rsi_block.get("latest"), technical.get("rsi"), technical.get("RSI")),
            "macd": _first_number(
                macd_block.get("latest_macd"),
                macd_block.get("macd"),
                technical.get("macd"),
                technical.get("MACD"),
            ),
            "macd_signal": _first_number(
                macd_block.get("latest_signal"),
                macd_block.get("signal"),
                macd_block.get("macd_signal"),
                technical.get("macd_signal"),
                technical.get("MACD_signal"),
            ),
            "gdp_growth": _first_number(
                economic.get("GDP", {}).get("change_percent") if isinstance(economic.get("GDP"), dict) else None,
                economic.get("gdp_growth"),
                economic.get("gdp_growth_rate"),
                economic.get("GDP_growth"),
                economic.get("realGDPGrowth"),
            ),
            "pce": _first_number(
                economic.get("PCE", {}).get("change_percent") if isinstance(economic.get("PCE"), dict) else None,
                economic.get("PCE", {}).get("current") if isinstance(economic.get("PCE"), dict) else None,
                economic.get("pce"),
                economic.get("consumer_spending"),
                economic.get("personalConsumptionExpenditures"),
            ),
            "inflation": _first_number(
                economic.get("CPIAUCSL", {}).get("change_percent") if isinstance(economic.get("CPIAUCSL"), dict) else None,
                economic.get("CPIAUCSL", {}).get("current") if isinstance(economic.get("CPIAUCSL"), dict) else None,
                economic.get("inflation"),
                economic.get("cpi"),
                economic.get("inflation_rate"),
            ),
            "unemployment_rate": _first_number(
                economic.get("UNRATE", {}).get("current") if isinstance(economic.get("UNRATE"), dict) else None,
                economic.get("unemployment_rate"),
                economic.get("unemployment"),
            ),
            "sentiment_score": _first_number(
                sentiment.get("sentiment_score"),
                sentiment.get("score"),
                sentiment.get("sentiment_score"),
                sentiment.get("compound"),
            ),
            "news_headlines": headlines,
        }

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
