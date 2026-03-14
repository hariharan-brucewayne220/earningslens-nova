"""
redis_store: Redis helpers for EarningsLens session and transcript management.
"""
import json
import os
from typing import Any

import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
TRANSCRIPT_KEY_PREFIX = "transcript:"
SESSION_KEY_PREFIX = "session:"
# TTL for session data: 24 hours
SESSION_TTL = 86_400


def get_redis_client() -> redis.Redis:
    """
    Return a Redis client connected to REDIS_URL.

    Uses decode_responses=True so all values come back as str.
    """
    return redis.from_url(REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_session(session_id: str) -> dict[str, Any] | None:
    """
    Retrieve session metadata dict from Redis.

    Returns None if the session does not exist.
    Raises redis.exceptions.ConnectionError on connection failure.
    """
    client = get_redis_client()
    raw = client.get(f"{SESSION_KEY_PREFIX}{session_id}")
    if raw is None:
        return None
    return json.loads(raw)


def update_session(session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    """
    Merge *updates* into the existing session dict and persist.

    If the session does not yet exist, a new document is created from
    *updates* alone.

    Returns the updated session dict.
    """
    client = get_redis_client()
    key = f"{SESSION_KEY_PREFIX}{session_id}"
    raw = client.get(key)
    session = json.loads(raw) if raw else {}
    session.update(updates)
    client.set(key, json.dumps(session), ex=SESSION_TTL)
    return session


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------

def store_transcript(session_id: str, segments: list[dict[str, Any]]) -> None:
    """
    Overwrite the stored transcript segments for a session.

    Each segment should be a dict with at least {text, start_time, end_time}.
    """
    client = get_redis_client()
    key = f"{TRANSCRIPT_KEY_PREFIX}{session_id}"
    client.set(key, json.dumps(segments), ex=SESSION_TTL)


def get_transcript(session_id: str) -> list[dict[str, Any]]:
    """
    Retrieve transcript segments for a session.

    Returns an empty list if no transcript has been stored yet.
    """
    client = get_redis_client()
    key = f"{TRANSCRIPT_KEY_PREFIX}{session_id}"
    raw = client.get(key)
    if raw is None:
        return []
    return json.loads(raw)


def append_transcript_segment(
    session_id: str, segment: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Append a single segment to the stored transcript list.

    Returns the full updated list.
    """
    client = get_redis_client()
    key = f"{TRANSCRIPT_KEY_PREFIX}{session_id}"
    raw = client.get(key)
    segments: list[dict[str, Any]] = json.loads(raw) if raw else []
    segments.append(segment)
    client.set(key, json.dumps(segments), ex=SESSION_TTL)
    return segments
