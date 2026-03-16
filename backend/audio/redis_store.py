"""
redis_store: In-memory session and transcript store (no Redis required for demo).

Drop-in replacement with identical API to the Redis-backed version.
"""
import json
from typing import Any

# ---------------------------------------------------------------------------
# In-memory storage
# ---------------------------------------------------------------------------

_sessions: dict[str, dict[str, Any]] = {}
_transcripts: dict[str, list[dict[str, Any]]] = {}
_kv: dict[str, str] = {}  # generic key-value for briefing metadata etc.


# ---------------------------------------------------------------------------
# Compatibility shim: get_redis_client() returns a fake client
# used only by briefing.py for raw set/get calls
# ---------------------------------------------------------------------------

class _FakeRedis:
    def get(self, key: str) -> str | None:
        return _kv.get(key)

    def set(self, key: str, value: str, ex: int = 0) -> None:
        _kv[key] = value


def get_redis_client() -> _FakeRedis:
    return _FakeRedis()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_session(session_id: str) -> dict[str, Any] | None:
    return _sessions.get(session_id)


def update_session(session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    session = _sessions.get(session_id, {})
    session.update(updates)
    _sessions[session_id] = session
    return session


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------

def store_transcript(session_id: str, segments: list[dict[str, Any]]) -> None:
    _transcripts[session_id] = segments


def get_transcript(session_id: str) -> list[dict[str, Any]]:
    return _transcripts.get(session_id, [])


def append_transcript_segment(
    session_id: str, segment: dict[str, Any]
) -> list[dict[str, Any]]:
    segments = _transcripts.get(session_id, [])
    segments.append(segment)
    _transcripts[session_id] = segments
    return segments
