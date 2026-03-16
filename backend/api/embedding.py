"""
embedding.py: FastAPI router for filing embedding operations.

Endpoints
---------
POST  /filing/embed          Start embedding pipeline (background task)
GET   /filing/embed/{job_id} Poll embedding job progress
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/filing", tags=["embedding"])

# In-memory job status store
_jobs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class EmbedRequest(BaseModel):
    ticker: str
    local_path: str


class EmbedStartResponse(BaseModel):
    job_id: str
    status: str


class EmbedStatusResponse(BaseModel):
    job_id: str
    status: str
    progress_pct: int = 0
    chunks_total: int = 0
    chunks_done: int = 0
    errors: int = 0
    store_path: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Background task runner
# ---------------------------------------------------------------------------

def _run_pipeline(pdf_path: str, ticker: str, job_id: str) -> None:
    """Synchronous wrapper executed in a thread pool by FastAPI."""
    from backend.embedding.pipeline import EmbeddingPipeline

    try:
        pipeline = EmbeddingPipeline()
        pipeline.run(pdf_path=pdf_path, ticker=ticker, job_id=job_id)
    except Exception as exc:
        logger.exception("Embedding pipeline failed for job %s", job_id)
        _jobs[job_id] = {"status": "failed", "error": str(exc), "progress_pct": 0, "chunks_total": 0, "chunks_done": 0}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/embed", response_model=EmbedStartResponse)
async def start_embedding(body: EmbedRequest, background_tasks: BackgroundTasks):
    """Start the embedding pipeline for a filing in the background."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "progress_pct": 0, "chunks_total": 0, "chunks_done": 0}
    background_tasks.add_task(_run_pipeline, body.local_path, body.ticker, job_id)
    return EmbedStartResponse(job_id=job_id, status="started")


@router.get("/embed/{job_id}", response_model=EmbedStatusResponse)
async def get_embed_status(job_id: str):
    """Poll the status of an embedding job."""
    data = _jobs.get(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    return EmbedStatusResponse(
        job_id=job_id,
        status=data.get("status", "unknown"),
        progress_pct=data.get("progress_pct", 0),
        chunks_total=data.get("chunks_total", 0),
        chunks_done=data.get("chunks_done", 0),
        errors=data.get("errors", 0),
        store_path=data.get("store_path"),
        error=data.get("error"),
    )
