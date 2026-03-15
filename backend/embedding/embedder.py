"""
embedder.py: Bedrock-backed multimodal embedder for EarningsLens.

Model used:
  - Text / images: amazon.nova-2-multimodal-embeddings-v1:0  (up to 3072 dims)
    Unified model for text, image, video, audio embeddings in a shared semantic space.
    Supports cross-modal retrieval — text queries can find relevant images and vice versa.

API format (invoke_model):
  {
    "schemaVersion": "nova-multimodal-embed-v1",
    "taskType": "SINGLE_EMBEDDING",
    "singleEmbeddingParams": {
      "embeddingPurpose": "GENERIC_INDEX",
      "embeddingDimension": 1024,
      "text": {"truncationMode": "END", "value": "..."}
      # OR
      "image": {"format": "png", "detailLevel": "STANDARD_IMAGE",
                "source": {"bytes": "<base64>"}}
    }
  }

Note: Nova Multimodal Embeddings maps text and images into a SHARED semantic space,
so a text query naturally retrieves image chunks. "Multimodal" means cross-modal
retrieval, not that one embedding encodes both text+image simultaneously.
"""

import base64
import json
import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

NOVA_EMBED_MODEL = "amazon.nova-2-multimodal-embeddings-v1:0"
VISION_MODEL = "amazon.nova-lite-v1:0"   # for image description fallback
SCHEMA_VERSION = "nova-multimodal-embed-v1"
EMBEDDING_DIM = 1024   # valid: 256, 384, 1024, 3072


