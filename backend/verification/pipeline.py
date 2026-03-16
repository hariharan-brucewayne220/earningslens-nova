"""
pipeline.py: Orchestrates claim extraction and triple-source verification.

VerificationPipeline wires together ClaimExtractor, Verifier, and
MacroDashClient. It reads transcript batches from Redis, processes them,
and writes verified claim results back to Redis.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from backend.audio import redis_store as _store

from backend.macrodash.client import MacroDashClient
from backend.verification.claim_extractor import ClaimExtractor
from backend.verification.verifier import Verifier

load_dotenv()

logger = logging.getLogger(__name__)

# In-memory results store (keyed by session_id)
_results: dict[str, list[dict]] = {}
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class VerificationPipeline:
    """
    End-to-end pipeline: transcript text → extracted claims → verified results.

    Results are stored in Redis under results:{session_id} as a JSON list.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.extractor = ClaimExtractor()
        self.verifier = Verifier()
        self.macrodash = MacroDashClient()

    # ------------------------------------------------------------------
    # Main processing
    # ------------------------------------------------------------------

    async def process_transcript_batch(
        self, transcript_text: str, ticker: str
    ) -> list[dict]:
        """
        Process one batch of transcript text end-to-end.

        Steps:
          1. Extract claims from transcript_text via Nova 2 Lite
          2. Retrieve MacroDash cache from Redis
          3. For each claim, run vector store query + verification
          4. Append results to Redis
          5. Return the list of verified result dicts
        """
        ticker = ticker.upper()

        # Step 1: Extract claims
        claims = self.extractor.extract_claims(transcript_text)
        if not claims:
            logger.info(
                "No claims extracted from transcript batch (session=%s)", self.session_id
            )
            return []

        # Step 2: Get MacroDash cache
        macrodash_cache = self.macrodash.get_all_cached(self.session_id)

        # Step 3: Verify each claim
        results: list[dict] = []
        for claim in claims:
            filing_evidence = self.verifier.query_vector_store(
                ticker=ticker,
                query_text=f"{claim.get('metric', '')} {claim.get('value', '')}",
                top_k=3,
            )
            result = self.verifier.verify_claim(
                claim=claim,
                filing_evidence=filing_evidence,
                macrodash_cache=macrodash_cache,
            )
            results.append(result)

        # Step 4: Persist to Redis
        self._append_results(results)

        logger.info(
            "Processed batch: %d claims → %d results (session=%s)",
            len(claims),
            len(results),
            self.session_id,
        )
        return results

    async def run_continuous(
        self, ticker: str, interval_seconds: int = 30
    ) -> None:
        """
        Background loop that processes new transcript text every interval_seconds.

        Tracks the last processed position in the transcript so each run only
        processes new content. Reads transcript from Redis key transcript:{session_id}.
        """
        ticker = ticker.upper()
        last_position = 0  # character offset into the full transcript text

        logger.info(
            "Starting continuous verification loop for session=%s ticker=%s interval=%ds",
            self.session_id,
            ticker,
            interval_seconds,
        )

        while True:
            try:
                # Fetch latest transcript text from Redis
                transcript_text = self._get_transcript_text()
                if transcript_text and len(transcript_text) > last_position:
                    new_text = transcript_text[last_position:]
                    last_position = len(transcript_text)
                    await self.process_transcript_batch(new_text, ticker)
                else:
                    logger.debug(
                        "No new transcript content (session=%s, pos=%d)",
                        self.session_id,
                        last_position,
                    )
            except Exception as exc:
                logger.error(
                    "Error in continuous verification loop (session=%s): %s",
                    self.session_id,
                    exc,
                )

            await asyncio.sleep(interval_seconds)

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    def get_all_results(self) -> list[dict]:
        """Retrieve all stored verification results for this session."""
        return _results.get(self.session_id, [])

    def _append_results(self, new_results: list[dict]) -> None:
        """Append new_results to the stored results list."""
        existing = _results.get(self.session_id, [])
        existing.extend(new_results)
        _results[self.session_id] = existing

    def _get_transcript_text(self) -> str:
        """Read the full transcript text for this session."""
        segments = _store.get_transcript(self.session_id)
        if isinstance(segments, list):
            return " ".join(s.get("text", "") for s in segments)
        return ""

