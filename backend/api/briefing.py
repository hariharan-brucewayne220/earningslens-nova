"""
briefing.py: FastAPI router for post-call briefing and Q&A.

Endpoints
---------
POST   /session/{session_id}/end        — generate briefing text + Nova Sonic audio
GET    /session/{session_id}/briefing   — retrieve cached briefing
POST   /session/{session_id}/qa         — text Q&A via Nova Lite
"""

import asyncio
import json
import logging
import os
import uuid as _uuid_mod
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from gtts import gTTS
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.audio import redis_store
from backend.briefing.generator import BriefingGenerator
from backend.macrodash.client import MacroDashClient
from backend.verification.pipeline import VerificationPipeline

load_dotenv = __import__("dotenv").load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["briefing"])

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
S3_BUCKET = os.getenv("S3_BUCKET", "")
AWS_REGION = os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1"))
BRIEFING_TTL = 86_400


# ---------------------------------------------------------------------------
# Models
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
    session = redis_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return session


def _upload_to_s3(local_path: str, s3_key: str) -> str | None:
    if not S3_BUCKET:
        return None
    try:
        s3 = boto3.client("s3", region_name=AWS_REGION)
        s3.upload_file(local_path, S3_BUCKET, s3_key)
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=BRIEFING_TTL,
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning("S3 upload failed for %s: %s", s3_key, exc)
        return None


def _local_audio_url(filename: str) -> str:
    return f"/api/audio/{filename}"


def _store_briefing_meta(session_id: str, text: str, s3_path: str | None, audio_url: str) -> None:
    try:
        r = redis_store.get_redis_client()
        payload = json.dumps({"text": text, "s3_path": s3_path, "audio_url": audio_url, "status": "ready"})
        r.set(f"briefing:{session_id}", payload, ex=BRIEFING_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store briefing meta: %s", exc)


def _get_briefing_meta(session_id: str) -> dict | None:
    try:
        r = redis_store.get_redis_client()
        raw = r.get(f"briefing:{session_id}")
        return json.loads(raw) if raw else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read briefing meta: %s", exc)
        return None


def _macrodash_summary(session_id: str) -> str:
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


async def _synthesize_briefing_audio(briefing_text: str, session_id: str) -> str:
    """Synthesise briefing text to MP3 via gTTS. Returns local file path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{session_id}_briefing.mp3"
    path = str(DATA_DIR / filename)
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: gTTS(text=briefing_text, lang="en", slow=False).save(path),
    )
    logger.info("gTTS briefing audio ready: %s", path)
    return path


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/{session_id}/end", response_model=EndSessionResponse)
async def end_session(session_id: str, body: EndSessionRequest):
    """
    End a session: generate briefing text via Nova Lite,
    synthesise audio via Nova 2 Sonic (gTTS fallback).
    """
    _require_redis_session(session_id)

    pipeline = VerificationPipeline(session_id)
    claims = pipeline.get_all_results()

    generator = BriefingGenerator()
    briefing_text = generator.generate_briefing_text(claims)

    try:
        local_audio_path = await _synthesize_briefing_audio(briefing_text, session_id)
    except Exception as exc:
        logger.error("All audio synthesis failed for session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=f"Audio synthesis failed: {exc}")

    audio_filename = Path(local_audio_path).name
    s3_key = f"briefings/{session_id}/{audio_filename}"
    s3_url = _upload_to_s3(local_audio_path, s3_key)
    audio_url = s3_url or _local_audio_url(audio_filename)

    _store_briefing_meta(session_id, briefing_text, s3_key if s3_url else None, audio_url)
    redis_store.update_session(session_id, {"status": "briefing_ready"})

    return EndSessionResponse(briefing_text=briefing_text, audio_url=audio_url, status="ready")


@router.get("/{session_id}/briefing", response_model=BriefingMetaResponse)
async def get_briefing(session_id: str):
    """Return cached briefing metadata for a session."""
    _require_redis_session(session_id)
    meta = _get_briefing_meta(session_id)
    if meta is None:
        return BriefingMetaResponse(session_id=session_id, status="not_generated")
    return BriefingMetaResponse(
        session_id=session_id,
        briefing_text=meta.get("text"),
        s3_path=meta.get("s3_path"),
        audio_url=meta.get("audio_url"),
        status=meta.get("status", "ready"),
    )


@router.post("/{session_id}/qa", response_model=QAResponse)
async def qa(session_id: str, body: QARequest):
    """Text Q&A via Nova Lite — returns response text and gTTS audio."""
    _require_redis_session(session_id)

    pipeline = VerificationPipeline(session_id)
    claims = pipeline.get_all_results()
    session = redis_store.get_session(session_id)

    context = {
        "ticker": body.ticker.upper(),
        "filing_date": session.get("filing_date", "unknown"),
        "macrodash_summary": _macrodash_summary(session_id),
    }

    generator = BriefingGenerator()
    response_text = generator.generate_qa_response(
        question=body.question,
        claims=claims,
        context=context,
    )

    qa_id = str(_uuid_mod.uuid4())[:8]
    audio_filename = f"{session_id}_qa_{qa_id}.mp3"
    local_audio_path = str(DATA_DIR / audio_filename)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: gTTS(text=response_text, lang="en", slow=False).save(local_audio_path),
        )
    except Exception as exc:
        logger.error("QA TTS synthesis failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Audio synthesis failed: {exc}")

    s3_key = f"briefings/{session_id}/qa_{qa_id}.mp3"
    s3_url = _upload_to_s3(local_audio_path, s3_key)
    audio_url = s3_url or _local_audio_url(audio_filename)

    return QAResponse(response_text=response_text, audio_url=audio_url)
