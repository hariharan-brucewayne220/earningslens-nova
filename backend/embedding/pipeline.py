"""
pipeline.py: Orchestrates the full PDF-to-vector-store embedding pipeline.

Steps:
  1. Extract chunks from PDF (text, tables, images)
  2. Embed each chunk via Bedrock (with Redis progress updates)
  3. Store in VectorStore
  4. Persist to data/{ticker}_vectorstore.json
"""

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from backend.embedding.embedder import Embedder
from backend.embedding.pdf_processor import PDFProcessor
from backend.embedding.vector_store import VectorStore

load_dotenv()

logger = logging.getLogger(__name__)


from backend.api.embedding import _jobs as _embed_jobs


def _update_progress(_r: None, job_id: str, data: dict) -> None:
    _embed_jobs[job_id] = data


class EmbeddingPipeline:
    """
    Runs the full PDF -> chunk -> embed -> VectorStore pipeline.
    Writes progress to Redis key: embed_job:{job_id}
    """

    def __init__(self):
        self.processor = PDFProcessor()
        self.embedder = Embedder()

    def run(self, pdf_path: str, ticker: str, job_id: str) -> VectorStore:
        """
        Execute the pipeline.

        Args:
            pdf_path: Absolute path to the PDF/HTML filing.
            ticker:   Company ticker symbol (e.g. "NVDA").
            job_id:   Unique identifier for this embedding job (for progress tracking).

        Returns:
            A populated VectorStore instance (also persisted to disk).
        """
        # Initial status
        _update_progress(None, job_id, {
            "status": "extracting",
            "progress_pct": 0,
            "chunks_total": 0,
            "chunks_done": 0,
        })

        # --- Step 1: Extract chunks ---
        logger.info("[%s] Extracting chunks from %s", job_id, pdf_path)
        try:
            chunks = self.processor.extract_chunks(pdf_path)
        except Exception as exc:
            _update_progress(None, job_id, {"status": "failed", "error": str(exc), "progress_pct": 0, "chunks_total": 0, "chunks_done": 0})
            raise

        total = len(chunks)
        logger.info("[%s] Extracted %d chunks", job_id, total)
        _update_progress(None, job_id, {
            "status": "embedding",
            "progress_pct": 5,
            "chunks_total": total,
            "chunks_done": 0,
        })

        # --- Step 2: Embed each chunk ---
        store = VectorStore()
        done = 0
        errors = 0

        for i, chunk in enumerate(chunks):
            try:
                embedded = self.embedder.embed_chunk(chunk)
                store.add(embedded)
                done += 1
            except Exception as exc:
                logger.warning("[%s] Skipping chunk %d (type=%s): %s", job_id, i, chunk.get("type"), exc)
                errors += 1

            if (i + 1) % 5 == 0 or (i + 1) == total:
                pct = 5 + int(90 * (i + 1) / total)
                _update_progress(None, job_id, {
                    "status": "embedding",
                    "progress_pct": pct,
                    "chunks_total": total,
                    "chunks_done": done,
                    "errors": errors,
                })

        # --- Step 3: Persist to disk ---
        data_dir = Path("data")
        data_dir.mkdir(parents=True, exist_ok=True)
        store_path = str(data_dir / f"{ticker.upper()}_vectorstore.json")

        try:
            store.save(store_path)
        except Exception as exc:
            logger.error("[%s] VectorStore save failed: %s", job_id, exc)

        # --- Done ---
        logger.info(
            "[%s] Pipeline complete. Stored %d/%d chunks (%d errors). Path: %s",
            job_id, done, total, errors, store_path,
        )
        _update_progress(None, job_id, {
            "status": "complete",
            "progress_pct": 100,
            "chunks_total": total,
            "chunks_done": done,
            "errors": errors,
            "store_path": store_path,
        })

        return store
