"""
vector_store.py: In-memory vector store with cosine similarity search.

Persists to / loads from a JSON file for demo caching.
Image bytes are excluded from JSON serialization (too large).
"""

import json
import logging
import math
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


class VectorStore:
    """
    Simple in-memory vector store.

    Each record is a chunk dict with an 'embedding' field (list[float]).
    Queries use cosine similarity via numpy.
    """

    def __init__(self):
        self._chunks: list[dict] = []
        self._embeddings: Optional[np.ndarray] = None  # shape (N, D), rebuilt lazily

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add(self, chunk: dict) -> None:
        """
        Add a chunk with its 'embedding' field to the store.
        Invalidates the cached embedding matrix.
        """
        if "embedding" not in chunk:
            raise ValueError("Chunk must have an 'embedding' field")
        self._chunks.append(chunk)
        self._embeddings = None  # invalidate cache

    def add_batch(self, chunks: list[dict]) -> None:
        """Add multiple chunks efficiently."""
        for chunk in chunks:
            if "embedding" not in chunk:
                raise ValueError("All chunks must have an 'embedding' field")
        self._chunks.extend(chunks)
        self._embeddings = None

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, query_embedding: list[float], top_k: int = 5) -> list[dict]:
        """
        Return top_k chunks by cosine similarity to query_embedding.
        Each result has a 'score' field; 'embedding' field is stripped.
        """
        if not self._chunks:
            return []

        q = np.array(query_embedding, dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-10)

        matrix = self._get_embedding_matrix()
        # matrix rows already normalised
        scores = matrix @ q_norm  # shape (N,)

        top_k = min(top_k, len(self._chunks))
        top_indices = np.argpartition(scores, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results = []
        for idx in top_indices:
            chunk = {k: v for k, v in self._chunks[idx].items() if k != "embedding"}
            chunk["score"] = float(scores[idx])
            results.append(chunk)

        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Serialize the store to a JSON file. Image bytes are base64-encoded."""
        import base64

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        records = []
        for chunk in self._chunks:
            record = {}
            for k, v in chunk.items():
                if isinstance(v, bytes):
                    record[k] = {"__bytes_b64__": base64.b64encode(v).decode()}
                elif isinstance(v, np.ndarray):
                    record[k] = v.tolist()
                else:
                    record[k] = v
            records.append(record)

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(records, fh)
        logger.info("Saved %d chunks to %s", len(records), path)

    def load(self, path: str) -> None:
        """Load a previously saved store from JSON."""
        import base64

        if not Path(path).exists():
            logger.warning("VectorStore file not found: %s", path)
            return

        with open(path, "r", encoding="utf-8") as fh:
            records = json.load(fh)

        self._chunks = []
        for record in records:
            chunk = {}
            for k, v in record.items():
                if isinstance(v, dict) and "__bytes_b64__" in v:
                    chunk[k] = base64.b64decode(v["__bytes_b64__"])
                else:
                    chunk[k] = v
            self._chunks.append(chunk)

        self._embeddings = None
        logger.info("Loaded %d chunks from %s", len(self._chunks), path)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def size(self) -> int:
        """Return the number of stored chunks."""
        return len(self._chunks)

    def type_counts(self) -> dict[str, int]:
        """Return a count of chunks by type."""
        counts: dict[str, int] = {}
        for c in self._chunks:
            t = c.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        return counts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_embedding_matrix(self) -> np.ndarray:
        """Build and cache the normalised embedding matrix."""
        if self._embeddings is not None:
            return self._embeddings

        rows = [np.array(c["embedding"], dtype=np.float32) for c in self._chunks]
        matrix = np.stack(rows)  # (N, D)
        # Row-normalise
        norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
        self._embeddings = matrix / norms
        return self._embeddings
