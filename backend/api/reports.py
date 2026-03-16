"""
reports.py: FastAPI router for structured report export (JSON + PDF).

Endpoints
---------
GET  /session/{session_id}/report.json
GET  /session/{session_id}/report.pdf
"""

import logging
import os
import tempfile
from pathlib import Path

import redis.exceptions
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend.audio import redis_store
from backend.macrodash.client import MacroDashClient
from backend.report.json_exporter import generate_json_report
from backend.report.pdf_exporter import generate_pdf_report

load_dotenv = __import__("dotenv").load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["reports"])

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_redis_session(session_id: str) -> dict:
    """Load session from Redis; raise 404/503 as appropriate."""
    try:
        session = redis_store.get_session(session_id)
    except redis.exceptions.ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {exc}")
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return session


def _get_ticker(session_id: str, session: dict, query_ticker: str | None) -> str:
    """
    Resolve ticker from query param or session metadata.

    Raises 400 if ticker cannot be determined.
    """
    ticker = query_ticker or session.get("ticker")
    if not ticker:
        raise HTTPException(
            status_code=400,
            detail="Ticker not provided and not found in session. Pass ?ticker=NVDA.",
        )
    return ticker.upper()


async def _ensure_macrodash_cache(session_id: str, ticker: str) -> None:
    """
    Backfill MacroDash cache for report generation if the session has no
    cached market payloads yet.
    """
    client = MacroDashClient()
    cached = client.get_all_cached(session_id)
    if any(cached.get(key) for key in cached):
        return

    try:
        data = await client.prefetch_all(ticker)
        client.cache_to_redis(session_id, ticker, data)
        logger.info("Backfilled MacroDash cache for report generation: %s %s", session_id, ticker)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MacroDash backfill failed for report %s/%s: %s", session_id, ticker, exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{session_id}/report.json")
async def get_json_report(session_id: str, ticker: str | None = None):
    """
    Generate and return a structured JSON report for the session.

    The ticker can be passed as a query parameter; if omitted it is read
    from the session metadata stored in Redis.
    """
    session = _require_redis_session(session_id)
    resolved_ticker = _get_ticker(session_id, session, ticker)
    await _ensure_macrodash_cache(session_id, resolved_ticker)

    try:
        report = generate_json_report(session_id, resolved_ticker)
    except Exception as exc:
        logger.error("JSON report generation failed for session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}")

    return JSONResponse(content=report)


@router.get("/{session_id}/report.pdf")
async def get_pdf_report(session_id: str, ticker: str | None = None):
    """
    Generate and return a PDF report for the session.

    The PDF is generated to a temporary file and streamed back as a
    FileResponse.  The ticker can be passed as a query parameter or is
    read from the session metadata.
    """
    session = _require_redis_session(session_id)
    resolved_ticker = _get_ticker(session_id, session, ticker)
    await _ensure_macrodash_cache(session_id, resolved_ticker)

    # Write to a deterministic path inside data/ so the file persists for
    # the session lifetime; also allows re-download without regenerating.
    output_path = str(DATA_DIR / f"{session_id}_report.pdf")

    try:
        generate_pdf_report(session_id, resolved_ticker, output_path)
    except Exception as exc:
        logger.error("PDF report generation failed for session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")

    filename = f"EarningsLens_{resolved_ticker}_{session_id[:8]}.pdf"
    return FileResponse(
        path=output_path,
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