class Embedder:
    """
    Wraps Amazon Nova Multimodal Embeddings (amazon.nova-2-multimodal-embeddings-v1:0).

    Text and images are embedded into the SAME semantic vector space, enabling
    cross-modal search: a text query about "revenue chart" will surface matching
    image chunks from the SEC filing.

    Usage:
        embedder = Embedder()
        chunk_with_embedding = embedder.embed_chunk(chunk)
    """

    def __init__(self):
        self.region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        self._bedrock = None

    @property
    def bedrock(self):
        if self._bedrock is None:
            self._bedrock = boto3.client("bedrock-runtime", region_name=self.region)
        return self._bedrock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """
        Embed a text string using amazon.nova-2-multimodal-embeddings-v1:0.
        Returns a list of EMBEDDING_DIM floats in the shared multimodal space.
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        body = json.dumps({
            "schemaVersion": SCHEMA_VERSION,
            "taskType": "SINGLE_EMBEDDING",
            "singleEmbeddingParams": {
                "embeddingPurpose": "GENERIC_INDEX",
                "embeddingDimension": EMBEDDING_DIM,
                "text": {
                    "truncationMode": "END",
                    "value": text,
                },
            },
        })

        try:
            response = self.bedrock.invoke_model(
                modelId=NOVA_EMBED_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            # Response: {"embeddings": [{"embeddingType": "TEXT", "embedding": [...]}]}
            return result["embeddings"][0]["embedding"]
        except (BotoCoreError, ClientError) as exc:
            logger.error("Text embedding failed: %s", exc)
            raise

    def embed_image(self, image_bytes: bytes, detail_level: str = "STANDARD_IMAGE") -> list[float]:
        """
        Embed an image using amazon.nova-2-multimodal-embeddings-v1:0.
        Returns a list of EMBEDDING_DIM floats in the shared multimodal space,
        enabling cross-modal retrieval (text queries can find this image).

        Args:
            image_bytes: Raw image bytes (PNG or JPEG).
            detail_level: "STANDARD_IMAGE" for photos/charts, "DOCUMENT_IMAGE" for
                          scanned documents/financial statements.
        """
        if not image_bytes:
            raise ValueError("Cannot embed empty image")

        # Detect format from magic bytes
        fmt = "png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            fmt = "jpeg"
        elif image_bytes[:4] == b"GIF8":
            fmt = "gif"

        b64 = base64.b64encode(image_bytes).decode("utf-8")

        body = json.dumps({
            "schemaVersion": SCHEMA_VERSION,
            "taskType": "SINGLE_EMBEDDING",
            "singleEmbeddingParams": {
                "embeddingPurpose": "GENERIC_INDEX",
                "embeddingDimension": EMBEDDING_DIM,
                "image": {
                    "format": fmt,
                    "detailLevel": detail_level,
                    "source": {
                        "bytes": b64,
                    },
                },
            },
        })

        try:
            response = self.bedrock.invoke_model(
                modelId=NOVA_EMBED_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            # Response: {"embeddings": [{"embeddingType": "IMAGE", "embedding": [...]}]}
            return result["embeddings"][0]["embedding"]
        except (BotoCoreError, ClientError) as exc:
            logger.warning("Image embedding failed, falling back to text description: %s", exc)
            description = self._describe_image_with_nova(image_bytes)
            return self.embed_text(description)

    def embed_multimodal(self, text: str, image_bytes: bytes) -> list[float]:
        """
        Embed a financial chart WITH its surrounding text context.

        Nova Multimodal Embeddings maps text and images into a SHARED semantic space,
        so this method embeds the image using IMAGE_RETRIEVAL purpose optimized for
        cross-modal search, while the text context is used to enrich the image chunk
        metadata for hybrid retrieval.

        For true joint encoding, we embed the image and store text as metadata.
        At query time, the text query will naturally surface the image via shared
        semantic space — that is Nova's key differentiator.

        Returns:
            Image embedding (list of EMBEDDING_DIM floats). Text is stored alongside
            as metadata in the chunk for BM25/hybrid retrieval if needed.
        """
        if not image_bytes:
            return self.embed_text(text)
        if not text or not text.strip():
            return self.embed_image(image_bytes)

        # Embed the image in the shared semantic space.
        # Use DOCUMENT_IMAGE for financial chart pages (better for charts/tables).
        return self.embed_image(image_bytes, detail_level="DOCUMENT_IMAGE")

    def embed_chunk(self, chunk: dict) -> dict:
        """
        Add an 'embedding' field to the chunk dict.

        Dispatches by chunk['type']:
          - text  -> embed_text(chunk['text'])
          - table -> embed_text(chunk['text_repr'])
          - image -> embed_multimodal(context, image_bytes) if context available,
                     else embed_image(image_bytes)
        """
        chunk_type = chunk.get("type")
        try:
            if chunk_type == "text":
                embedding = self.embed_text(chunk["text"])
            elif chunk_type == "table":
                embedding = self.embed_text(chunk["text_repr"])
            elif chunk_type == "image":
                image_bytes = chunk["image_bytes"]
                # Use surrounding text context when available — stored as metadata
                # for hybrid retrieval alongside the Nova image embedding
                context = chunk.get("context") or chunk.get("caption") or chunk.get("surrounding_text")
                if context:
                    embedding = self.embed_multimodal(context, image_bytes)
                else:
                    embedding = self.embed_image(image_bytes)
            else:
                raise ValueError(f"Unknown chunk type: {chunk_type}")
        except Exception as exc:
            logger.warning("Failed to embed chunk (type=%s, page=%s): %s", chunk_type, chunk.get("page"), exc)
            raise

        return {**chunk, "embedding": embedding}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _describe_image_with_nova(self, image_bytes: bytes) -> str:
        """
        Use Nova 2 Lite (multimodal) to describe an image.
        Returns a text description suitable for embedding — used as fallback
        if the image embedding call fails.
        """
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        mime = "png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            mime = "jpeg"

        body = json.dumps({
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "image": {
                                "format": mime,
                                "source": {"bytes": b64},
                            }
                        },
                        {
                            "text": (
                                "You are analyzing a page from an SEC financial filing. "
                                "Describe any charts, graphs, tables, or financial data visible "
                                "in this image. Be specific about numbers, trends, and metrics. "
                                "Focus on information relevant to financial analysis."
                            )
                        },
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": 512, "temperature": 0},
        })

        try:
            response = self.bedrock.invoke_model(
                modelId=VISION_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            description = result["output"]["message"]["content"][0]["text"]
            logger.debug("Nova Lite image description: %s...", description[:100])
            return description
        except Exception as exc:
            logger.warning("Nova Lite image description failed: %s", exc)
            return "Financial chart or visual content from SEC filing"
