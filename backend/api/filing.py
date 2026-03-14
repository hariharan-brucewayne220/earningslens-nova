"""
filing.py: FastAPI router for SEC EDGAR filing fetch operations.

Endpoints
---------
POST  /filing/fetch              Download latest 10-Q/10-K for a ticker
GET   /filing/{ticker}/status    Return cached filing metadata from Redis
"""

import json
import logging
import os
from typing import Optional

import redis as redis_lib
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.filing.edgar_navigator import EDGARNavigator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/filing", tags=["filing"])

# ---------------------------------------------------------------------------
# Redis helpers
# ---------------------------------------------------------------------------

def _get_redis() -> redis_lib.Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    return redis_lib.from_url(url, decode_responses=True)


def _store_filing(r: redis_lib.Redis, ticker: str, data: dict) -> None:
    r.set(f"filing:{ticker.upper()}", json.dumps(data), ex=86400 * 7)


def _load_filing(r: redis_lib.Redis, ticker: str) -> Optional[dict]:
    raw = r.get(f"filing:{ticker.upper()}")
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class FetchFilingRequest(BaseModel):
    ticker: str


class FetchFilingResponse(BaseModel):
    ticker: str
    form_type: str
    filing_date: str
    s3_path: str
    local_path: str
    status: str


class FilingStatusResponse(BaseModel):
    ticker: str
    form_type: Optional[str] = None
    filing_date: Optional[str] = None
    s3_path: Optional[str] = None
    local_path: Optional[str] = None
    status: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/fetch", response_model=FetchFilingResponse)
async def fetch_filing(body: FetchFilingRequest):
    """
    Download the latest 10-Q (or 10-K fallback) for the given ticker from
    SEC EDGAR, upload it to S3, and cache metadata in Redis.
    """
    ticker = body.ticker.upper()
    nav = EDGARNavigator()

    # Download filing
    try:
        result = nav.download_filing(ticker, output_dir="data")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("EDGAR download failed for %s", ticker)
        raise HTTPException(status_code=500, detail=f"EDGAR download failed: {exc}")

    # Upload to S3
    s3_path = ""
    try:
        s3_path = nav.upload_to_s3(
            result["local_path"], ticker, result["filing_date"]
        )
        result["s3_path"] = s3_path
    except Exception as exc:
        logger.warning("S3 upload failed (non-fatal): %s", exc)
        # Non-fatal: local file is still usable

    # Persist to Redis
    metadata = {
        "ticker": ticker,
        "form_type": result["form_type"],
        "filing_date": result["filing_date"],
        "local_path": result["local_path"],
        "s3_path": s3_path,
        "status": "ready",
    }
    try:
        r = _get_redis()
        _store_filing(r, ticker, metadata)
    except Exception as exc:
        logger.warning("Redis store failed (non-fatal): %s", exc)

    return FetchFilingResponse(
        ticker=ticker,
        form_type=result["form_type"],
        filing_date=result["filing_date"],
        s3_path=s3_path,
        local_path=result["local_path"],
        status="ready",
    )


@router.get("/{ticker}/status", response_model=FilingStatusResponse)
async def get_filing_status(ticker: str):
    """Return cached filing metadata for the given ticker."""
    ticker = ticker.upper()
    try:
        r = _get_redis()
        data = _load_filing(r, ticker)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")

    if data is None:
        return FilingStatusResponse(ticker=ticker, status="not_fetched")

    return FilingStatusResponse(
        ticker=ticker,
        form_type=data.get("form_type"),
        filing_date=data.get("filing_date"),
        s3_path=data.get("s3_path"),
        local_path=data.get("local_path"),
        status=data.get("status", "unknown"),
    )
