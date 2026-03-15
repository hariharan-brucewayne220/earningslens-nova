"""
briefing.py: FastAPI router for post-call briefing and interactive Q&A.

Endpoints
---------
POST   /session/{session_id}/end
GET    /session/{session_id}/briefing
POST   /session/{session_id}/qa
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3
import redis.exceptions
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.audio import redis_store
from backend.briefing import simple_tts
from backend.briefing.generator import BriefingGenerator
from backend.briefing.sonic_tts import SonicTTS
from backend.macrodash.client import MacroDashClient
from backend.verification.pipeline import VerificationPipeline

load_dotenv = __import__("dotenv").load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["briefing"])

# Local directory for audio files
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

S3_BUCKET = os.getenv("S3_BUCKET", "")
AWS_REGION = os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1"))

BRIEFING_TTL = 86_400  # 24 hours


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class EndSessionRequest(BaseModel):
    ticker: str


class EndSessionResponse(BaseModel):
    briefing_text: str
    audio_url: str
    status: str


class BriefingMetaResponse(BaseModel):
    session_id: str
    briefing_text: str | None = None
    s3_path: str | None = None
    audio_url: str | None = None
    status: str


class QARequest(BaseModel):
    question: str
    ticker: str


class QAResponse(BaseModel):
    response_text: str
    audio_url: str


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


def _upload_to_s3(local_path: str, s3_key: str) -> str | None:
    """
    Upload a file to S3 and return a pre-signed URL (valid 24 h).

    Returns None if S3_BUCKET is not configured or upload fails.
    """
    if not S3_BUCKET:
        return None
    try:
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.upload_file(local_path, S3_BUCKET, s3_key)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=BRIEFING_TTL,
        )
        return url
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("S3 upload failed for %s: %s", s3_key, exc)
        return None


def _local_audio_url(session_id: str, filename: str) -> str:
    """Return a /api/audio/<filename> URL served from the local data dir."""
    return f"/api/audio/{filename}"


def _store_briefing_meta(session_id: str, text: str, s3_path: str | None, audio_url: str) -> None:
    """Persist briefing metadata to Redis."""
    try:
        r = redis_store.get_redis_client()
        key = f"briefing:{session_id}"
        payload = json.dumps({"text": text, "s3_path": s3_path, "audio_url": audio_url, "status": "ready"})
        r.set(key, payload, ex=BRIEFING_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store briefing meta in Redis: %s", exc)


def _get_briefing_meta(session_id: str) -> dict | None:
    """Retrieve briefing metadata from Redis."""
    try:
        r = redis_store.get_redis_client()
        key = f"briefing:{session_id}"
        raw = r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read briefing meta from Redis: %s", exc)
        return None


def _macrodash_summary(session_id: str) -> str:
    """Produce a one-line MacroDash macro summary from the Redis cache."""
    try:
        client = MacroDashClient()
        eco = client.get_cached(session_id, "economic_data") or {}
        tech = client.get_cached(session_id, "technical_indicators") or {}
        parts = []
        if eco.get("gdp_growth"):
            parts.append(f"GDP growth {eco['gdp_growth']}%")
        if eco.get("unemployment_rate"):
            parts.append(f"unemployment {eco['unemployment_rate']}%")
        indicators = tech.get("indicators", tech)
        if indicators.get("rsi"):
            parts.append(f"RSI {indicators['rsi']:.1f}" if isinstance(indicators["rsi"], float) else f"RSI {indicators['rsi']}")
        return ", ".join(parts) if parts else "No macro data cached."
    except Exception as exc:  # noqa: BLE001
        logger.debug("Macro summary error: %s", exc)
        return "No macro data cached."


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/{session_id}/end", response_model=EndSessionResponse)
async def end_session(session_id: str, body: EndSessionRequest):
    """
    End a session: generate the post-call briefing and synthesise audio.

    Steps:
      1. Fetch all claim results from Redis (results:{session_id})
      2. Generate briefing text via Nova 2 Lite (BriefingGenerator)
      3. Synthesise to MP3 via gTTS (simple_tts)
      4. Upload to S3 if configured; also keep local copy
      5. Store briefing metadata in Redis (briefing:{session_id})
    """
    _require_redis_session(session_id)

    pipeline = VerificationPipeline(session_id)
    claims = pipeline.get_all_results()

    # Generate briefing text
    generator = BriefingGenerator()
    briefing_text = generator.generate_briefing_text(claims)

    # Synthesise to audio using gTTS (Google TTS) for reliable briefing read-out
    audio_filename = f"{session_id}_briefing.mp3"
    local_audio_path = str(DATA_DIR / audio_filename)

    try:
        await simple_tts.synthesize_async(briefing_text, local_audio_path)
    except Exception as exc:
        logger.error("TTS synthesis failed for session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Audio synthesis failed: {exc}")

    # Upload to S3 (optional)
    s3_key = f"briefings/{session_id}/briefing.mp3"
    s3_url = _upload_to_s3(local_audio_path, s3_key)
    audio_url = s3_url or _local_audio_url(session_id, audio_filename)

    # Store metadata in Redis
    _store_briefing_meta(session_id, briefing_text, s3_key if s3_url else None, audio_url)

    # Update session status
    redis_store.update_session(session_id, {"status": "briefing_ready"})

    return EndSessionResponse(
        briefing_text=briefing_text,
        audio_url=audio_url,
        status="ready",
    )


@router.get("/{session_id}/briefing", response_model=BriefingMetaResponse)
async def get_briefing(session_id: str):
    """Return cached briefing metadata for a session."""
    _require_redis_session(session_id)

    meta = _get_briefing_meta(session_id)
    if meta is None:
        return BriefingMetaResponse(
            session_id=session_id,
            status="not_generated",
        )

    return BriefingMetaResponse(
        session_id=session_id,
        briefing_text=meta.get("text"),
        s3_path=meta.get("s3_path"),
        audio_url=meta.get("audio_url"),
        status=meta.get("status", "ready"),
    )


@router.post("/{session_id}/qa", response_model=QAResponse)
async def qa(session_id: str, body: QARequest):
    """
    Answer a follow-up question about the session's claim analysis.

    Generates a natural-speech response and synthesises it to audio.
    """
    _require_redis_session(session_id)

    pipeline = VerificationPipeline(session_id)
    claims = pipeline.get_all_results()

    session = redis_store.get_session(session_id)
    filing_date = session.get("filing_date", "unknown")

    context = {
        "ticker": body.ticker.upper(),
        "filing_date": filing_date,
        "macrodash_summary": _macrodash_summary(session_id),
    }

    generator = BriefingGenerator()
    response_text = generator.generate_qa_response(
        question=body.question,
        claims=claims,
        context=context,
    )

    # Synthesise response to audio using gTTS
    import uuid as _uuid
    qa_id = str(_uuid.uuid4())[:8]
    audio_filename = f"{session_id}_qa_{qa_id}.mp3"
    local_audio_path = str(DATA_DIR / audio_filename)

    try:
        await simple_tts.synthesize_async(response_text, local_audio_path)
    except Exception as exc:
        logger.error("QA TTS synthesis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Audio synthesis failed: {exc}")

    s3_key = f"briefings/{session_id}/qa_{qa_id}.mp3"
    s3_url = _upload_to_s3(local_audio_path, s3_key)
    audio_url = s3_url or _local_audio_url(session_id, audio_filename)

    return QAResponse(response_text=response_text, audio_url=audio_url)
