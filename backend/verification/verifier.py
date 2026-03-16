"""
verifier.py: Triple-source claim verification engine for EarningsLens.

Verifies CEO/CFO claims against:
  1. SEC filing vector store (semantic search)
  2. MacroDash technical indicators (RSI, MACD, Bollinger Bands)
  3. FRED macroeconomic data via MacroDash (GDP, unemployment, PCE, CPI)

Uses Amazon Nova 2 Lite (amazon.nova-lite-v1:0) for the reasoning verdict.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from backend.embedding.embedder import Embedder
from backend.embedding.vector_store import VectorStore

load_dotenv()

logger = logging.getLogger(__name__)

NOVA_LITE_MODEL = "amazon.nova-lite-v1:0"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _payload_data(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


class Verifier:
    """
    Verifies a single extracted claim against three evidence sources and
    returns a structured verdict from Nova 2 Lite.
    """

    VERIFICATION_PROMPT = """
You are a financial fact-checker. A CEO made this claim during an earnings call:
CLAIM: "{claim_text}"
METRIC: {metric}
STATED VALUE: {value}
PERIOD: {period}

You have three sources of evidence:

SOURCE 1 — SEC Filing Excerpts:
{filing_evidence}

SOURCE 2 — Live Technical Indicators (MacroDash):
RSI: {rsi} (overbought >70, oversold <30)
MACD Signal: {macd_signal}, MACD Histogram: {macd_histogram}
Bollinger Band Position: {bb_position}

SOURCE 3 — Macroeconomic Data (FRED via MacroDash):
{macro_data}

Based on ALL three sources, provide your analysis as a JSON object:
{{
  "verdict": "VERIFIED" | "FLAGGED" | "UNVERIFIABLE",
  "confidence": 0.0-1.0,
  "filing_match": "what the filing shows for this metric, or null",
  "filing_delta": "percentage point difference, or null",
  "filing_page": "page/section reference, or null",
  "technical_context": "brief RSI/MACD interpretation relevant to the claim",
  "macro_context": "brief macro data interpretation relevant to the claim",
  "explanation": "2-3 sentence human-readable explanation of the verdict"
}}

