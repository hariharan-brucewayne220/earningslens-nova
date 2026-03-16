"""
stream.py: Server-Sent Events (SSE) endpoint for EarningsLens.

Streams live claim verification results and transcript updates to the
frontend as they arrive in Redis.

Endpoint
--------
GET /session/{session_id}/stream
"""

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from backend.audio import redis_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["stream"])

POLL_INTERVAL = 2   # seconds between Redis polls
HEARTBEAT_INTERVAL = 10  # seconds between heartbeat events


async def event_generator(session_id: str):
    """
    Async generator that yields SSE-formatted events.

    Polls Redis every POLL_INTERVAL seconds for:
      - New claim results under results:{session_id}
      - Transcript updates under transcript:{session_id}

    Sends a heartbeat every HEARTBEAT_INTERVAL seconds so the browser
    connection stays alive.
    """
    last_claim_count = 0
    last_transcript_len = 0
    ticks_since_heartbeat = 0

    while True:
        try:
            r = redis_store.get_redis_client()

            # ----------------------------------------------------------------
            # Emit new claims
            # ----------------------------------------------------------------
            raw_results = r.get(f"results:{session_id}")
            if raw_results:
                claims: list[dict] = json.loads(raw_results)
                if len(claims) > last_claim_count:
                    new_claims = claims[last_claim_count:]
                    last_claim_count = len(claims)
                    for claim in new_claims:
                        event = json.dumps({"type": "claim", "data": claim})
                        yield f"data: {event}\n\n"

            # ----------------------------------------------------------------
            # Emit transcript updates
            # ----------------------------------------------------------------
            raw_transcript = r.get(f"transcript:{session_id}")
            if raw_transcript:
                segments = json.loads(raw_transcript)
                if isinstance(segments, list):
                    full_text = " ".join(s.get("text", "") for s in segments)
                else:
                    full_text = str(segments)

                if len(full_text) > last_transcript_len:
                    new_text = full_text[last_transcript_len:]
                    last_transcript_len = len(full_text)
                    event = json.dumps({"type": "transcript", "text": new_text})
                    yield f"data: {event}\n\n"

            # ----------------------------------------------------------------
            # Emit briefing-ready notification when available
            # ----------------------------------------------------------------
            raw_briefing = r.get(f"briefing:{session_id}")
            if raw_briefing:
                briefing = json.loads(raw_briefing)
                if briefing.get("status") == "ready":
                    event = json.dumps({
                        "type": "briefing_ready",
                        "briefing_text": briefing.get("text", ""),
                        "audio_url": briefing.get("audio_url", ""),
                    })
                    yield f"data: {event}\n\n"

        except Exception as exc:  # noqa: BLE001
            logger.warning("SSE event generator error for session %s: %s", session_id, exc)

        # ----------------------------------------------------------------
        # Heartbeat
        # ----------------------------------------------------------------
        ticks_since_heartbeat += 1
        if ticks_since_heartbeat * POLL_INTERVAL >= HEARTBEAT_INTERVAL:
            yield 'data: {"type": "heartbeat"}\n\n'
            ticks_since_heartbeat = 0

        await asyncio.sleep(POLL_INTERVAL)


@router.get("/{session_id}/stream")
async def stream_session(session_id: str):
    """
    SSE endpoint that streams live claim and transcript events.

    Clients should use EventSource or equivalent. Events have a 'type' field:
      - "claim"          — a new verified claim result (data.data contains the claim dict)
      - "transcript"     — new transcript text appended (data.text)
      - "briefing_ready" — end-of-call briefing is ready (data.briefing_text, data.audio_url)
      - "heartbeat"      — keepalive, no payload
      - "error"          — Redis or internal error (data.message)
    """
    return StreamingResponse(
        event_generator(session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
