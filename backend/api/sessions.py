"""
sessions.py: FastAPI router for EarningsLens session management.

Endpoints
---------
POST   /session/start
POST   /session/{session_id}/upload-audio
GET    /session/{session_id}/transcript
GET    /session/{session_id}/status
"""
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import redis.exceptions
from fastapi import APIRouter, HTTPException, UploadFile, File, Query
from pydantic import BaseModel

from backend.audio.ingestor import AudioIngestor, SUPPORTED_FORMATS
from backend.audio.transcribe_client import TranscribeClient
from backend.audio import redis_store

router = APIRouter(prefix="/session", tags=["session"])

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class StartSessionRequest(BaseModel):
    ticker: str = "NVDA"


class StartSessionResponse(BaseModel):
    session_id: str


class UploadAudioResponse(BaseModel):
    session_id: str
    s3_uri: str
    transcribe_job_name: str
    status: str


class TranscriptSegment(BaseModel):
    text: str
    start_time: float
    end_time: float


class TranscriptResponse(BaseModel):
    status: str
    transcript_text: str | None = None
    segments: list[TranscriptSegment] = []


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str
    ticker: str | None = None
    created_at: str | None = None
    transcribe_job_name: str | None = None
    s3_uri: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_redis_session(session_id: str) -> dict:
    """Load session from Redis, raise 404 if missing."""
    try:
        session = redis_store.get_session(session_id)
    except redis.exceptions.ConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Redis is unavailable: {exc}",
        )
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found.",
        )
    return session


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/start", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest):
    """
    Create a new EarningsLens session.

    Stores session metadata in Redis and returns the session_id.
    """
    session_id = str(uuid.uuid4())
    metadata = {
        "ticker": body.ticker.upper(),
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        redis_store.update_session(session_id, metadata)
    except redis.exceptions.ConnectionError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Redis is unavailable: {exc}",
        )
    return StartSessionResponse(session_id=session_id)


@router.post("/{session_id}/upload-audio", response_model=UploadAudioResponse)
async def upload_audio(session_id: str, file: UploadFile = File(...)):
    """
    Accept a multipart audio file upload, push it to S3, and start an
    AWS Transcribe job.
    """
    # Validate extension
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext}'. "
                f"Accepted: {sorted(SUPPORTED_FORMATS)}"
            ),
        )

    # Ensure the session exists
    _require_redis_session(session_id)

    # Read file bytes and upload to S3
    file_bytes = await file.read()
    ingestor = AudioIngestor()
    try:
        s3_uri = ingestor.upload_audio_bytes(file_bytes, filename, session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"S3 upload failed: {exc}")

    # Start Transcribe job
    timestamp = int(time.time())
    job_name = f"earningslens-{session_id[:8]}-{timestamp}"
    transcriber = TranscribeClient()
    try:
        transcriber.start_transcription_job(job_name, s3_uri)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start transcription job: {exc}",
        )

    # Persist to Redis
    try:
        redis_store.update_session(
            session_id,
            {
                "status": "transcribing",
                "s3_uri": s3_uri,
                "transcribe_job_name": job_name,
            },
        )
    except redis.exceptions.ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"Redis is unavailable: {exc}")

    return UploadAudioResponse(
        session_id=session_id,
        s3_uri=s3_uri,
        transcribe_job_name=job_name,
        status="transcribing",
    )


@router.get("/{session_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(session_id: str):
    """
    Poll the AWS Transcribe job and, when complete, return the transcript.

    - If still in progress: returns {status: "IN_PROGRESS"}.
    - If complete: parses, caches in Redis, and returns full transcript.
    - If failed: returns {status: "FAILED"}.
    """
    session = _require_redis_session(session_id)
    job_name: str | None = session.get("transcribe_job_name")

    if not job_name:
        raise HTTPException(
            status_code=400,
            detail="No transcription job associated with this session. Upload audio first.",
        )

    transcriber = TranscribeClient()

    # Check for a cached completed transcript first
    try:
        cached_segments = redis_store.get_transcript(session_id)
    except redis.exceptions.ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"Redis is unavailable: {exc}")

    if cached_segments:
        transcript_text = " ".join(s["text"] for s in cached_segments)
        return TranscriptResponse(
            status="COMPLETED",
            transcript_text=transcript_text,
            segments=[TranscriptSegment(**s) for s in cached_segments],
        )

    # Poll the job
    try:
        status = transcriber.poll_transcription_job(job_name)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to poll transcription job: {exc}",
        )

    if status in {"QUEUED", "IN_PROGRESS"}:
        return TranscriptResponse(status="IN_PROGRESS")

    if status == "FAILED":
        redis_store.update_session(session_id, {"status": "failed"})
        return TranscriptResponse(status="FAILED")

    # COMPLETED — fetch and parse
    try:
        import urllib.request, json as _json
        aws_client = transcriber.client
        job_resp = aws_client.get_transcription_job(TranscriptionJobName=job_name)
        transcript_url = job_resp["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        with urllib.request.urlopen(transcript_url) as resp:
            transcript_json = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transcript JSON: {exc}",
        )

    segments = transcriber.parse_transcript_segments(transcript_json)
    transcript_text = " ".join(s["text"] for s in segments)

    # Cache in Redis
    try:
        redis_store.store_transcript(session_id, segments)
        redis_store.update_session(session_id, {"status": "completed"})
    except redis.exceptions.ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"Redis is unavailable: {exc}")

    return TranscriptResponse(
        status="COMPLETED",
        transcript_text=transcript_text,
        segments=[TranscriptSegment(**s) for s in segments],
    )


@router.get("/{session_id}/status", response_model=SessionStatusResponse)
async def get_session_status(session_id: str):
    """
    Return the current session metadata from Redis.
    """
    session = _require_redis_session(session_id)
    return SessionStatusResponse(
        session_id=session_id,
        status=session.get("status", "unknown"),
        ticker=session.get("ticker"),
        created_at=session.get("created_at"),
        transcribe_job_name=session.get("transcribe_job_name"),
        s3_uri=session.get("s3_uri"),
    )