Rules:
- VERIFIED: filing confirms within 2 percentage points
- FLAGGED: filing contradicts by >2pp OR directional mismatch
- UNVERIFIABLE: forward guidance OR no relevant filing data found
"""

    def __init__(self):
        region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        self._bedrock = boto3.client("bedrock-runtime", region_name=region)
        self._embedder = Embedder()
        # Cache loaded vector stores by ticker to avoid reloading on each claim
        self._vector_stores: dict[str, VectorStore] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_claim(
        self,
        claim: dict,
        filing_evidence: list[dict],
        macrodash_cache: dict,
    ) -> dict:
        """
        Verify a single claim against all three evidence sources.

        Args:
            claim: dict from ClaimExtractor (claim_text, metric, value, period, ...)
            filing_evidence: pre-fetched list of vector store result dicts
            macrodash_cache: dict with keys technical_indicators, economic_data, ...

        Returns:
            dict with keys: claim, verdict, confidence, filing_match, filing_delta,
            filing_page, technical_context, macro_context, explanation, sources
        """
        # Build evidence strings
        filing_text = self._format_filing_evidence(filing_evidence)
        tech_summary = self._extract_technical_summary(
            macrodash_cache.get("technical_indicators", {})
        )
        macro_text = self._extract_macro_summary(
            macrodash_cache.get("economic_data", {})
        )

        prompt = self.VERIFICATION_PROMPT.format(
            claim_text=claim.get("claim_text", ""),
            metric=claim.get("metric", ""),
            value=claim.get("value", ""),
            period=claim.get("period", "unknown"),
            filing_evidence=filing_text,
            rsi=tech_summary.get("rsi", "N/A"),
            macd_signal=tech_summary.get("macd_signal", "N/A"),
            macd_histogram=tech_summary.get("macd_histogram", "N/A"),
            bb_position=tech_summary.get("bb_position", "N/A"),
            macro_data=macro_text,
        )

        verdict_raw = self._call_nova(prompt)
        verdict = self._parse_verdict(verdict_raw)

        return {
            "claim": claim,
            "verdict": verdict.get("verdict", "UNVERIFIABLE"),
            "confidence": verdict.get("confidence", 0.0),
            "filing_match": verdict.get("filing_match"),
            "filing_delta": verdict.get("filing_delta"),
            "filing_page": verdict.get("filing_page"),
            "technical_context": verdict.get("technical_context", ""),
            "macro_context": verdict.get("macro_context", ""),
            "explanation": verdict.get("explanation", ""),
            "sources": {
                "filing_chunks": len(filing_evidence),
                "technical_indicators_available": bool(
                    macrodash_cache.get("technical_indicators")
                ),
                "economic_data_available": bool(macrodash_cache.get("economic_data")),
            },
        }

    def query_vector_store(
        self, ticker: str, query_text: str, top_k: int = 3
    ) -> list[dict]:
        """
        Search the vector store for the given ticker and return top_k chunks.

        Loads the store from data/{ticker}_vectorstore.json on first call;
        subsequent calls reuse the cached instance.
        """
        store = self._load_vector_store(ticker)
        if store is None or store.size() == 0:
            return []

        try:
            query_embedding = self._embedder.embed_text(query_text)
            return store.query(query_embedding, top_k=top_k)
        except Exception as exc:
            logger.warning("Vector store query failed for ticker %s: %s", ticker, exc)
            return []

    # ------------------------------------------------------------------
    # Evidence formatters
    # ------------------------------------------------------------------

    def _extract_technical_summary(self, tech_data: dict) -> dict:
        """
        Safely extract RSI, MACD signal/histogram, and Bollinger band position
        from the MacroDash technical indicators payload.

        Returns a clean dict with string values; missing fields default to "N/A".
        """
        if not tech_data:
            return {
                "rsi": "N/A",
                "macd_signal": "N/A",
                "macd_histogram": "N/A",
                "bb_position": "N/A",
            }

        raw = _payload_data(tech_data)
        indicators = raw.get("indicators", raw)

        def _safe(value: Any, fmt: str = ".2f") -> str:
            if value is None:
                return "N/A"
            try:
                return format(float(value), fmt)
            except (TypeError, ValueError):
                return str(value)

        rsi_block = indicators.get("rsi", indicators.get("RSI", {}))
        if isinstance(rsi_block, dict):
            rsi = _safe(rsi_block.get("latest"))
        else:
            rsi = _safe(rsi_block)

        macd_block = indicators.get("macd", indicators.get("MACD", {}))
        if isinstance(macd_block, dict):
            macd_signal = _safe(
                macd_block.get("latest_signal") or
                macd_block.get("signal") or macd_block.get("macd_signal")
            )
            macd_histogram = _safe(
                macd_block.get("latest_histogram") or
                macd_block.get("histogram") or macd_block.get("macd_histogram")
            )
        else:
            macd_signal = _safe(
                indicators.get("macd_signal") or indicators.get("MACD_signal")
            )
            macd_histogram = _safe(
                indicators.get("macd_histogram") or indicators.get("MACD_histogram")
            )

        # Bollinger band position: describe price relative to bands
        bb_block = indicators.get("bbands", indicators.get("bollinger_bands", indicators.get("bollingerBands", {})))
        if isinstance(bb_block, dict):
            upper = bb_block.get("latest_upper") or bb_block.get("upper") or bb_block.get("upper_band")
            middle = bb_block.get("latest_middle") or bb_block.get("middle") or bb_block.get("mid") or bb_block.get("sma")
            lower = bb_block.get("latest_lower") or bb_block.get("lower") or bb_block.get("lower_band")
            price = raw.get("current_price") or raw.get("price")
            if upper and lower and price:
                try:
                    u, l, p = float(upper), float(lower), float(price)
                    band_width = u - l
                    if band_width > 0:
                        position_pct = (p - l) / band_width * 100
                        mid_display = f"{float(middle):.2f}" if middle not in (None, "N/A") else "N/A"
                        bb_position = f"{position_pct:.0f}% of band (upper={u:.2f}, mid={mid_display}, lower={l:.2f})"
                    else:
                        bb_position = "N/A"
                except (TypeError, ValueError):
                    bb_position = "N/A"
            elif upper and lower:
                bb_position = f"upper={_safe(upper)}, lower={_safe(lower)}"
            else:
                bb_position = "N/A"
        else:
            bb_position = "N/A"

        return {
            "rsi": rsi,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "bb_position": bb_position,
        }

    def _extract_macro_summary(self, eco_data: dict) -> str:
        """
        Format FRED macroeconomic data into a readable string for the prompt.

        Extracts GDP growth, unemployment, PCE (consumer spending), and CPI
        from the MacroDash economic-data payload.
        """
        if not eco_data:
            return "No macroeconomic data available."

        raw = _payload_data(eco_data)

        def _val(keys: list[str], data: dict) -> str:
            for k in keys:
                v = data.get(k)
                if v is not None:
                    try:
                        return f"{float(v):.2f}"
                    except (TypeError, ValueError):
                        return str(v)
            return "N/A"

        lines = []

        # GDP
        gdp = _val(["gdp_growth", "gdp_growth_rate", "GDP_growth", "realGDPGrowth"], raw)
        if gdp == "N/A" and isinstance(raw.get("GDP"), dict):
            gdp = _val(["change_percent", "current"], raw["GDP"])
        lines.append(f"GDP Growth: {gdp}%")

        # Unemployment
        unemp = _val(["unemployment_rate", "unemployment", "unemploymentRate"], raw)
        if unemp == "N/A" and isinstance(raw.get("UNRATE"), dict):
            unemp = _val(["current", "previous"], raw["UNRATE"])
        lines.append(f"Unemployment Rate: {unemp}%")

        # PCE / consumer spending
        pce = _val(["pce", "consumer_spending", "personalConsumptionExpenditures"], raw)
        if pce == "N/A" and isinstance(raw.get("PCE"), dict):
            pce = _val(["change_percent", "current"], raw["PCE"])
        lines.append(f"Consumer Spending (PCE): {pce}%")

        # CPI / inflation
        cpi = _val(["cpi", "inflation_rate", "cpi_yoy", "inflation"], raw)
        if cpi == "N/A" and isinstance(raw.get("CPIAUCSL"), dict):
            cpi = _val(["change_percent", "current"], raw["CPIAUCSL"])
        lines.append(f"CPI / Inflation: {cpi}%")

        # Interest rates
        rates = _val(["federal_funds_rate", "interest_rate", "fed_rate", "federalFundsRate"], raw)
        if rates == "N/A" and isinstance(raw.get("DFF"), dict):
            rates = _val(["current", "previous"], raw["DFF"])
        if rates != "N/A":
            lines.append(f"Federal Funds Rate: {rates}%")

        return "\n".join(lines)

    @staticmethod
    def _format_filing_evidence(chunks: list[dict]) -> str:
        """Format vector store result chunks into a readable block for the prompt."""
        if not chunks:
            return "No relevant filing excerpts found."

        parts = []
        for i, chunk in enumerate(chunks, 1):
            chunk_type = chunk.get("type", "text")
            page = chunk.get("page", "?")
            score = chunk.get("score", 0.0)
            if chunk_type == "text":
                content = chunk.get("text", "")[:500]
            elif chunk_type == "table":
                content = chunk.get("text_repr", "")[:500]
            else:
                content = f"[{chunk_type} content on page {page}]"
            parts.append(
                f"[Excerpt {i} | page {page} | type={chunk_type} | relevance={score:.2f}]\n{content}"
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Nova 2 Lite helpers
    # ------------------------------------------------------------------

    def _call_nova(self, prompt: str) -> str:
        """Invoke Nova 2 Lite and return the raw text response."""
        try:
            response = self._bedrock.invoke_model(
                modelId=NOVA_LITE_MODEL,
                body=json.dumps({
                    "messages": [{"role": "user", "content": [{"text": prompt}]}],
                    "inferenceConfig": {"max_new_tokens": 1000},
                }),
            )
            result = json.loads(response["body"].read())
            return result["output"]["message"]["content"][0]["text"]
        except (BotoCoreError, ClientError) as exc:
            logger.error("Nova Lite verification call failed: %s", exc)
            return ""
        except (KeyError, json.JSONDecodeError) as exc:
            logger.error("Unexpected Nova Lite response: %s", exc)
            return ""

    @staticmethod
    def _parse_verdict(raw_text: str) -> dict:
        """
        Extract and parse the JSON verdict object from Nova's response.

        Returns a default UNVERIFIABLE verdict on parse failure.
        """
        default = {
            "verdict": "UNVERIFIABLE",
            "confidence": 0.0,
            "filing_match": None,
            "filing_delta": None,
            "filing_page": None,
            "technical_context": "",
            "macro_context": "",
            "explanation": "Could not parse verdict from model response.",
        }

        if not raw_text:
            return default

        # Strip markdown fences
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        if fence_match:
            json_text = fence_match.group(1)
        else:
            obj_match = re.search(r"(\{.*?\})", raw_text, re.DOTALL)
            if obj_match:
                json_text = obj_match.group(1)
            else:
                logger.warning("No JSON object found in verdict response: %s", raw_text[:200])
                return default

        try:
            verdict = json.loads(json_text)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse verdict JSON: %s", exc)
            return default

        # Validate verdict field
        valid_verdicts = {"VERIFIED", "FLAGGED", "UNVERIFIABLE"}
        if verdict.get("verdict") not in valid_verdicts:
            verdict["verdict"] = "UNVERIFIABLE"

        # Clamp confidence
        try:
            verdict["confidence"] = max(0.0, min(1.0, float(verdict.get("confidence", 0.0))))
        except (TypeError, ValueError):
            verdict["confidence"] = 0.0

        return {**default, **verdict}

    # ------------------------------------------------------------------
    # Vector store loader
    # ------------------------------------------------------------------

    def _load_vector_store(self, ticker: str) -> VectorStore | None:
        """
        Load (and cache) the vector store for a given ticker from disk.

        Expected path: data/{ticker}_vectorstore.json
        """
        ticker = ticker.upper()
        if ticker in self._vector_stores:
            return self._vector_stores[ticker]

        store = VectorStore()
        path = str(DATA_DIR / f"{ticker}_vectorstore.json")
        try:
            store.load(path)
            logger.info("Loaded vector store for %s: %d chunks", ticker, store.size())
        except Exception as exc:
            logger.warning("Could not load vector store for %s at %s: %s", ticker, path, exc)

        self._vector_stores[ticker] = store
        return store
