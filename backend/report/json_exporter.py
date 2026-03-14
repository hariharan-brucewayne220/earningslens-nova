"""
json_exporter.py: Structured JSON report generation for EarningsLens.

Produces a fully structured report dict from Redis claim results, suitable
for download or further programmatic analysis.
"""

import json
import logging
from datetime import datetime, timezone

from backend.verification.pipeline import VerificationPipeline

logger = logging.getLogger(__name__)


def generate_json_report(session_id: str, ticker: str) -> dict:
    """
    Build a structured JSON report for the given session.

    Fetches verified claim results from Redis (key: results:{session_id})
    and assembles a report with summary statistics and per-claim detail.

    Args:
        session_id: the EarningsLens session UUID
        ticker: stock ticker symbol (e.g. "NVDA")

    Returns:
        dict matching the EarningsLens report schema
    """
    ticker = ticker.upper()

    pipeline = VerificationPipeline(session_id)
    raw_claims = pipeline.get_all_results()

    # Build normalised claim list
    claims_out: list[dict] = []
    flagged_out: list[dict] = []

    for raw in raw_claims:
        claim_obj = raw.get("claim", raw)  # support both wrapped and flat dicts
        verdict = raw.get("verdict", "UNVERIFIABLE")

        claim_record = {
            "claim_text": claim_obj.get("claim_text", raw.get("claim_text", "")),
            "metric": claim_obj.get("metric", raw.get("metric", "")),
            "stated_value": claim_obj.get("value", raw.get("stated_value", "")),
            "verdict": verdict,
            "confidence": float(raw.get("confidence", 0.0)),
            "filing_match": raw.get("filing_match"),
            "filing_delta": raw.get("filing_delta"),
            "technical_context": raw.get("technical_context", ""),
            "macro_context": raw.get("macro_context", ""),
            "explanation": raw.get("explanation", ""),
        }
        claims_out.append(claim_record)
        if verdict == "FLAGGED":
            flagged_out.append(claim_record)

    # Summary statistics
    total = len(claims_out)
    verified = sum(1 for c in claims_out if c["verdict"] == "VERIFIED")
    flagged = len(flagged_out)
    unverifiable = sum(1 for c in claims_out if c["verdict"] == "UNVERIFIABLE")
    verification_rate = round(verified / total, 4) if total > 0 else 0.0

    report = {
        "session_id": session_id,
        "ticker": ticker,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_claims": total,
            "verified": verified,
            "flagged": flagged,
            "unverifiable": unverifiable,
            "verification_rate": verification_rate,
        },
        "claims": claims_out,
        "flagged_claims": flagged_out,
    }

    logger.info(
        "JSON report generated: session=%s ticker=%s total=%d verified=%d flagged=%d",
        session_id, ticker, total, verified, flagged,
    )
    return report
