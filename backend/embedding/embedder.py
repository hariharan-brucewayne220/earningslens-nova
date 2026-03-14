"""
embedder.py: Bedrock-backed multimodal embedder for EarningsLens.

Models used:
  - Text / tables: amazon.titan-embed-text-v2:0  (1536 dims)
  - Images:        amazon.titan-embed-image-v1    (1024 dims)
                   Fallback: describe with Nova 2 Lite -> embed as text
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

TEXT_MODEL = "amazon.titan-embed-text-v2:0"
IMAGE_MODEL = "amazon.titan-embed-image-v1"
VISION_MODEL = "amazon.nova-lite-v1:0"   # for describing images when image embedding unavailable


class Embedder:
    """
    Wraps Bedrock embedding models.

    Usage:
        embedder = Embedder()
        chunk_with_embedding = embedder.embed_chunk(chunk)
    """

    def __init__(self):
        self.region = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))
        self._bedrock = None
        self._image_model_available: Optional[bool] = None

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
        Embed a text string using amazon.titan-embed-text-v2:0.
        Returns a list of 1536 floats.
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        # Titan v2 truncates at ~8192 tokens; clip at 8000 chars to be safe
        text = text[:8000]

        # Titan embed-text-v2:0 accepts only inputText in the basic call
        # (dimensions/normalize params cause ValidationException on some deployments)
        body = json.dumps({"inputText": text})

        try:
            response = self.bedrock.invoke_model(
                modelId=TEXT_MODEL,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            return result["embedding"]
        except (BotoCoreError, ClientError) as exc:
            logger.error("Text embedding failed: %s", exc)
            raise

    def embed_image(self, image_bytes: bytes) -> list[float]:
        """
        Embed an image using amazon.titan-embed-image-v1 (1024 dims).
        Falls back to: describe with Nova 2 Lite -> embed the description as text.
        """
        if self._image_model_available is None:
            self._image_model_available = self._check_image_model()

        if self._image_model_available:
            try:
                return self._embed_image_native(image_bytes)
            except Exception as exc:
                logger.warning("Image model call failed, falling back to text: %s", exc)
                self._image_model_available = False

        # Fallback: describe then embed
        description = self._describe_image_with_nova(image_bytes)
        return self.embed_text(description)

    def embed_chunk(self, chunk: dict) -> dict:
        """
        Add an 'embedding' field to the chunk dict.

        Dispatches by chunk['type']:
          - text  -> embed_text(chunk['text'])
          - table -> embed_text(chunk['text_repr'])
          - image -> embed_image(chunk['image_bytes'])
        """
        chunk_type = chunk.get("type")
        try:
            if chunk_type == "text":
                embedding = self.embed_text(chunk["text"])
            elif chunk_type == "table":
                embedding = self.embed_text(chunk["text_repr"])
            elif chunk_type == "image":
                embedding = self.embed_image(chunk["image_bytes"])
            else:
                raise ValueError(f"Unknown chunk type: {chunk_type}")
        except Exception as exc:
            logger.warning("Failed to embed chunk (type=%s, page=%s): %s", chunk_type, chunk.get("page"), exc)
            raise

        return {**chunk, "embedding": embedding}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_image_model(self) -> bool:
        """Test whether the image embedding model is accessible."""
        try:
            # Use a tiny 1x1 white PNG
            tiny_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
                b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
                b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            self._embed_image_native(tiny_png)
            logger.info("Image embedding model (%s) is available", IMAGE_MODEL)
            return True
        except Exception as exc:
            logger.info("Image embedding model not available (%s), will use text fallback", exc)
            return False

    def _embed_image_native(self, image_bytes: bytes) -> list[float]:
        """Call amazon.titan-embed-image-v1 directly."""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        body = json.dumps({
            "inputImage": b64,
            "embeddingConfig": {"outputEmbeddingLength": 1024},
        })
        response = self.bedrock.invoke_model(
            modelId=IMAGE_MODEL,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        return result["embedding"]

    def _describe_image_with_nova(self, image_bytes: bytes) -> str:
        """
        Use Nova 2 Lite (multimodal) to describe an image.
        Returns a text description suitable for embedding.
        """
        b64 = base64.b64encode(image_bytes).decode("utf-8")

        # Determine MIME type (assume PNG if unknown)
        mime = "image/png"
        if image_bytes[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"

        body = json.dumps({
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "image": {
                                "format": mime.split("/")[1],
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
